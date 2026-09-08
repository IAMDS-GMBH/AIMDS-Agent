"""Shared types for brief source adapters (AIS-305)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

ITEM_KINDS = ("event", "mail", "task", "ticket", "chat", "worklog", "note")


@dataclass
class BriefItem:
    source: str
    kind: str
    key: str
    title: str = ""
    status: str = ""
    priority: str = ""
    starts_at: str = ""
    ends_at: str = ""
    due_at: str = ""
    updated_at: str = ""
    url: str = ""
    mine: bool = True
    extra: Dict[str, Any] = field(default_factory=dict)
    # populated when read back from the store
    prev_status: Optional[str] = None
    status_changed_at: Optional[str] = None
    first_seen_at: Optional[str] = None

    @property
    def id(self) -> str:
        return f"{self.source}:{self.kind}:{self.key}"


@dataclass
class SourceStatus:
    name: str
    status: str  # active | skipped | failed
    detail: str = ""
    count: int = 0

    def render(self) -> str:
        if self.status == "active":
            return f"{self.name}=active({self.detail or self.count})"
        return f"{self.name}={self.status}({self.detail})"


@dataclass
class Window:
    kind: str
    now: datetime
    today: date
    preview_day: date
    last_working_day: date
    week_start: date
    week_end: date
    start: datetime
    end: datetime
    holidays: Dict[date, str] = field(default_factory=dict)

    @property
    def tz(self) -> Optional[tzinfo]:
        return self.now.tzinfo

    def contains_day(self, d: Optional[date]) -> bool:
        return d is not None and self.start.date() <= d <= self.end.date()


@dataclass
class SourceContext:
    tool_names: set
    mcp_status: Dict[str, Dict[str, Any]]
    dispatch: Callable[[str, Dict[str, Any]], Any]
    cfg: Dict[str, Any]
    vault_root: Optional[Path]
    store: Any  # cron.brief_store.BriefStore
    lang: str = "en"
    mcp_config: Dict[str, Dict[str, Any]] = field(default_factory=dict)


class SourceAdapter:
    """Base class: subclasses set ``name``/``server``/``required_tools`` and implement ``fetch``."""

    name: str = ""
    server: Optional[str] = None
    required_tools: Sequence[str] = ()
    kinds: Sequence[str] = ()  # which collector kinds use this adapter (empty = all)

    def availability(self, ctx: SourceContext) -> Optional[SourceStatus]:
        """Return a ``skipped`` status when the source cannot be used, else None."""
        if not self.server:
            return None
        entry = ctx.mcp_status.get(self.server)
        if entry is None:
            return SourceStatus(self.name, "skipped", "not configured")
        if entry.get("disabled"):
            return SourceStatus(self.name, "skipped", "disabled")
        if not entry.get("connected"):
            return SourceStatus(self.name, "skipped", "not connected")
        missing = [t for t in self.required_tools if resolve_tool(ctx.tool_names, self.server, t) is None]
        if missing:
            return SourceStatus(self.name, "skipped", f"tool not in tools.include: {missing[0]}")
        return None

    def fetch(self, window: Window, ctx: SourceContext) -> List[BriefItem]:  # pragma: no cover - abstract
        raise NotImplementedError


# ----------------------------------------------------------------------
# helpers shared by adapters
# ----------------------------------------------------------------------
def resolve_tool(tool_names: Iterable[str], server: str, suffix: str) -> Optional[str]:
    """Registered name for ``suffix`` on ``server`` (``mcp_<server>_<suffix>``), tolerant of sanitising."""
    wanted = suffix.lower()
    server_l = server.lower()
    for name in tool_names:
        low = name.lower()
        if not low.startswith("mcp_"):
            continue
        if low.endswith("_" + wanted) and server_l in low:
            return name
    return None


def dispatch_json(ctx: SourceContext, tool: str, args: Dict[str, Any]) -> Any:
    """Call a registered tool and decode its JSON text; raises on error payloads."""
    raw = ctx.dispatch(tool, args)
    if isinstance(raw, (dict, list)):
        data = raw
    else:
        text = str(raw or "").strip()
        if not text:
            raise RuntimeError("empty result")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # some servers wrap JSON in prose — take the outermost object
            m = re.search(r"[\[{].*[\]}]", text, re.S)
            if not m:
                raise RuntimeError(f"non-JSON result: {text[:80]!r}")
            data = json.loads(m.group(0))
    data = unwrap_result(data)
    if isinstance(data, dict) and data.get("error") and len(data) <= 3:
        raise RuntimeError(str(data.get("error"))[:120])
    return data


def unwrap_result(data: Any, depth: int = 5) -> Any:
    """Peel the registry/MCP envelopes: ``{"result": {"result": <payload>}}`` where the
    innermost payload may itself be a JSON string (mcp-atlassian returns text)."""
    for _ in range(depth):
        if isinstance(data, str):
            text = data.strip()
            if text[:1] in "[{":
                try:
                    data = json.loads(text)
                    continue
                except json.JSONDecodeError:
                    return data
            return data
        if isinstance(data, dict) and "result" in data and len(data) <= 2 and not data.get("error"):
            data = data["result"]
            continue
        break
    return data


def first_list(payload: Any, *keys: str) -> List[Any]:
    """Locate the item list in an MCP payload (top-level list or first matching key)."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in keys:
            v = payload.get(k)
            if isinstance(v, list):
                return v
        for v in payload.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return []


