"""OpenProject adapter: booked hours of the token owner vs. target hours (AIS-409).

The OpenProject counterpart of the Tempo adapter: ``pm_list_time_entries`` of
the bundled OpenProjectMCP server (pm_* contract since AIS-479) filters on the
current user and the date range server-side; the adapter pages through every
entry of the range (``next_offset``). Only per-day totals and a
per-work-package breakdown are kept, never the raw rows.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, List

from cron.brief_sources.base import BriefItem, SourceAdapter, SourceContext, Window, dispatch_json, first_list, resolve_tool

SERVER = "OpenProjectMCP"
LIST = "pm_list_time_entries"
PAGE_SIZE = 200  # the contract's ceiling
MAX_PAGES = 25

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
    server = SERVER
    required_tools = (LIST,)
    kinds = ("morning-brief", "weekly-review")

    def fetch(self, window: Window, ctx: SourceContext) -> List[BriefItem]:
        tool = resolve_tool(ctx.tool_names, SERVER, LIST)
        if window.kind == "morning-brief":
            start = end = window.last_working_day
        else:
            start, end = window.week_start, min(window.week_end, window.today)
        args = {"spent_on_from": start.isoformat(), "spent_on_to": end.isoformat(), "user": "me", "limit": PAGE_SIZE}
        entries: List[Any] = []
        offset = 1
        for _ in range(MAX_PAGES):
            data = dispatch_json(ctx, tool, dict(args, offset=offset) if offset > 1 else args)
            entries.extend(first_list(data, "time_entries", "results"))
            next_offset = data.get("next_offset") if isinstance(data, dict) and data.get("has_more") else None
            if not isinstance(next_offset, int) or next_offset <= offset:
                break
            offset = next_offset
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
