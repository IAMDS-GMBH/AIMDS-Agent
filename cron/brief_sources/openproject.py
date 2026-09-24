"""OpenProject adapter: booked hours of the token owner vs. target hours (AIS-409).

The OpenProject counterpart of the Tempo adapter: ``list_time_entries`` of the
in-repo OpenProjectMCP server (AIS-408) filters on the current user and the
date range server-side and returns every entry of the range. Only per-day
totals and a per-work-package breakdown are kept, never the raw rows.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

from cron.brief_sources.base import BriefItem, SourceAdapter, SourceContext, Window, dispatch_json, first_list, resolve_tool

SERVER = "OpenProjectMCP"
LIST = "list_time_entries"


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
        data = dispatch_json(ctx, tool, {"date_from": start.isoformat(), "date_to": end.isoformat(), "user": "me"})
        per_day: Dict[str, float] = defaultdict(float)
        per_wp: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for entry in first_list(data, "time_entries", "results"):
            if not isinstance(entry, dict):
                continue
            day = str(entry.get("spent_on") or "")[:10]
            try:
                hours = float(entry.get("hours") or 0)
            except (TypeError, ValueError):
                continue
            if not day or hours <= 0:
                continue
            per_day[day] += hours
            per_wp[day][str(entry.get("work_package_id") or "?")] += hours

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
