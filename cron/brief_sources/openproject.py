"""OpenProject adapter: booked hours of the token owner vs. target hours (AIS-409).

The OpenProject counterpart of the Tempo adapter: ``pm_list_time_entries``
(pm_* contract since AIS-479) filters on the current user and the date range
server-side; the adapter pages through every entry of the range. Only per-day
totals and a per-work-package breakdown are kept, never the raw rows.

AIS-483: the tool comes from the Suite's go-mcp-openproject
(``mcp_AIMDSSuiteMCP_mcp_openproject_pm_list_time_entries``, preferred) or the
bundled OpenProjectMCP (``mcp_op_pm_list_time_entries``) — matched by the
``pm_*`` suffix, never by server name.

AIS-711: both answer for every readable project when ``project`` is left out
(the Suite since go-mcp-openproject 0.4.0, AIS-486), so the whole range is one
filtered listing on either server. The per-project loop over
``pm_list_projects`` it replaces timed out the morning brief on the listing
alone and cost one request per project.

AIS-797: for the work feed (``workspace`` window) it also lists the user's
open work packages — one ``pm_list_work_packages`` call, assignee "me",
``open_only`` — next to today's hours.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, List, Optional

from cron.brief_sources.base import (
    BriefItem, SourceAdapter, SourceContext, SourceStatus, Window, clip, dispatch_json, first_list,
)
from tools.openproject_names import SUITE, openproject_kind, openproject_tools, server_for

SERVER = "OpenProjectMCP"
LIST = "pm_list_time_entries"
WORK_PACKAGES = "pm_list_work_packages"
PAGE_SIZE = 200  # the contract's ceiling
MAX_PAGES = 25
OPEN_LIMIT = 30  # the feed's open work packages: one page

_ISO_DURATION = re.compile(r"P(?:(\d+(?:\.\d+)?)D)?(?:T(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?")


def entry_hours(entry: Dict[str, Any]) -> float:
    """Hours of one entry: ``duration_seconds`` (bundled server), else the
    contract's ISO-8601 ``hours`` ("PT1H30M"), else a decimal."""
    seconds = entry.get("duration_seconds")
    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds > 0:
        return float(seconds) / 3600
    value = entry.get("hours")
    match = _ISO_DURATION.fullmatch(str(value or "").strip().upper())
    if match and any(match.groups()):
        d, h, m, s = (float(g) if g else 0.0 for g in match.groups())
        return d * 24 + h + m / 60 + s / 3600
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("title") or "")
    return str(value or "")


