"""LLM-free brief collector for the shipped cron jobs (AIS-305).

Runs inside the scheduler before the agent is built: detects which MCP
sources are usable, fetches bounded, user-scoped data through the source
adapters, persists normalised items in ``state.db`` (``cron.brief_store``)
and renders a compact ``## Collected Data`` block the model composes from.
The agent then runs with **no tools** — one or two API calls instead of
seventeen.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from cron.brief_sources.base import (
    BriefItem, SourceAdapter, SourceContext, SourceStatus, Window, clip, parse_dt, to_local,
)

logger = logging.getLogger(__name__)

COLLECTOR_FIELD = "collector"
KINDS = ("morning-brief", "weekly-review", "mail-check", "teams-check")
_SEED_KEY_TO_KIND = {
    "morning-brief": "morning-brief",
    "weekly-review": "weekly-review",
    "weekly-digest": "weekly-review",
    "m365-mail-check": "mail-check",
    "m365-teams-check": "teams-check",
}
DEFAULT_CFG = {
    "enabled": True,
    "language": "",
    "char_budget": 5000,
    "mail_top": 15,
    "source_timeout_seconds": 45,
    "compose_max_iterations": 3,
}

# Deterministic headings so the journal title and the "preview" section can be
# found again by the workspace adapter regardless of what the model wrote.
HEADINGS = {
    "en": {
        "morning-brief": "Morning Brief", "weekly-review": "Weekly Review",
        "mail-check": "Mail Check", "teams-check": "Teams Check",
        "preview": "Preview: next working day", "changed": "Changed since the last brief",
        "latest": "Latest briefs",
    },
    "de": {
        "morning-brief": "Tages-Briefing", "weekly-review": "Wochen-Rückblick",
        "mail-check": "Mail-Check", "teams-check": "Teams-Check",
        "preview": "Vorschau: nächster Arbeitstag", "changed": "Seit dem letzten Briefing geändert",
        "latest": "Letzte Briefings",
    },
}


# ----------------------------------------------------------------------
# config helpers
# ----------------------------------------------------------------------
def collector_config(cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    raw = (((cfg or {}).get("cron") or {}).get("brief_collector") or {}) if isinstance(cfg, dict) else {}
    merged = dict(DEFAULT_CFG)
    if isinstance(raw, dict):
        merged.update({k: v for k, v in raw.items() if v is not None})
    return merged


def brief_language(cfg: Optional[Dict[str, Any]]) -> str:
    """Output language of scheduled runs: ``cron.brief_collector.language`` → ``display.language`` → ``en``.

    Any language ``agent.i18n`` supports is accepted (codes, aliases such as
    ``Deutsch``, region tags such as ``de-AT``); unknown values normalise to
    ``en``.  Journal titles (``HEADINGS``) exist in en/de only and fall back to
    English for other languages — the LANGUAGE directive still applies.
    """
    from agent.i18n import normalize_language

    raw = collector_config(cfg).get("language")
    if isinstance(raw, str) and raw.strip():
        return normalize_language(raw)
    display = (cfg or {}).get("display") if isinstance(cfg, dict) else None
    raw = display.get("language") if isinstance(display, dict) else None
    if isinstance(raw, str) and raw.strip():
        return normalize_language(raw)
    return "en"


def _canonical_seed_key(raw: Any) -> str:
    key = str(raw or "").strip().lower().replace("_", "-")
    return "weekly-review" if key == "weekly-digest" else key


def resolve_collector_kind(job: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Explicit ``job["collector"]`` wins (``false``/``"none"`` opts out); else AIMDS defaults by seed key."""
    if not collector_config(cfg).get("enabled", True):
        return None
    explicit = job.get(COLLECTOR_FIELD)
    if explicit is not None:
        if explicit is False or str(explicit).strip().lower() in ("", "false", "none", "off", "0"):
            return None
        kind = str(explicit).strip().lower()
        return kind if kind in KINDS else None
    origin = job.get("origin")
    if isinstance(origin, dict) and origin.get("source") == "aimds-default-cron":
        return _SEED_KEY_TO_KIND.get(_canonical_seed_key(origin.get("seed_key")))
    return None


# ----------------------------------------------------------------------
# window
# ----------------------------------------------------------------------
def _holidays(now: datetime, cfg: Optional[Dict[str, Any]]) -> Dict[date, str]:
    try:
        from hermes_time import get_calendar_context, get_public_holidays

        ctx = get_calendar_context(now)
        region = ctx.get("region")
        state = ctx.get("state") or "BW"
        out: Dict[date, str] = {}
        for year in (now.year, now.year + 1):
            out.update(get_public_holidays(year, state, region=region) or {})
        return out
    except Exception as exc:
        logger.debug("holiday lookup failed: %s", exc)
        return {}


