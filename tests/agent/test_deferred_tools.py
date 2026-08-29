"""Per-session loading of deferred tools — agent/deferred_tools.py.

The contract these tests pin: a tool the model found via tool_search (or
loaded with tool_describe, or invoked through tool_call, or simply called by
its real name) becomes callable by name in *this* session, stays inside the
session's toolset scope, survives a tool-list rebuild, and follows the live
registry when it changes.
"""

from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

import pytest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agent import deferred_tools as dt  # noqa: E402
from tools.registry import registry  # noqa: E402
from tools.tool_search import BRIDGE_TOOL_NAMES, bridge_tool_schemas  # noqa: E402


def _td(name, description="", properties=None):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties or {"repo": {"type": "string"}},
                "required": ["repo"],
            },
        },
    }


def _register(name, toolset, description="desc"):
    def _handler(args, task_id=None, **kw):
        return json.dumps({"ok": True, "tool": name})

    # registry.register takes the inner {name, description, parameters} form.
    registry.register(name=name, handler=_handler, schema=_td(name, description)["function"], toolset=toolset)


def _agent(enabled_toolsets, *, with_bridge=True):
    tools = [_td("terminal", "run a command", {"command": {"type": "string"}})]
    if with_bridge:
        tools += bridge_tool_schemas(5)
    return SimpleNamespace(
        tools=tools,
        valid_tool_names={t["function"]["name"] for t in tools},
        enabled_toolsets=enabled_toolsets,
        disabled_toolsets=None,
        _repair_tool_call=lambda name: None,
    )


def _names(agent):
    return [t["function"]["name"] for t in agent.tools]


class TestLoadDeferredTool:
    def test_direct_load_appends_schema_at_the_end(self):
        _register("mcp_dt_gh_create_issue", "mcp-dt-gh")
        agent = _agent(["mcp-dt-gh"])
        assert "mcp_dt_gh_create_issue" not in agent.valid_tool_names

        assert dt.load_deferred_tool(agent, "mcp_dt_gh_create_issue") == "mcp_dt_gh_create_issue"

        assert _names(agent)[-1] == "mcp_dt_gh_create_issue"
        assert "mcp_dt_gh_create_issue" in agent.valid_tool_names
        assert agent.tools[-1]["function"]["parameters"]["required"] == ["repo"]
        # idempotent — no duplicate entry on a second load
        assert dt.load_deferred_tool(agent, "mcp_dt_gh_create_issue") == "mcp_dt_gh_create_issue"
        assert _names(agent).count("mcp_dt_gh_create_issue") == 1

    def test_suffix_name_resolves_like_tool_call_does(self):
        _register("mcp_dt_suffix_jira_search", "mcp-dt-suffix")
        agent = _agent(["mcp-dt-suffix"])
        assert dt.load_deferred_tool(agent, "jira_search") == "mcp_dt_suffix_jira_search"
        assert "mcp_dt_suffix_jira_search" in agent.valid_tool_names

    def test_out_of_scope_tool_is_refused(self):
        _register("mcp_dt_scope_in_op", "mcp-dt-scope-in")
        _register("mcp_dt_scope_out_op", "mcp-dt-scope-out")
        agent = _agent(["mcp-dt-scope-in"])
        assert dt.load_deferred_tool(agent, "mcp_dt_scope_out_op") is None
        assert "mcp_dt_scope_out_op" not in agent.valid_tool_names
        assert dt.load_deferred_tool(agent, "mcp_dt_scope_in_op") == "mcp_dt_scope_in_op"

    def test_core_and_bridge_and_unknown_names_are_not_loaded(self):
        _register("mcp_dt_core_op", "mcp-dt-core")
        agent = _agent(["mcp-dt-core"])
        assert dt.load_deferred_tool(agent, "terminal") is None
        for bridge in BRIDGE_TOOL_NAMES:
            assert dt.load_deferred_tool(agent, bridge) is None
        assert dt.load_deferred_tool(agent, "definitely_not_a_tool_xyz") is None
        assert _names(agent) == ["terminal", *sorted(BRIDGE_TOOL_NAMES, key=_names(agent).index)]

    def test_nothing_loads_when_tool_search_is_not_in_the_session(self):
        _register("mcp_dt_nobridge_op", "mcp-dt-nobridge")
        agent = _agent(["mcp-dt-nobridge"], with_bridge=False)
        assert dt.load_deferred_tool(agent, "mcp_dt_nobridge_op") is None
        assert _names(agent) == ["terminal"]

    def test_autoload_cap_only_limits_search_loads(self):
        for i in range(dt.MAX_AUTOLOADED_TOOLS + 2):
            _register(f"mcp_dt_cap_op_{i}", "mcp-dt-cap")
        agent = _agent(["mcp-dt-cap"])
        for i in range(dt.MAX_AUTOLOADED_TOOLS):
            assert dt.load_deferred_tool(agent, f"mcp_dt_cap_op_{i}", reason="search", enforce_cap=True)
        assert dt.load_deferred_tool(agent, f"mcp_dt_cap_op_{dt.MAX_AUTOLOADED_TOOLS}", reason="search", enforce_cap=True) is None
        # an explicit describe/direct load still goes through
        assert dt.load_deferred_tool(agent, f"mcp_dt_cap_op_{dt.MAX_AUTOLOADED_TOOLS + 1}", reason="describe")


