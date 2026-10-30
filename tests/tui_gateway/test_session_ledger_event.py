"""AIS-797: tool.complete is followed by a session.ledger delta when the call added entries."""

from __future__ import annotations

import json
from types import SimpleNamespace

from agent.session_ledger import record_tool_call
from tui_gateway import server

OP = "mcp_AIMDSSuiteMCP_mcp_openproject_"


def _capture(monkeypatch):
    events = []
    monkeypatch.setattr(server, "_emit", lambda event_type, sid, payload=None: events.append((event_type, sid, payload)))
    return events


def test_ledger_delta_follows_tool_complete_even_with_progress_off(monkeypatch):
    events = _capture(monkeypatch)
    agent = SimpleNamespace(session_id="db-session", _session_db=None, _current_turn_id="t1")
    monkeypatch.setitem(server._sessions, "gw-1", {"tool_progress_mode": "off", "tool_started_at": {}, "agent": agent})
    args = {"id": "DEMO-1"}
    result = json.dumps({"result": {"id": 9001, "key": "DEMO-1", "subject": "Prepare the staging host", "status": "Backlog"}})
    record_tool_call(agent, OP + "pm_get_work_package", args, result, tool_call_id="tool-1")

    server._on_tool_complete("gw-1", "tool-1", OP + "pm_get_work_package", args, result)

    ledger = [(sid, payload) for kind, sid, payload in events if kind == "session.ledger"]
    assert len(ledger) == 1
    sid, payload = ledger[0]
    assert sid == "gw-1"
    assert payload["v"] == 1 and payload["session_id"] == "db-session" and payload["lineage_root"] == "db-session"
    assert [(e["verb"], e["ref"]["key"], e["tool_call_id"]) for e in payload["entries"]] == [("read", "DEMO-1", "tool-1")]

    # Handed out once: a repeated callback emits nothing more.
    server._on_tool_complete("gw-1", "tool-1", OP + "pm_get_work_package", args, result)
    assert sum(1 for kind, *_ in events if kind == "session.ledger") == 1


def test_no_ledger_event_when_the_call_added_nothing(monkeypatch):
    events = _capture(monkeypatch)
    agent = SimpleNamespace(session_id="db-session", _session_db=None, _current_turn_id="")
    monkeypatch.setitem(server._sessions, "gw-2", {"tool_progress_mode": "all", "tool_started_at": {}, "agent": agent})
    record_tool_call(agent, "terminal", {"command": "pwd"}, '{"output": "/tmp"}', tool_call_id="tool-2")

    server._on_tool_complete("gw-2", "tool-2", "terminal", {"command": "pwd"}, '{"output": "/tmp"}')

    assert [kind for kind, *_ in events] == ["tool.complete"]
