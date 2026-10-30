"""AIS-797: the work feed — the brief collector for the ``workspace`` window."""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from cron import brief_collector as bc
from cron import workspace_feed as wf
from cron.brief_sources.base import BriefItem, SourceAdapter, SourceContext, SourceStatus
from cron.brief_store import BriefStore

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 30, 9, 0, tzinfo=TZ)  # a Friday
SUITE_OP = "mcp_AIMDSSuiteMCP_mcp_openproject_"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    s = BriefStore(tmp_path / "state.db")
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _fresh_cache():
    wf.clear_cache()
    yield
    wf.clear_cache()


class _Adapter(SourceAdapter):
    def __init__(self, name, items=None, skip=None, kinds=("morning-brief", "weekly-review", "workspace")):
        self.name, self._items, self._skip, self.kinds = name, items or [], skip, kinds
        self.fetched = 0

    def availability(self, ctx):
        return self._skip

    def fetch(self, window, ctx):
        self.fetched += 1
        ctx.dispatch("mcp_fake_tool", {})
        return list(self._items)


def _spy():
    calls = []

    def dispatch(tool, args):
        calls.append((tool, args))
        return "{}"

    return calls, dispatch


def _feed(store, adapters, dispatch, now=NOW, **kw):
    return wf.build_feed(now=now, cfg={}, store=store, adapters=adapters, mcp_status=[], tool_names=[],
                         vault_root=None, holidays={}, dispatch=dispatch, **kw)


def _ticket(key, status):
    return BriefItem("openproject", "ticket", key, f"Task {key}", status=status, priority="Normal",
                     due_at="2026-11-06", extra={"project": "Demo"})


def test_feed_normalises_items_and_sources_to_the_contract(store):
    calls, dispatch = _spy()
    adapters = [
        _Adapter("openproject", [_ticket("DEMO-1", "In Progress")]),
        _Adapter("m365", [BriefItem("m365", "mail", "m-1", "Weekly numbers", status="unread",
                                    updated_at="2026-10-30T08:10+01:00", extra={"from": "Sam"})]),
        _Adapter("jira", skip=SourceStatus("jira", "skipped", "not configured")),
    ]
    feed = _feed(store, adapters, dispatch)
    assert feed["v"] == 1 and feed["window"]["kind"] == "workspace"
    assert feed["window"]["today"] == "2026-10-30" and feed["window"]["preview_day"] == "2026-11-02"
    assert [(i["id"], i["status"]) for i in feed["items"]] == [
        ("openproject:ticket:DEMO-1", "In Progress"), ("m365:mail:m-1", "unread")]
    assert feed["items"][0]["due_at"] == "2026-11-06" and feed["items"][0]["extra"] == {"project": "Demo"}
    assert [(s["name"], s["status"], s["label"]) for s in feed["sources"]] == [
        ("openproject", "active", "OpenProject"), ("m365", "active", "Microsoft 365"), ("jira", "skipped", "Jira")]
    assert len(calls) == 2  # one per active source, none for the skipped one
    jsonschema = pytest.importorskip("jsonschema")
    from agent.contracts import validate

    try:
        validate("workspace_feed", feed)
    except jsonschema.ValidationError as exc:  # pragma: no cover - message for the failure
        pytest.fail(str(exc))


def test_no_active_source_makes_no_call(store):
    calls, dispatch = _spy()
    adapters = [
        _Adapter("m365", [BriefItem("m365", "mail", "x", "x")], skip=SourceStatus("m365", "skipped", "not connected")),
        _Adapter("openproject", [_ticket("DEMO-2", "Backlog")], skip=SourceStatus("openproject", "skipped", "disabled")),
    ]
    feed = _feed(store, adapters, dispatch)
    assert calls == [] and [a.fetched for a in adapters] == [0, 0]
    assert feed["items"] == []
    assert [(s["status"], s["unavailable"]) for s in feed["sources"]] == [("skipped", True), ("skipped", False)]


