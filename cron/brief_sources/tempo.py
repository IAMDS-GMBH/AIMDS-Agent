"""Tempo adapter: logged hours of the token owner vs. target hours (AIS-305).

``retrieveWorklogs`` already defaults to the authenticated user; we only keep
per-day totals and a per-issue breakdown — never the raw worklog rows.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

from cron.brief_sources.base import (
    BriefItem, SourceAdapter, SourceContext, Window, dispatch_json, first_list, iso_day, parse_dt, resolve_tool,
)

SERVER = "TempoMCP"
RETRIEVE = "retrieveWorklogs"


class TempoAdapter(SourceAdapter):
    name = "tempo"
    server = SERVER
    required_tools = (RETRIEVE,)
    kinds = ("morning-brief", "weekly-review")

    def fetch(self, window: Window, ctx: SourceContext) -> List[BriefItem]:
        tool = resolve_tool(ctx.tool_names, SERVER, RETRIEVE)
        if window.kind == "morning-brief":
            start = end = window.last_working_day
        else:
            start, end = window.week_start, min(window.week_end, window.today)
        data = dispatch_json(ctx, tool, {"startDate": start.isoformat(), "endDate": end.isoformat()})
        rows = first_list(data, "worklogs", "results", "value")
        per_day: Dict[str, float] = defaultdict(float)
        per_issue: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for w in rows:
            if not isinstance(w, dict):
                continue
            secs = w.get("timeSpentSeconds") or w.get("time_spent_seconds") or 0
            try:
                hours = float(secs) / 3600.0
            except (TypeError, ValueError):
                continue
            day = str(w.get("startDate") or w.get("start_date") or w.get("date") or "")[:10]
            if not day:
                dt = parse_dt(w.get("started") or w.get("startDateTimeUtc"), window.tz)
                day = iso_day(dt)
            if not day:
                continue
            issue = w.get("issue")
            issue_key = issue.get("key") if isinstance(issue, dict) else (w.get("issueKey") or w.get("issue_key") or "")
            per_day[day] += hours
            per_issue[day][str(issue_key or "?")] += hours

        items: List[BriefItem] = []
        cur = start
        while cur <= end:
            day = cur.isoformat()
            if cur.weekday() < 5 and cur not in window.holidays:
                logged = round(per_day.get(day, 0.0), 2)
                target = ctx.store.target_hours(day) if ctx.store else None
                items.append(BriefItem(
                    source="tempo", kind="worklog", key=day, title=f"{logged:g} h logged",
                    status="complete" if (target is not None and logged >= target - 0.01) else ("missing" if logged == 0 else "partial"),
                    starts_at=day,
                    extra={"hours": logged, "target_hours": target,
                           "by_issue": {k: round(v, 2) for k, v in sorted(per_issue.get(day, {}).items())}},
                ))
            cur = cur.fromordinal(cur.toordinal() + 1)
        return items