class TestRegistryChanges:
    def test_deregistered_tool_is_dropped_when_generation_moves(self):
        _register("mcp_dt_gen_gone", "mcp-dt-gen")
        _register("mcp_dt_gen_stays", "mcp-dt-gen")
        agent = _agent(["mcp-dt-gen"])
        dt.load_deferred_tool(agent, "mcp_dt_gen_gone")
        dt.load_deferred_tool(agent, "mcp_dt_gen_stays")
        assert dt.ensure_loaded_tools_current(agent) is False  # same generation → no-op

        registry.deregister("mcp_dt_gen_gone")
        assert dt.ensure_loaded_tools_current(agent) is True
        assert "mcp_dt_gen_gone" not in agent.valid_tool_names
        assert "mcp_dt_gen_gone" not in _names(agent)
        assert "mcp_dt_gen_stays" in _names(agent)

    def test_changed_schema_is_replaced_in_place(self):
        _register("mcp_dt_schema_op", "mcp-dt-schema", description="v1")
        agent = _agent(["mcp-dt-schema"])
        dt.load_deferred_tool(agent, "mcp_dt_schema_op")
        assert agent.tools[-1]["function"]["description"] == "v1"

        registry.deregister("mcp_dt_schema_op")
        _register("mcp_dt_schema_op", "mcp-dt-schema", description="v2")
        assert dt.ensure_loaded_tools_current(agent) is True
        assert agent.tools[-1]["function"]["description"] == "v2"
        assert _names(agent).count("mcp_dt_schema_op") == 1


class TestRebuild:
    def test_apply_tool_definitions_reappends_loaded_tools(self):
        _register("mcp_dt_rebuild_op", "mcp-dt-rebuild")
        agent = _agent(["mcp-dt-rebuild"])
        dt.load_deferred_tool(agent, "mcp_dt_rebuild_op")

        fresh = [_td("terminal"), *bridge_tool_schemas(3)]
        dt.apply_tool_definitions(agent, fresh)

        assert _names(agent)[-1] == "mcp_dt_rebuild_op"
        assert "mcp_dt_rebuild_op" in agent.valid_tool_names
        assert "terminal" in agent.valid_tool_names

    def test_apply_tool_definitions_drops_loads_that_left_scope(self):
        _register("mcp_dt_rebuild_gone", "mcp-dt-rebuild-gone")
        agent = _agent(["mcp-dt-rebuild-gone"])
        dt.load_deferred_tool(agent, "mcp_dt_rebuild_gone")

        agent.enabled_toolsets = ["mcp-dt-rebuild"]  # session was re-scoped
        dt.apply_tool_definitions(agent, [_td("terminal"), *bridge_tool_schemas(3)])
        assert "mcp_dt_rebuild_gone" not in agent.valid_tool_names
        assert not dt.is_loaded(agent, "mcp_dt_rebuild_gone")


