"""AIS-305: adapter availability + payload shaping with fixture payloads."""

from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from cron import brief_collector as bc
from cron.brief_sources.base import SourceContext, resolve_tool
from cron.brief_sources.jira import JiraAdapter
from cron.brief_sources.m365 import M365Adapter
from cron.brief_sources.tempo import TempoAdapter
from cron.brief_sources.workspace import WorkspaceAdapter

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 9, 8, 8, 0, tzinfo=TZ)


class _Store:
    def __init__(self, open_keys=(), targets=None):
        self._open = list(open_keys)
        self._targets = targets or {}

    def open_ticket_keys(self, source, limit=10):
        return self._open[:limit]

    def target_hours(self, day):
        return self._targets.get(day)


def _ctx(tools, calls, status=None, store=None, vault=None, mcp_config=None):
    def dispatch(tool, args):
        calls.append((tool, args))
        return json.dumps(_ctx.responses[tool](args) if callable(_ctx.responses[tool]) else _ctx.responses[tool])
    return SourceContext(tool_names=set(tools), mcp_status=status or {}, dispatch=dispatch, cfg={},
                         vault_root=vault, store=store, lang="en", mcp_config=mcp_config or {})


_ctx.responses = {}


def test_resolve_tool_matches_registered_names():
    names = {"mcp_MSOffice365MCP_m365_get_events", "mcp_AtlassianMCP_jira_search", "read_file"}
    assert resolve_tool(names, "MSOffice365MCP", "m365_get_events") == "mcp_MSOffice365MCP_m365_get_events"
    assert resolve_tool(names, "AtlassianMCP", "jira_search") == "mcp_AtlassianMCP_jira_search"
    assert resolve_tool(names, "AtlassianMCP", "jira_get_issue") is None


@pytest.mark.parametrize("status, tools, detail", [
    ({}, [], "not configured"),
    ({"AtlassianMCP": {"connected": False, "disabled": True}}, [], "disabled"),
    ({"AtlassianMCP": {"connected": False, "disabled": False}}, [], "not connected"),
    ({"AtlassianMCP": {"connected": True}}, [], "tool not in tools.include: jira_search"),
])
def test_jira_availability_names_first_failed_check(status, tools, detail):
    ctx = _ctx(tools, [], status=status)
    st = JiraAdapter().availability(ctx)
    assert st is not None and st.status == "skipped" and st.detail == detail


def test_jira_availability_ok_when_connected_and_tool_present():
    ctx = _ctx(["mcp_AtlassianMCP_jira_search"], [], status={"AtlassianMCP": {"connected": True}})
    assert JiraAdapter().availability(ctx) is None


def test_jira_fetch_is_user_scoped_bounded_and_refreshes_tracked_tickets():
    calls = []
    _ctx.responses = {"mcp_AtlassianMCP_jira_search": lambda args: {
        "issues": ([{"key": "AIS-9", "summary": "Fresh", "status": {"name": "In Progress", "category": "In Progress"},
                     "priority": {"name": "High"}, "duedate": "2026-09-10", "updated": "2026-09-07T10:00:00.000+0200",
                     "issue_type": {"name": "Story"}}] if "currentUser" in args["jql"]
                   else [{"key": "AIS-1", "summary": "Old", "status": {"name": "Done", "category": "Done"}}])}}
    ctx = _ctx(["mcp_AtlassianMCP_jira_search"], calls, store=_Store(open_keys=["AIS-1", "AIS-9"]),
               mcp_config={"AtlassianMCP": {"env": {"JIRA_URL": "https://jira.example.com/"}}})
    window = bc.build_window("morning-brief", NOW, {})
    items = JiraAdapter().fetch(window, ctx)
    assert len(calls) == 2
    first, second = calls
    assert "assignee = currentUser()" in first[1]["jql"] and first[1]["limit"] == 20 and "summary" in first[1]["fields"]
    assert second[1]["jql"] == "key in (AIS-1)" and second[1]["limit"] == 10
    by_key = {i.key: i for i in items}
    assert by_key["AIS-9"].status == "In Progress" and by_key["AIS-9"].due_at == "2026-09-10"
    assert by_key["AIS-9"].url == "https://jira.example.com/browse/AIS-9"
    assert by_key["AIS-1"].extra["refreshed"] is True and by_key["AIS-1"].extra["category"] == "Done"


def test_jira_assignee_override_from_config():
    calls = []
    _ctx.responses = {"mcp_AtlassianMCP_jira_search": {"issues": []}}
    ctx = _ctx(["mcp_AtlassianMCP_jira_search"], calls, store=_Store())
    ctx.cfg = {"cron": {"brief_collector": {"jira": {"assignee": "svc.bot"}}}}
    JiraAdapter().fetch(bc.build_window("morning-brief", NOW, {}), ctx)
    assert 'assignee = "svc.bot"' in calls[0][1]["jql"]


