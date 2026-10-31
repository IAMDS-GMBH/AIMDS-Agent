"""Deterministic delegation hint (AIS-796): more than N calls of one tool in a
turn, or an MCP result over the result shaper's list budget, adds one hint
line to the tool result. Never blocks, no LLM call."""

from __future__ import annotations

from types import SimpleNamespace

from agent.delegation_nudge import DEFAULT_NUDGE_THRESHOLD, DelegationNudge
from agent.tool_guardrails import ToolCallGuardrailController

CREATE = "mcp_AIMDSSuiteMCP_mcp_openproject-pm_create_work_package"
LIST = "mcp_AIMDSSuiteMCP_mcp_openproject-pm_list_work_packages"


def _calls(nudge, name, count, result="{}"):
    return [nudge.observe(name, result) for _ in range(count)]


def test_threshold_is_more_than_six_similar_calls():
    assert DEFAULT_NUDGE_THRESHOLD == 6
    nudge = DelegationNudge(platform="tui")
    hints = _calls(nudge, CREATE, 31)
    assert hints[:6] == [""] * 6
    assert hints[6].startswith("[Delegation hint: 7 ") and "delegate_task" in hints[6]
    # A write batch points at apply and at the user's approval; once per turn.
    assert "'apply'" in hints[6] and "approved" in hints[6]
    assert hints[7:] == [""] * 24


def test_read_batches_point_at_the_reading_kinds():
    hint = [h for h in _calls(DelegationNudge(platform="tui"), LIST, 7) if h][0]
    assert "'collect' or 'sweep'" in hint and "approved" not in hint


def test_kinds_not_offered_on_the_surface_are_not_named(monkeypatch):
    import agent.agent_definitions as ad

    monkeypatch.setattr(ad, "definitions_for", lambda platform, dirs=None: [])
    hint = [h for h in _calls(DelegationNudge(platform="tui"), CREATE, 7) if h][0]
    assert "agent '" not in hint and "delegate_task" in hint


def test_delegating_in_the_turn_silences_it_and_the_next_turn_counts_again():
    nudge = DelegationNudge()
    _calls(nudge, CREATE, 3)
    assert nudge.observe("delegate_task", "{}") == ""
    assert _calls(nudge, CREATE, 10) == [""] * 10
    nudge.reset_for_turn()
    assert any(_calls(nudge, CREATE, 7))


def test_different_tools_and_housekeeping_do_not_add_up():
    nudge = DelegationNudge()
    assert not any(_calls(nudge, CREATE, 6) + _calls(nudge, LIST, 6))
    assert not any(_calls(nudge, "todo", 20) + _calls(nudge, "clarify", 20) + _calls(nudge, "tool_search", 20))


def test_zero_switches_it_off():
    assert not any(_calls(DelegationNudge(threshold=0), CREATE, 50))
    assert DelegationNudge(threshold="x").threshold == DEFAULT_NUDGE_THRESHOLD


def test_a_result_over_the_shaper_list_budget_points_at_digest():
    from tools.mcp_result_shaper import DEFAULT_LIST_BYTE_BUDGET

    nudge = DelegationNudge(platform="tui")
    assert nudge.observe(LIST, "x" * DEFAULT_LIST_BYTE_BUDGET) == ""
    hint = nudge.observe(LIST, "x" * (DEFAULT_LIST_BYTE_BUDGET + 1))
    assert "'digest'" in hint and f"{DEFAULT_LIST_BYTE_BUDGET + 1:,}" in hint
    assert nudge.observe(LIST, "x" * 50_000) == ""  # once per turn
    # Non-MCP tools have no shaper limit.
    assert DelegationNudge().observe("read_file", "x" * 500_000) == ""


def _agent(valid_tool_names, nudge):
    import run_agent

    agent = SimpleNamespace(
        _tool_guardrails=ToolCallGuardrailController(),
        _tool_guardrail_halt_decision=None,
        _delegation_nudge=nudge,
        valid_tool_names=set(valid_tool_names),
    )
    agent._set_tool_guardrail_halt = lambda d: run_agent.AIAgent._set_tool_guardrail_halt(agent, d)
    return agent


def test_the_hint_lands_in_the_tool_result_of_the_main_chat_only():
    import run_agent

    observe = run_agent.AIAgent._append_guardrail_observation
    chat = _agent({"delegate_task", CREATE}, DelegationNudge(platform="tui"))
    results = [observe(chat, CREATE, {"subject": f"Sample task {i:02d}"}, '{"id": 1}', failed=False) for i in range(8)]
    assert results[5] == '{"id": 1}'
    assert results[6].startswith('{"id": 1}\n\n[Delegation hint: 7 ')
    # A child (no delegate_task) never gets it.
    child = _agent({CREATE}, DelegationNudge(platform="tui"))
    assert all(observe(child, CREATE, {}, "{}", failed=False) == "{}" for _ in range(10))
