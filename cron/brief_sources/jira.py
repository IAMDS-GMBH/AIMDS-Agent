"""Jira adapter via AtlassianMCP (``mcp-atlassian``) — AIS-305.

One bundled ``jira_search`` scoped to the current user with ``fields`` and a
hard ``limit``; a second, optional call refreshes at most 10 tickets the
store still lists as open but the first call did not return (so a ticket
someone closed for you shows up as "changed" without reading all of Jira).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from cron.brief_sources.base import (
    BriefItem, SourceAdapter, SourceContext, Window, clip, dispatch_json, first_list, iso_day, parse_dt, resolve_tool,
)

SERVER = "AtlassianMCP"
SEARCH = "jira_search"
FIELDS = "key,summary,status,priority,duedate,updated,issuetype,project"
SEARCH_LIMIT = 20
REFRESH_LIMIT = 10


def _name(v: Any) -> str:
    if isinstance(v, dict):
        return str(v.get("name") or v.get("value") or "")
    return str(v or "")


class JiraAdapter(SourceAdapter):
    name = "jira"
    server = SERVER
    required_tools = (SEARCH,)
    kinds = ("morning-brief", "weekly-review")

    def fetch(self, window: Window, ctx: SourceContext) -> List[BriefItem]:
        tool = resolve_tool(ctx.tool_names, SERVER, SEARCH)
        jcfg = ((ctx.cfg.get("cron") or {}).get("brief_collector") or {}).get("jira") or {}
        assignee = str(jcfg.get("assignee") or "").strip()
        me = f'assignee = "{assignee}"' if assignee else "assignee = currentUser()"
        horizon = "endOfWeek()" if window.kind == "morning-brief" else "endOfWeek(1)"
        jql = (
            f"{me} AND statusCategory != Done AND "
            f"(updated >= -7d OR duedate <= {horizon} OR sprint in openSprints()) "
            "ORDER BY priority DESC, duedate ASC"
        )
        data = dispatch_json(ctx, tool, {"jql": jql, "fields": FIELDS, "limit": SEARCH_LIMIT})
        base_url = self._base_url(ctx)
        items = [self._item(i, base_url, window) for i in first_list(data, "issues", "results", "value")]
        items = [i for i in items if i is not None]

        returned = {i.key for i in items}
        stale = [k for k in ctx.store.open_ticket_keys("jira", limit=REFRESH_LIMIT) if k not in returned] if ctx.store else []
        if stale:
            keys = ", ".join(stale[:REFRESH_LIMIT])
            data2 = dispatch_json(ctx, tool, {"jql": f"key in ({keys})", "fields": FIELDS, "limit": REFRESH_LIMIT})
            for raw in first_list(data2, "issues", "results", "value"):
                item = self._item(raw, base_url, window)
                if item is not None:
                    item.extra["refreshed"] = True
                    items.append(item)
        return items

    @staticmethod
    def _base_url(ctx: SourceContext) -> str:
        cfg = ctx.mcp_config.get(SERVER) or {}
        env = cfg.get("env") or {}
        url = str(env.get("JIRA_URL") or "").rstrip("/")
        return url

    @staticmethod
    def _item(raw: Any, base_url: str, window: Window) -> Optional[BriefItem]:
        if not isinstance(raw, dict):
            return None
        key = str(raw.get("key") or "")
        if not key:
            return None
        fields = raw.get("fields") if isinstance(raw.get("fields"), dict) else raw
        status = fields.get("status")
        category = ""
        if isinstance(status, dict):
            cat = status.get("category") or status.get("statusCategory")
            category = _name(cat) if cat else ""
        due = parse_dt(fields.get("duedate") or fields.get("due_date"), window.tz)
        updated = parse_dt(fields.get("updated"), window.tz)
        url = str(raw.get("browse_url") or raw.get("url") or "") or (f"{base_url}/browse/{key}" if base_url else "")
        return BriefItem(
            source="jira", kind="ticket", key=key, title=clip(fields.get("summary"), 90),
            status=_name(status), priority=_name(fields.get("priority")),
            due_at=iso_day(due), updated_at=updated.isoformat(timespec="minutes") if updated else "",
            url=url,
            extra={"category": "Done" if is_done_category(category) else category,
                   "type": _name(fields.get("issuetype") or fields.get("issue_type"))},
        )


_DONE_CATEGORIES = {"done", "fertig", "erledigt", "complete", "completed", "abgeschlossen", "closed", "geschlossen"}


def is_done_category(category: str) -> bool:
    """Jira localises statusCategory names ("Fertig"); normalise the done bucket."""
    return str(category or "").strip().lower() in _DONE_CATEGORIES
