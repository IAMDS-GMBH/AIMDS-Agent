"""The ``apply`` agent and the generated delegation line (AIS-796).

apply carries out a change list the user already approved. A child cannot ask
the user, so the tool rule keeps it to editable, reversible MCP writes: no
sending, no deleting, no confirming for the user, no skipped dry run — and no
Hermes write tool at all.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent import agent_definitions as ad
from agent.tool_guardrails import ToolCallGuardrailController

SUITE = "mcp_AIMDSSuiteMCP_mcp_"
OP = SUITE + "openproject-pm_"
M365 = "mcp_MSOffice365MCP_m365_"
HUB = "mcp_MCPUnifiedHub_"


@pytest.fixture(scope="module")
def apply():
    definition = ad.load_definitions([ad.BUNDLED_AGENTS_DIR])["apply"]
    assert definition is not None
    return definition


def _denied(definition, name, args=None):
    return ad.tool_call_denial(definition, name, args or {})


# --------------------------------------------------------------------------- definition


def test_apply_writes_through_mcp_only_and_offers_no_clarify(apply):
    assert apply.writes and apply.toolsets == ("mcp",)
    assert not apply.keeps_hermes_write_tools
    assert apply.surfaces == ("tui", "cli")
    assert apply.when == "approved writes"
    # The parent gets the approval first (one clarify question), the child never asks.
    assert "clarify" in apply.description and "approved" in apply.description
    assert _denied(apply, "clarify", {"question": "Shall I?"})


def test_apply_result_contract_is_one_row_per_item(apply):
    body = apply.body
    for word in ("ok / failed / skipped", "reference", "count per status", "skipped"):
        assert word in body, word


@pytest.mark.parametrize("name, args", [
    (OP + "create_work_package", {"project": "DEMO", "subject": "Sample task 01"}),
    (OP + "update_work_package", {"id": 101, "status": "Done"}),
    (OP + "comment_work_package", {"id": 101, "comment": "Sample note"}),
    (OP + "create_work_package_relation", {"from_id": 101, "to_id": 102, "type": "relates"}),
    (OP + "create_time_entry", {"work_package_id": 101, "hours": 1.5}),
    (SUITE + "memory-memory_save", {"title": "Sample decision"}),
    (OP + "list_work_packages", {"project": "DEMO"}),
    (OP + "get_work_package", {"id": 101}),
    (HUB + "op_work_package", {"action": "update", "id": 101}),
    (OP + "update_work_package", {"id": 101, "dry_run": True}),
    (OP + "update_work_package", {"id": 101, "confirm": False}),
    ("tool_search", {"query": "openproject create"}),
])
def test_apply_allows_editable_reversible_writes_and_reads(apply, name, args):
    assert _denied(apply, name, args) is None


@pytest.mark.parametrize("name, args", [
    # never sends mail or chat messages
    (M365 + "send_email", {"to": "someone@example.com"}),
    (M365 + "send_chat_message", {"chat_id": "c1"}),
    ("mcp_claude_ai_Microsoft_365__teams_reply_channel_message", {}),
    ("send_message", {"target": "x"}),
    # never deletes or removes anything
    (OP + "delete_time_entry", {"id": 7}),
    (OP + "delete_work_package_relation", {"id": 7}),
    (M365 + "trash_email", {"id": "m1"}),
    (HUB + "op_work_package", {"action": "delete", "id": 101}),
    (SUITE + "memory-memory_manage", {"action": "delete", "slug": "x"}),
    # never confirms for the user
    (HUB + "px_confirm", {"token": "abc"}),
    (HUB + "fb_cancel", {"token": "abc"}),
])
def test_apply_never_sends_deletes_or_confirms(apply, name, args):
    reason = _denied(apply, name, args)
    assert reason and "only the user may confirm" in reason and "skipped" in reason


@pytest.mark.parametrize("args", [
    {"id": 101, "confirm": True},
    {"id": 101, "confirmed": "yes"},
    {"id": 101, "force": True},
    {"id": 101, "dry_run": False},
    {"id": 101, "dryRun": "false"},
    {"id": 101, "confirmation_token": "tok-1"},
])
def test_apply_never_skips_a_tools_confirmation_contract(apply, args):
    reason = _denied(apply, OP + "update_work_package", args)
    assert reason and "confirmation" in reason


@pytest.mark.parametrize("name", [
    # Hermes' own write tools: apply writes through MCP only
    "write_file", "patch", "terminal", "todo", "memory", "skill_manage", "cronjob",
    "execute_code", "delegate_task",
    # conservative extras of the definition: notifies people, moves, infra, accounts
    M365 + "create_event", OP + "create_meeting", M365 + "move_email", OP + "link_account",
    HUB + "k8s_set_image", HUB + "px_update_vm_config",
    # a generic proxy hides the real action
    HUB + "hub_call",
])
def test_apply_blocks_everything_else(apply, name):
    assert _denied(apply, name, {"tool": "mail_send"}) is not None


def test_the_floor_cannot_be_lifted_by_an_override():
    own = ad.parse_definition(
        "---\nname: apply\ndescription: My own apply.\nwrites: true\ntoolsets: [mcp, file]\n---\nDo it.",
        source="~/.hermes/agents/apply.md",
    )
    assert own.keeps_hermes_write_tools  # the user's own choice: file writes too
    assert ad.tool_call_denial(own, OP + "create_work_package", {}) is None  # no allow list
    assert ad.tool_call_denial(own, M365 + "send_email", {}) is not None
    assert ad.tool_call_denial(own, OP + "delete_time_entry", {}) is not None
    assert ad.tool_call_denial(own, OP + "update_work_package", {"confirm": True}) is not None


def test_the_tool_call_bridge_is_judged_by_its_underlying_tool(apply):
    def resolve(args):
        return args["name"], args.get("arguments") or {}, None

    with patch("tools.tool_search.resolve_underlying_call", side_effect=resolve):
        assert _denied(apply, "tool_call", {"name": OP + "create_work_package", "arguments": {}}) is None
        assert _denied(apply, "tool_call", {"name": M365 + "send_email", "arguments": {}})
    # Unresolvable bridge calls are not one of apply's actions.
    assert _denied(apply, "tool_call", {"name": "nope"})


# --------------------------------------------------------------------------- runtime guard


def test_the_guardrail_denies_without_halting_the_child(apply):
    import functools

    guard = ToolCallGuardrailController()
    guard.call_policy = functools.partial(ad.tool_call_denial, apply)
    denied = guard.before_call(M365 + "send_email", {"to": "x"})
    assert denied.action == "deny" and denied.code == "tool_rule_deny"
    assert not denied.allows_execution and not denied.should_halt
    allowed = guard.before_call(OP + "create_work_package", {"subject": "Sample task 01"})
    assert allowed.allows_execution
    # A rule that raises denies instead of allowing.
    guard.call_policy = lambda *_: 1 / 0
    assert not guard.before_call(OP + "create_work_package", {}).allows_execution


def test_the_executor_turns_a_denial_into_a_tool_result_and_goes_on(apply):
    """Both execution paths synthesize the denial as the call's result and do
    not record a turn halt (``_guardrail_block_result``)."""
    import functools

    import run_agent

    agent = SimpleNamespace(_tool_guardrail_halt_decision=None)
    agent._set_tool_guardrail_halt = lambda d: run_agent.AIAgent._set_tool_guardrail_halt(agent, d)
    guard = ToolCallGuardrailController()
    guard.call_policy = functools.partial(ad.tool_call_denial, apply)
    decision = guard.before_call(OP + "delete_time_entry", {"id": 7})
    result = run_agent.AIAgent._guardrail_block_result(agent, decision)
    assert "only the user may confirm" in result
    assert agent._tool_guardrail_halt_decision is None


# --------------------------------------------------------------------------- generated identity line


def test_delegation_line_is_generated_from_the_definitions(tmp_path):
    line = ad.delegation_line("tui", dirs=[ad.BUNDLED_AGENTS_DIR])
    assert line == (
        "Delegate bulk work and keep only the summary: apply = approved writes, "
        "collect/sweep = many similar reads, digest = large results, research = several sources."
    )
    cli = ad.delegation_line("cli", dirs=[ad.BUNDLED_AGENTS_DIR])
    assert "explore = finding code" in cli and "verify = running checks" in cli
    # A new kind with a principle shows up without touching the prompt code.
    (tmp_path / "triage.md").write_text(
        "---\nname: triage\ndescription: Sort incoming items.\nwhen: many new items\n---\nSort them.",
        encoding="utf-8",
    )
    assert "triage = many new items" in ad.delegation_line("tui", dirs=[tmp_path, ad.BUNDLED_AGENTS_DIR])
    assert ad.delegation_line("tui", dirs=[tmp_path / "empty"]) == ""


def test_identity_no_longer_hardcodes_kinds_or_a_context_size():
    from agent.prompt_builder import DEFAULT_AGENT_IDENTITY, build_delegation_guidance

    assert "64k" not in DEFAULT_AGENT_IDENTITY
    assert "task/explore/research" not in DEFAULT_AGENT_IDENTITY
    assert build_delegation_guidance({"read_file"}, "tui") == ""
    assert build_delegation_guidance({"delegate_task"}, "tui").startswith("Delegate bulk work")


def _stable(valid_tool_names, platform="tui"):
    from agent.system_prompt import build_system_prompt_parts

    agent = SimpleNamespace(
        load_soul_identity=False, skip_context_files=True, valid_tool_names=set(valid_tool_names),
        _task_completion_guidance=False, _tool_use_enforcement=False, _environment_probe=False,
        _kanban_worker_guidance="", _memory_store=None, _memory_manager=None, _user_profile_enabled=False,
        model="AIMDS-Suite-Auto", provider="aimds-suite-prod", platform=platform, pass_session_id=False,
        session_id="",
    )
    with (
        patch("run_agent.build_environment_hints", return_value=""),
        patch("run_agent.build_context_files_prompt", return_value=""),
        patch("run_agent.build_skills_system_prompt", return_value=""),
        patch("run_agent.build_mcp_status_prompt", return_value=""),
        patch("run_agent.build_m365_identity_prompt", return_value=""),
    ):
        return build_system_prompt_parts(agent)["stable"]


def test_the_line_is_in_the_prompt_only_with_delegate_task():
    assert "Delegate bulk work" in _stable({"delegate_task", "read_file"})
    assert "Delegate bulk work" not in _stable({"read_file"})
    assert "explore = finding code" in _stable({"delegate_task"}, platform="cli")