def test_m365_prefers_snapshot_tool_and_drops_bodies():
    calls = []
    snap = {
        "events": [{"subject": "Daily", "start_local": "2026-09-08T08:45:00", "end_local": "2026-09-08T08:50:00", "location": "Teams", "organizer": "Max"},
                   {"subject": "Later", "start_local": "2026-09-20T08:45:00", "end_local": "2026-09-20T09:00:00"}],
        "unread_mail": [{"id": "m1", "received": "2026-09-08T06:00:00Z", "from_name": "Anna", "subject": "Invoice", "has_attachments": True, "bodyPreview": "SECRET BODY"},
                        {"id": "m2", "received": "2026-09-08T06:30:00Z", "from_name": "Bob", "subject": "Read one", "isRead": True}],
        "todos": [{"id": "t1", "title": "Pay", "status": "notStarted", "due": "2026-09-09"},
                  {"id": "t2", "title": "Done thing", "status": "completed"}],
        "teams": {"chats": [{"chat_id": "c1", "topic": "Team", "messages": [{"from": "Eve", "created": "2026-09-08T07:00:00Z", "preview": "ping"}]}]},
        "errors": [{"source": "teams", "error": "partial"}],
    }
    _ctx.responses = {"mcp_MSOffice365MCP_m365_brief_snapshot": snap}
    ctx = _ctx(["mcp_MSOffice365MCP_m365_brief_snapshot", "mcp_MSOffice365MCP_m365_get_events"], calls,
               status={"MSOffice365MCP": {"connected": True}})
    adapter = M365Adapter()
    assert adapter.availability(ctx) is None
    items = adapter.fetch(bc.build_window("morning-brief", NOW, {}), ctx)
    assert [c[0] for c in calls] == ["mcp_MSOffice365MCP_m365_brief_snapshot"]
    kinds = sorted((i.kind, i.key) for i in items)
    assert kinds == [("chat", "c1:2026-09-08T09:00+02:00:Eve"), ("event", "2026-09-08T0845-Daily"), ("mail", "m1"), ("task", "t1")]
    dump = json.dumps([i.__dict__ for i in items], default=str)
    assert "SECRET BODY" not in dump and "Read one" not in dump and "Done thing" not in dump
    assert adapter.partial_errors == ["teams: partial"]


def test_m365_falls_back_to_individual_tools():
    calls = []
    _ctx.responses = {
        "mcp_MSOffice365MCP_m365_get_events": {"value": []},
        "mcp_MSOffice365MCP_m365_list_emails": {"value": [{"id": "m1", "subject": "Hi", "isRead": False, "receivedDateTime": "2026-09-08T06:00:00Z", "from": {"emailAddress": {"name": "Anna"}}}]},
        "mcp_MSOffice365MCP_m365_list_todo_tasks": {"tasks": []},
        "mcp_MSOffice365MCP_m365_get_activity_feed": {"recent_chats": [], "team_channels": []},
    }
    ctx = _ctx(list(_ctx.responses), calls, status={"MSOffice365MCP": {"connected": True}})
    items = M365Adapter().fetch(bc.build_window("morning-brief", NOW, {}), ctx)
    assert sorted(c[0] for c in calls) == sorted(_ctx.responses)
    assert [(i.kind, i.extra.get("from")) for i in items] == [("mail", "Anna")]


def test_m365_mail_check_only_calls_mail():
    calls = []
    _ctx.responses = {"mcp_MSOffice365MCP_m365_list_emails": {"value": []}, "mcp_MSOffice365MCP_m365_get_events": {"value": []}}
    ctx = _ctx(list(_ctx.responses), calls, status={"MSOffice365MCP": {"connected": True}})
    M365Adapter().fetch(bc.build_window("mail-check", NOW, {}), ctx)
    assert [c[0] for c in calls] == ["mcp_MSOffice365MCP_m365_list_emails"]


def test_tempo_aggregates_hours_per_working_day_against_targets():
    calls = []
    _ctx.responses = {"mcp_TempoMCP_retrieveWorklogs": {"worklogs": [
        {"timeSpentSeconds": 3600, "startDate": "2026-09-07", "issue": {"key": "AIS-1"}},
        {"timeSpentSeconds": 5400, "startDate": "2026-09-07", "issue": {"key": "AIS-2"}},
    ]}}
    ctx = _ctx(["mcp_TempoMCP_retrieveWorklogs"], calls, status={"TempoMCP": {"connected": True}},
               store=_Store(targets={"2026-09-07": 8.0}))
    items = TempoAdapter().fetch(bc.build_window("morning-brief", NOW, {}), ctx)
    assert calls[0][1] == {"startDate": "2026-09-07", "endDate": "2026-09-07"}
    assert len(items) == 1 and items[0].key == "2026-09-07" and items[0].status == "partial"
    assert items[0].extra == {"hours": 2.5, "target_hours": 8.0, "by_issue": {"AIS-1": 1.0, "AIS-2": 1.5}}