def test_status_history_and_watermark(store):
    _, dispatch = _spy()
    mail = BriefItem("m365", "mail", "m-2", "Invoice", status="unread")
    _feed(store, [_Adapter("openproject", [_ticket("DEMO-3", "Backlog")]), _Adapter("m365", [mail])], dispatch)
    later = NOW.replace(hour=11)
    feed = _feed(store, [_Adapter("openproject", [_ticket("DEMO-3", "In Progress")]), _Adapter("m365", [mail])],
                 dispatch, now=later)
    ticket = next(i for i in feed["items"] if i["kind"] == "ticket")
    assert (ticket["prev_status"], ticket["status"]) == ("Backlog", "In Progress")
    assert ticket["status_changed_at"] == later.isoformat(timespec="seconds")
    # The feed showing a mail is no notification: the mail check must still report it.
    assert store.existing_ids(["m365:mail:m-2"]) == set()
    assert store.existing_ids(["openproject:ticket:DEMO-3"]) == {"openproject:ticket:DEMO-3"}
    # Feed reads are not brief runs.
    assert store.last_run_at(wf.FEED_JOB_ID) is None


def test_adapters_scoped_to_other_kinds_are_not_asked(store):
    _, dispatch = _spy()
    only_brief = _Adapter("legacy", [_ticket("DEMO-4", "Backlog")], kinds=("morning-brief",))
    feed = _feed(store, [only_brief], dispatch)
    assert only_brief.fetched == 0 and feed["sources"] == []


def test_get_feed_caches_until_ttl_or_refresh(store, monkeypatch):
    built = []

    def fake_build(**kw):
        built.append(kw)
        return {"v": 1, "generated_at": "t", "window": {}, "items": [], "sources": []}

    monkeypatch.setattr(wf, "build_feed", fake_build)
    first = wf.get_feed(cfg={})
    second = wf.get_feed(cfg={})
    assert len(built) == 1 and first["cached"] is False and second["cached"] is True
    wf.get_feed(cfg={}, refresh=True)
    assert len(built) == 2
    wf.get_feed(cfg={"cron": {"brief_collector": {"feed_ttl_seconds": 0}}})
    assert len(built) == 3


def test_format_feed_is_plain_text(store):
    _, dispatch = _spy()
    feed = _feed(store, [_Adapter("openproject", [_ticket("DEMO-5", "Backlog")])], dispatch)
    lines = wf.format_feed(feed)
    assert lines[1] == "Sources: openproject=active(1)"
    assert any("DEMO-5 Task DEMO-5 [Backlog]" in line for line in lines)


# --- adapters for the workspace window ---------------------------------------------
class _Store:
    def __init__(self, targets=None):
        self._targets = targets or {}

    def open_ticket_keys(self, source, limit=10):
        return []

    def target_hours(self, day):
        return self._targets.get(day)


def _ctx(tools, responses, calls, status):
    def dispatch(tool, args):
        calls.append((tool, args))
        answer = responses[tool]
        if isinstance(answer, Exception):
            raise answer
        return json.dumps(answer(args) if callable(answer) else answer)

    return SourceContext(tool_names=set(tools), mcp_status=status, dispatch=dispatch, cfg={},
                         vault_root=None, store=_Store({"2026-10-30": 8.0}), lang="en")


def _suite_owner(monkeypatch):
    import tools.mcp_tool as mt

    monkeypatch.setattr(mt, "get_mcp_server_for_tool", lambda name: (
        "AIMDSSuiteMCP" if name.startswith("mcp_AIMDSSuiteMCP_") else None))