def _is_working_day(d: date, holidays: Dict[date, str]) -> bool:
    return d.weekday() < 5 and d not in holidays


def next_working_day(d: date, holidays: Dict[date, str]) -> date:
    cur = d + timedelta(days=1)
    while not _is_working_day(cur, holidays):
        cur += timedelta(days=1)
    return cur


def last_working_day(d: date, holidays: Dict[date, str]) -> date:
    cur = d - timedelta(days=1)
    while not _is_working_day(cur, holidays):
        cur -= timedelta(days=1)
    return cur


def build_window(kind: str, now: datetime, holidays: Optional[Dict[date, str]] = None) -> Window:
    holidays = holidays or {}
    today = now.date()
    tz = now.tzinfo
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=4)
    if kind == "weekly-review":
        preview = next_working_day(week_end, holidays)
        start_day, end_day = week_start, week_end
    else:
        # On a weekend/holiday "today" has no work items; the preview day carries the brief.
        preview = next_working_day(today, holidays)
        start_day, end_day = today, preview
    return Window(
        kind=kind, now=now, today=today, preview_day=preview,
        last_working_day=last_working_day(today, holidays),
        week_start=week_start, week_end=week_end,
        start=datetime.combine(start_day, dtime.min, tzinfo=tz),
        end=datetime.combine(end_day, dtime(23, 59, 59), tzinfo=tz),
        holidays=holidays,
    )


# ----------------------------------------------------------------------
# result
# ----------------------------------------------------------------------
@dataclass
class CollectorResult:
    kind: str
    text: str
    sources: List[SourceStatus]
    window: Window
    lang: str
    has_new: bool = True
    items: List[BriefItem] = field(default_factory=list)
    pending_items: List[BriefItem] = field(default_factory=list)  # committed after a successful LLM run
    counts: Dict[str, int] = field(default_factory=dict)
    elapsed_s: float = 0.0

    @property
    def compose_only(self) -> bool:
        return True

    def sources_line(self) -> str:
        return "Sources: " + " | ".join(s.render() for s in self.sources)

    def commit(self, store: Any, seen_at: Optional[datetime] = None) -> None:
        """Persist watermark items (mail/teams) after the brief was delivered."""
        if not self.pending_items or store is None:
            return
        store.upsert_items(self.pending_items, seen_at or self.window.now)
        self.pending_items = []


# ----------------------------------------------------------------------
# dispatch with timeout
# ----------------------------------------------------------------------
def _dispatch_with_timeout(timeout_s: float) -> Callable[[str, Dict[str, Any]], Any]:
    def _call(tool: str, args: Dict[str, Any]) -> Any:
        from tools.registry import registry

        q: "queue.Queue[tuple]" = queue.Queue(maxsize=1)

        def _worker() -> None:
            try:
                q.put(("ok", registry.dispatch(tool, args)))
            except Exception as exc:  # pragma: no cover - surfaced below
                q.put(("err", exc))

        threading.Thread(target=_worker, name=f"brief-collector:{tool}", daemon=True).start()
        try:
            status, payload = q.get(timeout=timeout_s)
        except queue.Empty:
            raise TimeoutError(f"{tool} timed out after {timeout_s:g}s")
        if status == "err":
            raise payload
        return payload

    return _call