def test_workspace_reads_signals_and_previous_preview(tmp_path):
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "thisweek.md").write_text("- [x] done\n- [ ] call Anna\n- [ ] send offer\n", encoding="utf-8")
    (tmp_path / "_open-questions.md").write_text("# Q\n- budget approved?\n", encoding="utf-8")
    (tmp_path / "journal").mkdir()
    (tmp_path / "journal" / "2026-09-07-morning-brief.md").write_text(
        "---\ntype: journal\n---\n# Brief\n\n## Heute\n- x\n\n## Vorschau: nächster Arbeitstag\n- 09:00 Kickoff\n- 14:00 Review\n\nFINDING: y\n", encoding="utf-8")
    ctx = _ctx([], [], vault=tmp_path)
    adapter = WorkspaceAdapter()
    assert adapter.availability(ctx) is None
    items = adapter.fetch(bc.build_window("morning-brief", NOW, {}), ctx)
    titles = {i.key.split(":")[0]: i.title for i in items}
    assert [i.title for i in items if i.kind == "task"] == ["call Anna", "send offer"]
    assert titles["open-question"] == "budget approved?"
    assert titles["prev-preview"] == "- 09:00 Kickoff - 14:00 Review"  # clip() collapses whitespace
    assert adapter.parts == ["tasks", "open-questions", "prev-brief"]
    assert WorkspaceAdapter().availability(_ctx([], [], vault=tmp_path / "missing")).detail == "no workspace"


def test_dispatch_json_unwraps_registry_envelopes_and_json_strings():
    from cron.brief_sources.base import dispatch_json

    ctx = _ctx([], [])
    ctx.dispatch = lambda tool, args: json.dumps({"result": {"result": json.dumps({"issues": [{"key": "AIS-1"}]})}})
    assert dispatch_json(ctx, "x", {}) == {"issues": [{"key": "AIS-1"}]}
    ctx.dispatch = lambda tool, args: json.dumps({"result": {"result": {"value": [1, 2]}}})
    assert dispatch_json(ctx, "x", {}) == {"value": [1, 2]}
    ctx.dispatch = lambda tool, args: json.dumps({"error": "boom"})
    with pytest.raises(RuntimeError, match="boom"):
        dispatch_json(ctx, "x", {})


def test_jira_localized_done_category_and_browse_url():
    calls = []
    _ctx.responses = {"mcp_AtlassianMCP_jira_search": {"result": {"result": json.dumps({"total": -1, "issues": [
        {"key": "AIS-7", "summary": "Fertig gemacht", "browse_url": "https://j/browse/AIS-7",
         "status": {"name": "Fertig", "category": "Fertig"}, "updated": "2026-09-08 09:20:48 CEST"}]})}}}
    ctx = _ctx(["mcp_AtlassianMCP_jira_search"], calls, store=_Store())
    items = JiraAdapter().fetch(bc.build_window("morning-brief", NOW, {}), ctx)
    assert items[0].extra["category"] == "Done" and items[0].url == "https://j/browse/AIS-7"
    assert items[0].updated_at.startswith("2026-09-08")


@pytest.mark.parametrize("value, expected", [
    ({"dateTime": "2026-09-08T15:00:00.0000000", "timeZone": "Europe/Berlin"}, "2026-09-08T15:00+02:00"),
    ("2026-09-08T07:39:45Z", "2026-09-08T07:39+00:00"),
    ("2026-09-08 09:20:48 CEST", "2026-09-08T09:20+02:00"),
    ("2026-09-08 15:00:00 (Europe/Berlin)", "2026-09-08T15:00+02:00"),
    ("2026-09-07T10:00:00.000+0200", "2026-09-07T10:00+02:00"),
    ("2026-09-10", "2026-09-10T00:00+02:00"),
    ("nope", None),
])
def test_parse_dt_shapes(value, expected):
    from cron.brief_sources.base import parse_dt

    got = parse_dt(value, TZ)
    assert (got.isoformat(timespec="minutes") if got else None) == expected


def test_strip_html_for_teams_bodies():
    from cron.brief_sources.base import strip_html

    assert strip_html('<div> <attachment id="1"></attachment> Ich auch nicht!</div>') == "Ich auch nicht!"
    assert strip_html("<p><a href='x'>PR #28</a>&nbsp;bitte</p>") == "PR #28 bitte"
    assert strip_html('ERR loading <a href="https://x/y?client_id=her') == "ERR loading"