_DT_RE = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})(?:[T ](?P<h>\d{2}):(?P<m>\d{2})(?::(?P<s>\d{2}))?(?:\.\d+)?)?"
    r"\s*(?P<tz>Z|[+-]\d{2}:?\d{2})?(?:\s*\((?P<zone>[A-Za-z_]+/[A-Za-z_]+)\))?"
)


def parse_dt(value: Any, tz: Optional[tzinfo] = None) -> Optional[datetime]:
    """Parse Graph/Jira/Tempo timestamps in any of their shapes.

    Accepts Graph dicts (``{"dateTime": ..., "timeZone": ...}``, 7-digit
    fractions), ISO strings with ``Z``/offsets, Jira's ``2026-09-08 09:20:48 CEST``,
    and the M365 server's local form ``2026-09-08 15:00:00 (Europe/Berlin)``.
    Naive values are assumed in ``tz`` (or the dict's ``timeZone``).
    """
    if value is None:
        return None
    zone_hint: Optional[str] = None
    if isinstance(value, dict):  # Graph {"dateTime": ..., "timeZone": ...}
        zone_hint = value.get("timeZone") or value.get("time_zone")
        value = value.get("dateTime") or value.get("date_time") or value.get("date")
    if isinstance(value, datetime):
        return value if value.tzinfo else (value.replace(tzinfo=tz) if tz else value)
    text = str(value or "").strip()
    m = _DT_RE.search(text)
    if not m:
        return None
    try:
        dt = datetime.fromisoformat(m.group("date"))
    except ValueError:
        return None
    if m.group("h"):
        dt = dt.replace(hour=int(m.group("h")), minute=int(m.group("m")), second=int(m.group("s") or 0))
    off = m.group("tz")
    zone = m.group("zone") or zone_hint
    if off:
        if off == "Z":
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            sign = 1 if off[0] == "+" else -1
            digits = off[1:].replace(":", "")
            dt = dt.replace(tzinfo=timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:4]))))
    elif zone and zone.upper() != "UTC":
        try:
            from zoneinfo import ZoneInfo

            dt = dt.replace(tzinfo=ZoneInfo(str(zone)))
        except Exception:
            dt = dt.replace(tzinfo=tz) if tz else dt
    elif zone and zone.upper() == "UTC":
        dt = dt.replace(tzinfo=timezone.utc)
    elif tz is not None:
        dt = dt.replace(tzinfo=tz)
    return dt


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: Any) -> str:
    """Drop tags and collapse whitespace (Teams bodies arrive as HTML)."""
    import html as _html

    s = _HTML_TAG_RE.sub(" ", str(text or ""))
    s = re.sub(r"<[^>]*$", " ", s)  # tag cut off by an upstream preview limit
    return " ".join(_html.unescape(s).split())


def to_local(dt: Optional[datetime], tz: Optional[tzinfo]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.astimezone(tz) if (tz and dt.tzinfo) else dt


def iso_day(dt: Optional[datetime]) -> str:
    return dt.strftime("%Y-%m-%d") if dt else ""


def hhmm(dt: Optional[datetime]) -> str:
    return dt.strftime("%H:%M") if dt else ""


def clip(text: Any, n: int) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[: max(0, n - 1)] + "…"


def add_days(d: date, n: int) -> date:
    return d + timedelta(days=n)