# ----------------------------------------------------------------------
# collect
# ----------------------------------------------------------------------
def collect(
    job: Dict[str, Any],
    kind: str,
    *,
    now: Optional[datetime] = None,
    cfg: Optional[Dict[str, Any]] = None,
    dispatch: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
    store: Any = None,
    adapters: Optional[Sequence[SourceAdapter]] = None,
    mcp_status: Optional[List[Dict[str, Any]]] = None,
    tool_names: Optional[Sequence[str]] = None,
    vault_root: Optional[Path] = None,
    holidays: Optional[Dict[date, str]] = None,
) -> CollectorResult:
    """Gather everything for ``kind``; never raises — sources fail individually."""
    t0 = time.monotonic()
    cfg = cfg or {}
    ccfg = collector_config(cfg)
    if now is None:
        from hermes_time import now as _hermes_now

        now = _hermes_now()
    lang = brief_language(cfg)
    window = build_window(kind, now, holidays if holidays is not None else _holidays(now, cfg))

    if mcp_status is None:
        try:
            from tools.mcp_tool import get_mcp_status

            mcp_status = get_mcp_status()
        except Exception as exc:
            logger.debug("get_mcp_status failed: %s", exc)
            mcp_status = []
    status_map = {str(e.get("name")): e for e in (mcp_status or []) if isinstance(e, dict)}
    if tool_names is None:
        try:
            from tools.registry import registry

            tool_names = registry.get_all_tool_names()
        except Exception:
            tool_names = []
    mcp_config: Dict[str, Dict[str, Any]] = {}
    try:
        from tools.mcp_tool import _load_mcp_config

        mcp_config = _load_mcp_config() or {}
    except Exception:
        pass
    if vault_root is None:
        try:
            from agent.runtime_cwd import resolve_agent_cwd

            vault_root = resolve_agent_cwd().expanduser().resolve()
        except Exception:
            vault_root = None
    own_store = False
    if store is None:
        try:
            from cron.brief_store import BriefStore

            store = BriefStore()
            own_store = True
        except Exception as exc:
            logger.warning("brief store unavailable: %s", exc)
            store = None

    ctx = SourceContext(
        tool_names=set(tool_names or []), mcp_status=status_map,
        dispatch=dispatch or _dispatch_with_timeout(float(ccfg.get("source_timeout_seconds") or 45)),
        cfg=cfg, vault_root=vault_root, store=store, lang=lang, mcp_config=mcp_config,
    )

    if adapters is None:
        from cron.brief_sources import default_adapters

        adapters = default_adapters()

    statuses: List[SourceStatus] = []
    items: List[BriefItem] = []
    for adapter in adapters:
        if adapter.kinds and kind not in adapter.kinds:
            continue
        if kind in ("mail-check", "teams-check") and adapter.name != "m365":
            continue
        t1 = time.monotonic()
        skip = adapter.availability(ctx)
        if skip is not None:
            statuses.append(skip)
            logger.info("[AIS-161] collector source=%s status=%s detail=%s", adapter.name, skip.status, skip.detail)
            continue
        try:
            got = adapter.fetch(window, ctx) or []
            errors = getattr(adapter, "partial_errors", None)
            detail = str(len(got))
            if errors:
                detail += ", " + "; ".join(errors)[:80]
            statuses.append(SourceStatus(adapter.name, "active", detail, len(got)))
            items.extend(got)
            logger.info("[AIS-161] collector source=%s status=active count=%d ms=%d",
                        adapter.name, len(got), int((time.monotonic() - t1) * 1000))
        except Exception as exc:
            statuses.append(SourceStatus(adapter.name, "failed", clip(exc, 80)))
            logger.warning("[AIS-161] collector source=%s status=failed error=%s", adapter.name, clip(exc, 200))
        finally:
            if hasattr(adapter, "partial_errors"):
                try:
                    delattr(adapter, "partial_errors")
                except Exception:
                    pass

    result = CollectorResult(kind=kind, text="", sources=statuses, window=window, lang=lang)
    result.items = items
    result.counts = _counts(items)

    if kind in ("mail-check", "teams-check"):
        wanted = "mail" if kind == "mail-check" else "chat"
        candidates = [i for i in items if i.kind == wanted]
        existing = store.existing_ids(i.id for i in candidates) if store else set()
        new_items = [i for i in candidates if i.id not in existing]
        first_run = store is not None and store.last_run_at(job.get("id", "")) is None
        if first_run and len(new_items) > 15:
            # baseline: everything currently unread is "known" except the newest 15
            new_items = sorted(new_items, key=lambda i: i.updated_at, reverse=True)[:15]
            result.pending_items = candidates  # commit all as seen after the run
        else:
            result.pending_items = new_items
        result.has_new = bool(new_items)
        result.items = new_items
        result.text = _render_poll(kind, new_items, window, result) if new_items else ""
    else:
        changed: List[BriefItem] = []
        if store is not None:
            try:
                res = store.upsert_items(items, now)
                since = store.last_run_at(job.get("id", "")) or (now - timedelta(days=1))
                changed = [i for i in store.changed_since(since) if i.kind in ("ticket", "event", "task")]
                # refresh in-memory prev_status for items changed this run
                changed_ids = {i.id for i in changed}
                for item, prev in res.get("changed", []):
                    if item.id not in changed_ids:
                        item.prev_status = prev
                        changed.append(item)
            except Exception as exc:
                logger.warning("brief store upsert failed: %s", exc)
        result.text = _render_brief(kind, items, changed, window, lang, result, int(ccfg.get("char_budget") or 5000))

    if store is not None:
        try:
            store.record_run(job.get("id", ""), kind, result.sources_line(), result.counts, now,
                             session_id=str(job.get("_session_id") or ""))
        except Exception as exc:
            logger.debug("brief store record_run failed: %s", exc)
        if own_store and not result.pending_items:
            store.close()
    result.elapsed_s = round(time.monotonic() - t0, 2)
    return result


