"""OpenProject adapter: booked hours of the token owner vs. target hours (AIS-409).

The OpenProject counterpart of the Tempo adapter: ``pm_list_time_entries``
(pm_* contract since AIS-479) filters on the current user and the date range
server-side; the adapter pages through every entry of the range. Only per-day
totals and a per-work-package breakdown are kept, never the raw rows.

AIS-483: the tool comes from the Suite's go-mcp-openproject
(``mcp_AIMDSSuiteMCP_mcp_openproject_pm_list_time_entries``, preferred) or the
bundled OpenProjectMCP (``mcp_op_pm_list_time_entries``) — matched by the
``pm_*`` suffix, never by server name. The Suite requires ``project`` (or
``work_package_id``), so there the entries are fetched per visible project
(``pm_list_projects``); the bundled server takes the whole range in one go.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, List, Optional

from cron.brief_sources.base import BriefItem, SourceAdapter, SourceContext, SourceStatus, Window, dispatch_json, first_list
from tools.openproject_names import SUITE, openproject_kind, openproject_tools, server_for

SERVER = "OpenProjectMCP"
LIST = "pm_list_time_entries"
PROJECTS = "pm_list_projects"
PAGE_SIZE = 200  # the contract's ceiling
MAX_PAGES = 25
MAX_PROJECTS = 60  # Suite: one listing per visible project

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


class OpenProjectAdapter(SourceAdapter):
    name = "openproject"
    server = SERVER  # local default; the Suite is resolved per call (AIS-483)
    required_tools = (LIST,)
    kinds = ("morning-brief", "weekly-review")

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
        if openproject_kind(tool) == SUITE and self.resolve(ctx, PROJECTS) is None:
            return SourceStatus(self.name, "skipped", f"tool not in tools.include: {PROJECTS}")
        return None

    def _pages(self, ctx: SourceContext, tool: str, args: Dict[str, Any]) -> List[Any]:
        """Every entry of one filter set. ``offset`` is the 1-based page
        number (contract doc); the Suite's ``next_offset`` is offset + count,
        which skips pages, so the next page is always offset + 1. The Suite's
        ``has_more`` (offset + count < total) stays true past the last page,
        so there a short page ends the walk."""
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

    def _suite_projects(self, ctx: SourceContext) -> List[str]:
        data = dispatch_json(ctx, self.resolve(ctx, PROJECTS), {})
        ids: List[str] = []
        for project in first_list(data, "projects", "results"):
            if not isinstance(project, dict) or project.get("active") is False:
                continue
            ref = project.get("id") or project.get("identifier")
            if ref not in (None, "") and str(ref) not in ids:
                ids.append(str(ref))
        return ids[:MAX_PROJECTS]

    def fetch(self, window: Window, ctx: SourceContext) -> List[BriefItem]:
        tool = self.resolve(ctx, LIST)
        if window.kind == "morning-brief":
            start = end = window.last_working_day
        else:
            start, end = window.week_start, min(window.week_end, window.today)
        args = {"spent_on_from": start.isoformat(), "spent_on_to": end.isoformat(), "user": "me", "limit": PAGE_SIZE}
        entries: List[Any] = []
        if openproject_kind(tool) == SUITE:
            seen: set = set()
            for project in self._suite_projects(ctx):
                for entry in self._pages(ctx, tool, dict(args, project=project)):
                    key = entry.get("id") if isinstance(entry, dict) else None
                    if key is not None and key in seen:
                        continue
                    seen.add(key)
                    entries.append(entry)
        else:
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
            wp = entry.get("work_package_display_id") or entry.get("work_package_id") or "?"
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