class TestConversationLoopHook:
    def test_unknown_name_that_is_a_deferred_tool_loads_instead_of_rejecting(self):
        _register("mcp_dt_hook_worklogs", "mcp-dt-hook")
        agent = _agent(["mcp-dt-hook"])
        assert dt.resolve_unknown_tool_name(agent, "mcp_dt_hook_worklogs") == "mcp_dt_hook_worklogs"
        assert "mcp_dt_hook_worklogs" in agent.valid_tool_names

    def test_unknown_core_name_still_goes_through_fuzzy_repair(self):
        agent = _agent(["mcp-dt-hook"])
        agent._repair_tool_call = lambda name: "terminal" if name == "Terminal_tool" else None
        assert dt.resolve_unknown_tool_name(agent, "Terminal_tool") == "terminal"
        assert dt.resolve_unknown_tool_name(agent, "nope_nope") is None

    def test_hint_mentions_tool_search_only_when_the_bridge_is_active(self):
        assert "tool_search" in dt.unknown_tool_hint(_agent(["x"]))
        assert dt.unknown_tool_hint(_agent(["x"], with_bridge=False)) == ""


class TestBridgeResults:
    def test_tool_search_result_autoloads_top_hits_and_marks_status(self):
        for i in range(4):
            _register(f"mcp_dt_search_op_{i}", "mcp-dt-search", description=f"search op {i}")
        agent = _agent(["mcp-dt-search"])
        payload = {
            "query": "search op",
            "mode": "ranked",
            "total_available": 4,
            "matches": [{"name": f"mcp_dt_search_op_{i}", "kind": "tool", "description": "x"} for i in range(4)],
            "autoload": ["mcp_dt_search_op_0", "mcp_dt_search_op_1"],
        }
        out = json.loads(dt.absorb_bridge_result(agent, "tool_search", {"query": "search op"}, json.dumps(payload)))

        assert "autoload" not in out
        statuses = {m["name"]: m["status"] for m in out["matches"]}
        assert statuses["mcp_dt_search_op_0"] == "loaded"
        assert statuses["mcp_dt_search_op_1"] == "loaded"
        assert "tool_describe" in statuses["mcp_dt_search_op_2"]
        assert {"mcp_dt_search_op_0", "mcp_dt_search_op_1"} <= agent.valid_tool_names
        assert "mcp_dt_search_op_2" not in agent.valid_tool_names

    def test_tool_describe_result_loads_and_drops_the_schema_echo(self):
        _register("mcp_dt_describe_op", "mcp-dt-describe", description="describe me")
        agent = _agent(["mcp-dt-describe"])
        payload = {
            "name": "mcp_dt_describe_op",
            "description": "describe me",
            "parameters": _td("x")["function"]["parameters"],
            "usage_hint": "Call tool_call(...)",
        }
        out = json.loads(dt.absorb_bridge_result(agent, "tool_describe", {"name": "mcp_dt_describe_op"}, json.dumps(payload)))

        assert out["status"] == "loaded"
        assert "parameters" not in out
        assert out["required"] == ["repo"]
        assert out["arguments"] == {"repo": "string"}
        assert "call it directly" in out["usage_hint"]
        assert "mcp_dt_describe_op" in agent.valid_tool_names

    def test_error_results_and_other_tools_pass_through(self):
        agent = _agent(["mcp-dt-x"])
        err = json.dumps({"error": "Tool 'x' is not registered or found."})
        assert dt.absorb_bridge_result(agent, "tool_describe", {}, err) == err
        assert dt.absorb_bridge_result(agent, "terminal", {}, "plain output") == "plain output"
        assert dt.absorb_bridge_result(agent, "tool_search", {}, "not json") == "not json"