class OpenProjectAdapter(SourceAdapter):
    name = "openproject"
    server = SERVER  # local default; the Suite is resolved per call (AIS-483)
    required_tools = (LIST,)
    kinds = ("morning-brief", "weekly-review", "workspace")

    def resolve(self, ctx: SourceContext, suffix: str) -> Optional[str]:
        """The Suite's tool when both are registered, visible and connected,
        else the bundled server's (AIS-483)."""
        candidates = openproject_tools(ctx.tool_names, suffix, skip_hidden=True)
        for tool in candidates:
            status = ctx.mcp_status.get(server_for(tool)) or {}
            if status.get("connected") and not status.get("disabled"):
                return tool
        return candidates[0] if candidates else None

    def availability(self, ctx: SourceContext) -> Optional[SourceStatus]:
        tool = self.resolve(ctx, LIST)
        if tool is None:
            return super().availability(ctx) or SourceStatus(self.name, "skipped", f"tool not in tools.include: {LIST}")
        entry = ctx.mcp_status.get(server_for(tool))
        if entry is None:
            return SourceStatus(self.name, "skipped", "not configured")
        if entry.get("disabled"):
            return SourceStatus(self.name, "skipped", "disabled")
        if not entry.get("connected"):
            return SourceStatus(self.name, "skipped", "not connected")
        return None

    def _pages(self, ctx: SourceContext, tool: str, args: Dict[str, Any]) -> List[Any]:
        """Every entry of one filter set. ``offset`` is the 1-based page
        number (contract doc), so the next page is always offset + 1. Older
        Suites kept ``has_more`` true past the last page, so there a short
        page ends the walk too."""
        suite = openproject_kind(tool) == SUITE
        entries: List[Any] = []
        offset = 1
        for _ in range(MAX_PAGES):
            data = dispatch_json(ctx, tool, dict(args, offset=offset) if offset > 1 else args)
            rows = first_list(data, "time_entries", "results")
            entries.extend(rows)
            if not rows or not (isinstance(data, dict) and data.get("has_more")) or (suite and len(rows) < PAGE_SIZE):
                break
            offset += 1
        return entries

    def fetch(self, window: Window, ctx: SourceContext) -> List[BriefItem]:
        if window.kind != "workspace":
            return self._worklogs(window, ctx)
        items: List[BriefItem] = []
        errors: List[str] = []
        for label, part in (("work packages", self._open_work_packages), ("time entries", self._worklogs)):
            try:
                items += part(window, ctx)
            except Exception as exc:
                errors.append(f"{label}: {clip(exc, 60)}")
        if len(errors) == 2:
            raise RuntimeError("; ".join(errors))
        if errors:
            self.partial_errors = errors  # surfaced by the collector in the sources line
        return items

    def _open_work_packages(self, window: Window, ctx: SourceContext) -> List[BriefItem]:
        tool = self.resolve(ctx, WORK_PACKAGES)
        if tool is None:
            return []
        data = dispatch_json(ctx, tool, {"assignee": "me", "open_only": True, "limit": OPEN_LIMIT})
        items: List[BriefItem] = []
        for raw in first_list(data, "work_packages", "results"):
            if not isinstance(raw, dict):
                continue
            key = str(raw.get("key") or raw.get("display_id") or raw.get("id") or "")
            if not key:
                continue
            items.append(BriefItem(
                source="openproject", kind="ticket", key=key, title=clip(raw.get("subject"), 90),
                status=_name(raw.get("status")), priority=_name(raw.get("priority")),
                due_at=str(raw.get("due_date") or raw.get("date") or "")[:10],
                updated_at=str(raw.get("updated_at") or ""), url=str(raw.get("url") or ""),
                extra={"project": clip(_name(raw.get("project")), 40), "type": _name(raw.get("type"))},
            ))
        return items

    def _worklogs(self, window: Window, ctx: SourceContext) -> List[BriefItem]:
        tool = self.resolve(ctx, LIST)
        if window.kind == "workspace":
            start = end = window.today
        elif window.kind == "morning-brief":
            start = end = window.last_working_day
        else:
            start, end = window.week_start, min(window.week_end, window.today)
        # One listing across every readable project (no ``project``): the
        # brief needs per-day and per-work-package hours of the user's own
        # entries, which a single page of a day or week usually holds.
        args = {"spent_on_from": start.isoformat(), "spent_on_to": end.isoformat(), "user": "me", "limit": PAGE_SIZE}
        entries = self._pages(ctx, tool, args)
        per_day: Dict[str, float] = defaultdict(float)
        per_wp: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            day = str(entry.get("spent_on") or "")[:10]
            hours = entry_hours(entry)
            if not day or hours <= 0:
                continue
            per_day[day] += hours
            # AIS-504: go-mcp-openproject 0.5 names work packages by key; the
            # bundled server's older display-id field still counts.
            wp = (entry.get("work_package_key") or entry.get("work_package_display_id")
                  or entry.get("work_package_id") or "?")
            per_wp[day][str(wp)] += hours

        items: List[BriefItem] = []
        cur = start
        while cur <= end:
            day = cur.isoformat()
            if cur.weekday() < 5 and cur not in window.holidays:
                logged = round(per_day.get(day, 0.0), 2)
                target = ctx.store.target_hours(day) if ctx.store else None
                items.append(BriefItem(
                    source="openproject", kind="worklog", key=day, title=f"{logged:g} h logged",
                    status="complete" if (target is not None and logged >= target - 0.01) else ("missing" if logged == 0 else "partial"),
                    starts_at=day,
                    extra={"hours": logged, "target_hours": target,
                           "by_work_package": {k: round(v, 2) for k, v in sorted(per_wp.get(day, {}).items())}},
                ))
            cur = cur.fromordinal(cur.toordinal() + 1)
        return items
