"""AIS-797: both tool-execution paths feed the session ledger before tool.complete."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.session_ledger import pop_delta
from hermes_state import SessionDB
from run_agent import AIAgent

GET = "mcp_AIMDSSuiteMCP_mcp_openproject_pm_get_work_package"
LIST = "mcp_AIMDSSuiteMCP_mcp_openproject_pm_list_work_packages"


@pytest.fixture(autouse=True)
def _no_extra_model_calls(monkeypatch):
    monkeypatch.setattr(AIAgent, "_request_toolless_answer", lambda self, messages, instruction: "")
    monkeypatch.setattr(AIAgent, "_spawn_background_review", lambda self, *a, **k: None)


def _tool_defs(*names):
    return [{"type": "function", "function": {"name": n, "description": n, "parameters": {"type": "object", "properties": {}}}}
            for n in names]


@pytest.fixture
def agent(tmp_path):
    with (
        patch("run_agent.get_tool_definitions", return_value=_tool_defs(GET, LIST)),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("hermes_cli.config.load_config", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        a = AIAgent(api_key="test-key-1234567890", base_url="https://openrouter.ai/api/v1", max_iterations=5,
                    quiet_mode=True, skip_context_files=True, skip_memory=True)
    a.client = MagicMock()
    a.tool_delay = 0
    a.compression_enabled = False
    a.save_trajectories = False
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session(session_id=a.session_id, source="cli")
    a._session_db = db
    yield a
    db.close()


def _call(name, args, call_id):
    return SimpleNamespace(id=call_id, type="function", function=SimpleNamespace(name=name, arguments=json.dumps(args)))


def _result(name, args):
    if name == LIST:
        return json.dumps({"result": {"work_packages": [{"id": 9001, "key": "DEMO-1", "status": "Backlog"},
                                                         {"id": 9002, "key": "DEMO-2", "status": "Backlog"}]}})
    n = int(args["id"].split("-")[1])
    return json.dumps({"result": {"id": 9000 + n, "key": args["id"], "subject": f"Item {n}", "status": "Backlog"}})


def test_sequential_path_records_before_tool_complete(agent):
    seen = []
    agent.tool_complete_callback = lambda tc_id, name, args, result: seen.append(pop_delta(agent, tc_id))
    msg = SimpleNamespace(content="", tool_calls=[_call(GET, {"id": "DEMO-1"}, "c-seq")])
    with patch("run_agent.handle_function_call", side_effect=lambda name, args, *a, **k: _result(name, args)):
        agent._execute_tool_calls_sequential(msg, [], "task-1")
    assert len(seen) == 1 and seen[0] is not None
    assert [(e["verb"], e["ref"]["key"], e["tool_call_id"]) for e in seen[0]["entries"]] == [("read", "DEMO-1", "c-seq")]
    assert [e["ref"]["key"] for e in agent._session_db.get_ledger(agent.session_id)] == ["DEMO-1"]


def test_concurrent_path_records_every_call(agent):
    seen = {}
    agent.tool_complete_callback = lambda tc_id, name, args, result: seen.setdefault(tc_id, pop_delta(agent, tc_id))
    agent._invoke_tool = lambda name, args, *a, **k: _result(name, args)
    msg = SimpleNamespace(content="", tool_calls=[_call(LIST, {"project": "DEMO"}, "c-list"),
                                                  _call(GET, {"id": "DEMO-2"}, "c-get")])
    agent._execute_tool_calls_concurrent(msg, [], "task-1")
    assert set(seen) == {"c-list", "c-get"} and all(seen.values())
    entries = agent._session_db.get_ledger(agent.session_id)
    assert sorted((e["verb"], e.get("count")) for e in entries) == [("list", 2), ("read", None)]