class TestEndToEnd:
    def test_search_then_direct_call_through_handle_function_call(self):
        """The contract the prompt promises: search, then call by name."""
        import model_tools

        _register("mcp_dt_e2e_retrieve_worklogs", "mcp-dt-e2e", description="Retrieve Tempo worklogs for a date range")
        agent = _agent(["mcp-dt-e2e"])

        raw = model_tools.handle_function_call(
            function_name="tool_search",
            function_args={"query": "retrieve worklogs"},
            enabled_toolsets=["mcp-dt-e2e"],
        )
        out = json.loads(dt.absorb_bridge_result(agent, "tool_search", {"query": "retrieve worklogs"}, raw))
        assert out["mode"] == "ranked"
        hit = next(m for m in out["matches"] if m["name"] == "mcp_dt_e2e_retrieve_worklogs")
        assert hit["status"] == "loaded"
        assert "mcp_dt_e2e_retrieve_worklogs" in agent.valid_tool_names

        # and the conversation loop would now accept the direct call
        assert dt.resolve_unknown_tool_name(agent, "mcp_dt_e2e_retrieve_worklogs") == "mcp_dt_e2e_retrieve_worklogs"
        result = json.loads(model_tools.handle_function_call(
            function_name="mcp_dt_e2e_retrieve_worklogs",
            function_args={"repo": "a"},
            enabled_toolsets=["mcp-dt-e2e"],
        ))
        assert result["ok"] is True


class TestServerSkills:
    _CTX = {
        "rules": [],
        "suggested_skills": [
            {"auto_suggest": True, "builtin": True, "category": "coding", "slug": "repo-map",
             "name": "Repository Index Map", "description": "Cache architecture maps and file trees", "hash": "111b8b3b"},
            {"auto_suggest": True, "builtin": False, "category": "workflow", "slug": "session-start",
             "name": "Session Start", "description": "Mandatory session startup sequence", "hash": "55e4d073"},
        ],
    }

    def _agent_with_skill_tool(self):
        agent = _agent(["mcp-dt-srvskills"])
        agent.tools.append(_td("mcp_AIMDSSuiteMCP_mcp_memory_skill", "read a server skill", {"slug": {"type": "string"}}))
        agent.valid_tool_names.add("mcp_AIMDSSuiteMCP_mcp_memory_skill")
        return agent

    def test_memory_context_result_captures_suggested_skills(self):
        agent = self._agent_with_skill_tool()
        raw = json.dumps(self._CTX)
        out = dt.absorb_bridge_result(agent, "mcp_AIMDSSuiteMCP_mcp_memory_memory_context", {}, raw)
        assert out == raw  # untouched
        skills = dt.mcp_skills(agent)
        assert [s["slug"] for s in skills] == ["repo-map", "session-start"]
        assert skills[0]["kind"] == "mcp_skill"
        assert skills[0]["how_to_use"] == "mcp_AIMDSSuiteMCP_mcp_memory_skill(action='read', slug='repo-map')"

    def test_server_skills_are_ranked_into_search_results_first(self):
        agent = self._agent_with_skill_tool()
        dt.remember_mcp_skills(agent, "mcp_AIMDSSuiteMCP_mcp_memory_memory_context", json.dumps(self._CTX))
        payload = {"query": "repository architecture map", "mode": "ranked", "total_available": 1,
                   "matches": [{"name": "mcp_x_op", "kind": "tool", "description": "x"}], "autoload": []}
        out = json.loads(dt.absorb_bridge_result(agent, "tool_search", {"query": "repository architecture map"}, json.dumps(payload)))
        assert out["matches"][0]["kind"] == "mcp_skill"
        assert out["matches"][0]["name"] == "Repository Index Map"
        assert "slug='repo-map'" in out["matches"][0]["how_to_use"]
        assert out["matches"][-1]["name"] == "mcp_x_op"

    def test_kind_tool_filter_suppresses_server_skills(self):
        agent = self._agent_with_skill_tool()
        dt.remember_mcp_skills(agent, "memory_context", json.dumps(self._CTX))
        payload = {"query": "session start", "mode": "ranked", "total_available": 0, "matches": [], "autoload": []}
        out = json.loads(dt.absorb_bridge_result(agent, "tool_search", {"query": "session start", "kind": "tool"}, json.dumps(payload)))
        assert out["matches"] == []
