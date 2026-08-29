"""agent/tool_findings.py — what the agent learns about a tool flows back to the vault."""

from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

import pytest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agent import tool_findings as tf  # noqa: E402


def _agent():
    return SimpleNamespace(valid_tool_names={"tool_search", "terminal"}, _loaded_deferred_tools={})


class TestTriggers:
    def test_error_then_success_with_changed_args_triggers(self):
        agent = _agent()
        tf.reset_turn(agent)
        tf.record_tool_outcome(agent, "mcp_TempoMCP_retrieveWorklogs", {"from": "2026-8-1"}, "invalid date", True)
        tf.record_tool_outcome(agent, "mcp_TempoMCP_retrieveWorklogs", {"from": "2026-08-01"}, "[...]", False)
        triggers = tf.turn_triggers(agent)
        assert "mcp_TempoMCP_retrieveWorklogs" in triggers
        assert any("changed arguments" in r for r in triggers["mcp_TempoMCP_retrieveWorklogs"])

    def test_a_plain_retry_with_the_same_arguments_does_not_trigger(self):
        agent = _agent()
        tf.reset_turn(agent)
        tf.record_tool_outcome(agent, "mcp_X_op", {"a": 1}, "timeout", True)
        tf.record_tool_outcome(agent, "mcp_X_op", {"a": 1}, "ok", False)
        assert tf.turn_triggers(agent) == {}

    def test_first_success_of_a_loaded_tool_triggers_once(self):
        agent = _agent()
        agent._loaded_deferred_tools = {"mcp_GithubMCP_list_releases": {}}
        tf.reset_turn(agent)
        tf.record_tool_outcome(agent, "mcp_GithubMCP_list_releases", {}, "[]", False)
        assert "mcp_GithubMCP_list_releases" in tf.turn_triggers(agent)
        tf.reset_turn(agent)
        tf.record_tool_outcome(agent, "mcp_GithubMCP_list_releases", {}, "[]", False)
        assert tf.turn_triggers(agent) == {}  # seen before

    def test_repeated_calls_trigger(self):
        agent = _agent()
        tf.reset_turn(agent)
        for i in range(3):
            tf.record_tool_outcome(agent, "mcp_AtlassianMCP_jira_get_worklog", {"issue": f"AIS-{i}"}, "{}", False)
        assert "called 3 times" in tf.turn_triggers(agent)["mcp_AtlassianMCP_jira_get_worklog"][0]

    def test_core_tools_and_bridge_tools_are_ignored(self):
        agent = _agent()
        tf.reset_turn(agent)
        for _ in range(4):
            tf.record_tool_outcome(agent, "terminal", {"command": "ls"}, "…", False)
        tf.record_tool_outcome(agent, "tool_search", {"query": "x"}, "{}", False)
        assert tf.turn_triggers(agent) == {}

    def test_reset_clears_the_tally(self):
        agent = _agent()
        tf.record_tool_outcome(agent, "mcp_X_op", {}, "e", True)
        tf.reset_turn(agent)
        assert tf.turn_triggers(agent) == {}


class TestPrompt:
    def test_prompt_names_tools_reasons_and_the_note_contract(self):
        text = tf.build_review_prompt({"mcp_TempoMCP_retrieveWorklogs": ["recovered after an error with changed arguments"]},
                                      "`mcp_AIMDSSuiteMCP_mcp_memory_memory_save`")
        assert "Tool: <tool name>" in text
        assert "mcp_TempoMCP_retrieveWorklogs: recovered after an error" in text
        assert "`mcp_AIMDSSuiteMCP_mcp_memory_memory_save`" in text
        assert "tool-finding" in text
        assert "'this tool is broken'" in text

    def test_spawn_helper_appends_the_tool_prompt(self):
        from agent.background_review import spawn_background_review_thread

        agent = SimpleNamespace(valid_tool_names={"vault_memory"})
        _target, prompt = spawn_background_review_thread(
            agent, [], review_memory=True, review_tools={"mcp_X_op": ["called 3 times in one turn"]}
        )
        assert prompt.startswith("Review the conversation above and consider saving to memory")
        assert "mcp_X_op: called 3 times" in prompt
        assert "vault_memory(action='save'" in prompt

    def test_tool_only_review_has_just_the_tool_prompt(self):
        from agent.background_review import spawn_background_review_thread

        _target, prompt = spawn_background_review_thread(
            SimpleNamespace(valid_tool_names=set()), [], review_tools={"mcp_X_op": ["x"]}
        )
        assert prompt.startswith("Review how the tools below were used")


class TestNotesSurface:
    def test_describe_result_carries_the_vault_note(self, monkeypatch, tmp_path):
        from agent import deferred_tools as dt
        from agent import memory_facade as mf
        from tools.registry import registry
        from tools.tool_search import bridge_tool_schemas

        # vault mode with a saved finding
        root = tmp_path / "vault"; root.mkdir()
        (root / "_conventions.md").write_text("---\ntype: conventions\n---\n", encoding="utf-8")
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
        monkeypatch.setattr("agent.runtime_cwd.resolve_agent_cwd", lambda: root)
        monkeypatch.setattr(mf, "primary_memory_context_registered", lambda: None)

        registry.register(
            name="mcp_tf_srv_retrieve", toolset="mcp-tf-srv",
            schema={"name": "mcp_tf_srv_retrieve", "description": "retrieve things",
                    "parameters": {"type": "object", "properties": {"from": {"type": "string"}}}},
            handler=lambda a, **k: "{}",
        )
        agent = SimpleNamespace(
            tools=bridge_tool_schemas(1), valid_tool_names={"tool_search", "tool_describe", "tool_call"},
            enabled_toolsets=["mcp-tf-srv"], disabled_toolsets=None, session_id="s",
        )
        facade = mf.MemoryFacade.for_agent(agent)
        assert facade.mode == mf.MODE_VAULT
        facade.save(title="Tool: mcp_tf_srv_retrieve", content="`from` must be YYYY-MM-DD; a `total: -1` reply is an error.",
                    type="reference", tags=["tool-finding"])

        payload = {"name": "mcp_tf_srv_retrieve", "description": "retrieve things",
                   "parameters": {"type": "object", "properties": {"from": {"type": "string"}}}}
        out = json.loads(dt.absorb_bridge_result(agent, "tool_describe", {"name": "mcp_tf_srv_retrieve"}, json.dumps(payload)))
        assert out["status"] == "loaded"
        assert "YYYY-MM-DD" in out["notes"]

    def test_no_note_no_field(self, monkeypatch, tmp_path):
        from agent import tool_findings as tf2
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
        monkeypatch.setattr("agent.runtime_cwd.resolve_agent_cwd", lambda: tmp_path)
        assert tf2.finding_notes_for(SimpleNamespace(valid_tool_names=set()), "mcp_nothing_here") == ""