def _counts(items: Sequence[BriefItem]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for i in items:
        out[i.kind] = out.get(i.kind, 0) + 1
    return out


# ----------------------------------------------------------------------
# rendering
# ----------------------------------------------------------------------
def _day_label(d: date) -> str:
    return f"{d.isoformat()} ({d.strftime('%A')})"


def _fmt_event(i: BriefItem, tz) -> str:
    start = to_local(parse_dt(i.starts_at, tz), tz)
    end = to_local(parse_dt(i.ends_at, tz), tz)
    when = "all day" if i.extra.get("all_day") else f"{start.strftime('%H:%M') if start else '??:??'}–{end.strftime('%H:%M') if end else '??:??'}"
    loc = f" ({i.extra['location']})" if i.extra.get("location") else ""
    org = f" [{i.extra['organizer']}]" if i.extra.get("organizer") else ""
    return f"- {when}  {i.title}{loc}{org}"


def _fmt_ticket(i: BriefItem) -> str:
    due = f", due {i.due_at}" if i.due_at else ""
    prio = f", {i.priority}" if i.priority else ""
    return f"- {i.key} {i.title} [{i.status}{prio}{due}]"


def _fmt_mail(i: BriefItem, tz) -> str:
    when = to_local(parse_dt(i.updated_at, tz), tz)
    stamp = when.strftime("%d.%m %H:%M") if when else ""
    att = " 📎" if i.extra.get("has_attachments") else ""
    return f"- {stamp} | {i.extra.get('from', '')} | {i.title}{att}"


def _fmt_chat(i: BriefItem, tz) -> str:
    when = to_local(parse_dt(i.updated_at, tz), tz)
    stamp = when.strftime("%d.%m %H:%M") if when else ""
    return f"- {i.extra.get('topic', '')} — {i.extra.get('from', '')} @{stamp}: {i.title}"


def _fmt_task(i: BriefItem) -> str:
    due = f" (due {i.due_at})" if i.due_at else ""
    src = f" [{i.extra.get('list') or i.extra.get('file') or i.source}]"
    return f"- {i.title}{due}{src}"


def _fmt_worklog(i: BriefItem) -> str:
    target = i.extra.get("target_hours")
    tgt = f" of {target:g} h target" if isinstance(target, (int, float)) else ""
    issues = i.extra.get("by_issue") or {}
    detail = ", ".join(f"{k} {v:g}h" for k, v in list(issues.items())[:6])
    return f"- {i.key}: {i.extra.get('hours', 0):g} h logged{tgt} ({i.status}){' — ' + detail if detail else ''}"


def _section(title: str, lines: List[str], cap: int) -> str:
    if not lines:
        return f"### {title}\n(none)\n"
    shown = lines[:cap]
    more = len(lines) - len(shown)
    body = "\n".join(shown)
    if more > 0:
        body += f"\n… (+{more} more)"
    return f"### {title}\n{body}\n"


def _render_brief(kind: str, items: Sequence[BriefItem], changed: Sequence[BriefItem], window: Window,
                  lang: str, result: CollectorResult, budget: int) -> str:
    tz = window.tz
    by_kind: Dict[str, List[BriefItem]] = {}
    for i in items:
        by_kind.setdefault(i.kind, []).append(i)

    events = sorted(by_kind.get("event", []), key=lambda i: i.starts_at or "")
    events_today = [i for i in events if (parse_dt(i.starts_at, tz) or window.now).date() == window.today]
    events_preview = [i for i in events if (parse_dt(i.starts_at, tz) or window.now).date() != window.today]
    tickets = [i for i in by_kind.get("ticket", []) if not i.extra.get("refreshed")]
    todos = [i for i in by_kind.get("task", []) if i.source == "m365"]
    ws_tasks = [i for i in by_kind.get("task", []) if i.source == "workspace"]
    mails = by_kind.get("mail", [])
    chats = by_kind.get("chat", [])
    worklogs = by_kind.get("worklog", [])
    notes = by_kind.get("note", [])
    open_q = [n for n in notes if n.key.startswith("open-question:")]
    findings = [n for n in notes if n.key.startswith("finding:")]
    prev = [n for n in notes if n.key.startswith("prev-preview:")]

    header = (
        f"## Collected Data (LLM-free collector, {window.now.strftime('%Y-%m-%d %H:%M %Z').strip()})\n"
        f"Window: today={_day_label(window.today)} | preview={_day_label(window.preview_day)}"
        + (f" | week={window.week_start.isoformat()}..{window.week_end.isoformat()}" if kind == "weekly-review" else "")
        + (f" | holiday today: {window.holidays[window.today]}" if window.today in window.holidays else "")
        + "\n"
    )
    sections: List[str] = []
    cal_title = "Calendar (this week)" if kind == "weekly-review" else "Calendar today"
    sections.append(_section(cal_title, [_fmt_event(i, tz) for i in events_today], 12))
    sections.append(_section(f"Calendar {window.preview_day.isoformat()} (preview)" if kind != "weekly-review" else "Calendar next working day",
                             [_fmt_event(i, tz) for i in events_preview], 8))
    sections.append(_section("My tickets (Jira)", [_fmt_ticket(i) for i in tickets], 12))
    sections.append(_section("To Do", [_fmt_task(i) for i in todos + ws_tasks], 12))
    sections.append(_section("Unread mail", [_fmt_mail(i, tz) for i in mails], 15))
    sections.append(_section("Teams", [_fmt_chat(i, tz) for i in chats], 10))
    sections.append(_section("Time tracking", [_fmt_worklog(i) for i in worklogs], 6))
    changed_lines = [f"- {i.key or i.title}: {i.prev_status or '?'} → {i.status} ({i.kind})" for i in changed]
    sections.append(_section("Changed since the last brief", changed_lines, 10))
    ws_lines = [f"- open question: {n.title}" for n in open_q] + [f"- finding: {n.title}" for n in findings]
    if prev:
        ws_lines.append(f"- previous brief preview: {clip(prev[0].title, 600)}")
    sections.append(_section("Workspace", ws_lines, 12))

    text = header + "\n" + "\n".join(sections) + "\n" + result.sources_line() + "\n"
    return _fit(header, sections, result.sources_line(), budget) if len(text) > budget else text


def _fit(header: str, sections: List[str], footer: str, budget: int) -> str:
    """Trim from the bottom (workspace first, calendar last) until under budget."""
    fixed = len(header) + len(footer) + 4
    sections = list(sections)
    idx = len(sections) - 1
    while idx >= 0 and fixed + sum(len(s) for s in sections) > budget:
        s = sections[idx]
        lines = s.splitlines()
        if len(lines) > 3:
            title = lines[0]
            keep = lines[1:3]
            dropped = len(lines) - 3
            sections[idx] = f"{title}\n" + "\n".join(keep) + f"\n… (+{dropped} more)\n"
        else:
            idx -= 1
    text = header + "\n" + "\n".join(sections) + "\n" + footer + "\n"
    if len(text) > budget:
        text = text[: budget - 20].rstrip() + "\n… (truncated)\n"
    return text


def _render_poll(kind: str, new_items: Sequence[BriefItem], window: Window, result: CollectorResult) -> str:
    tz = window.tz
    title = "New unread mail" if kind == "mail-check" else "New Teams messages"
    lines = [(_fmt_mail(i, tz) if i.kind == "mail" else _fmt_chat(i, tz)) for i in new_items]
    return (
        f"## Collected Data (LLM-free collector, {window.now.strftime('%Y-%m-%d %H:%M')})\n\n"
        + _section(title, lines, 20)
        + "\n" + result.sources_line() + "\n"
    )


# ----------------------------------------------------------------------
# journal helpers (used by the scheduler)
# ----------------------------------------------------------------------
def brief_title(kind: str, lang: str, day: date) -> str:
    h = HEADINGS.get(lang, HEADINGS["en"])
    if kind == "weekly-review":
        return f"{h[kind]} {day.isocalendar()[0]}-W{day.isocalendar()[1]:02d}"
    return f"{h.get(kind, kind)} {day.isoformat()}"


def language_instruction(lang: str) -> str:
    """One-line LANGUAGE directive for every scheduled run (all cron jobs, not only briefs).

    Wording must stay clear of the cron prompt-injection scanner's strict
    patterns (no "ignore … instructions", "disregard", "system prompt override")
    and must keep the scheduler's literal markers untranslated.
    """
    from agent.i18n import LANGUAGE_NAMES, normalize_language

    name = LANGUAGE_NAMES.get(normalize_language(lang), "English")
    return (
        f"LANGUAGE: Write your entire response in {name}, including headings, list items and summaries; "
        "do not mix languages. Keep proper nouns, ticket keys, e-mail subjects and quoted titles as they are, "
        "and keep the literal markers FINDING:, NEXT:, OPEN_QUESTION:, OPEN_QUESTION_NEEDED: and [SILENT] "
        f"exactly as written. This is a scheduled run with no user message to mirror; {name} is the user's "
        "configured language."
    )