def test_openproject_lists_open_work_packages_and_todays_hours(monkeypatch):
    from cron.brief_sources.openproject import OpenProjectAdapter

    _suite_owner(monkeypatch)
    calls = []
    responses = {
        f"{SUITE_OP}pm_list_work_packages": {"has_more": False, "total": 2, "work_packages": [
            {"id": 9001, "key": "DEMO-1", "subject": "Prepare the staging host", "status": "In Progress",
             "priority": "High", "due_date": "2026-11-06", "project": "Demo", "type": "Task",
             "updated_at": "2026-10-29T10:00:00Z"},
            {"id": 9002, "key": "DEMO-2", "subject": "Write the runbook", "status": "Backlog", "project": "Demo"},
        ]},
        f"{SUITE_OP}pm_list_time_entries": {"has_more": False, "time_entries": [
            {"id": 1, "spent_on": "2026-10-30", "hours": "PT2H", "work_package_key": "DEMO-1"}]},
    }
    ctx = _ctx(list(responses), responses, calls, {"AIMDSSuiteMCP": {"connected": True}})
    adapter = OpenProjectAdapter()
    assert adapter.availability(ctx) is None
    items = adapter.fetch(bc.build_window(bc.FEED_KIND, NOW, {}), ctx)
    assert calls[0] == (f"{SUITE_OP}pm_list_work_packages", {"assignee": "me", "open_only": True, "limit": 30})
    assert calls[1][1]["spent_on_from"] == calls[1][1]["spent_on_to"] == "2026-10-30"
    tickets = [i for i in items if i.kind == "ticket"]
    assert [(t.key, t.status, t.priority, t.due_at) for t in tickets] == [
        ("DEMO-1", "In Progress", "High", "2026-11-06"), ("DEMO-2", "Backlog", "", "")]
    worklog = next(i for i in items if i.kind == "worklog")
    assert (worklog.key, worklog.extra["hours"], worklog.status) == ("2026-10-30", 2.0, "partial")


def test_openproject_feed_is_partial_when_one_part_fails(monkeypatch):
    from cron.brief_sources.openproject import OpenProjectAdapter

    _suite_owner(monkeypatch)
    responses = {
        f"{SUITE_OP}pm_list_work_packages": RuntimeError("upstream 503"),
        f"{SUITE_OP}pm_list_time_entries": {"time_entries": []},
    }
    ctx = _ctx(list(responses), responses, [], {"AIMDSSuiteMCP": {"connected": True}})
    adapter = OpenProjectAdapter()
    items = adapter.fetch(bc.build_window(bc.FEED_KIND, NOW, {}), ctx)
    assert [i.kind for i in items] == ["worklog"]
    assert adapter.partial_errors == ["work packages: upstream 503"]
    responses[f"{SUITE_OP}pm_list_time_entries"] = RuntimeError("timeout")
    with pytest.raises(RuntimeError):
        OpenProjectAdapter().fetch(bc.build_window(bc.FEED_KIND, NOW, {}), ctx)


def test_m365_feed_reads_calendar_mail_todos_and_teams():
    from cron.brief_sources.m365 import M365Adapter

    calls = []
    snapshot = "mcp_MSOffice365MCP_m365_brief_snapshot"
    responses = {snapshot: {"events": [], "unread_mail": [], "todos": [], "teams": {"chats": []}}}
    ctx = _ctx([snapshot], responses, calls, {"MSOffice365MCP": {"connected": True}})
    M365Adapter().fetch(bc.build_window(bc.FEED_KIND, NOW, {}), ctx)
    args = calls[0][1]
    assert args["mail_top"] > 0 and args["todo_top"] == 10 and args["chats_top"] == 5


def test_tempo_feed_reads_today():
    from cron.brief_sources.tempo import TempoAdapter

    calls = []
    tool = "mcp_TempoMCP_retrieveWorklogs"
    ctx = _ctx([tool], {tool: {"worklogs": []}}, calls, {"TempoMCP": {"connected": True}})
    TempoAdapter().fetch(bc.build_window(bc.FEED_KIND, NOW, {}), ctx)
    assert calls[0][1] == {"startDate": "2026-10-30", "endDate": "2026-10-30"}


def test_workspace_adapter_skips_the_previous_brief_for_the_feed(tmp_path):
    from cron.brief_sources.workspace import WorkspaceAdapter

    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "thisweek.md").write_text("- [ ] Review the demo plan\n- [x] Done thing\n", encoding="utf-8")
    (tmp_path / "journal").mkdir()
    (tmp_path / "journal" / "2026-10-29-morning-brief.md").write_text(
        "## Preview: next working day\n- something\n", encoding="utf-8")
    ctx = SourceContext(tool_names=set(), mcp_status={}, dispatch=lambda t, a: None, cfg={},
                        vault_root=tmp_path, store=None)
    feed_items = WorkspaceAdapter().fetch(bc.build_window(bc.FEED_KIND, NOW, {}), ctx)
    assert [i.title for i in feed_items] == ["Review the demo plan"]
    brief_items = WorkspaceAdapter().fetch(bc.build_window("morning-brief", NOW, {}), ctx)
    assert any(i.key.startswith("prev-preview:") for i in brief_items)
