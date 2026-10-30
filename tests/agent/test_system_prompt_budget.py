"""Token budget of the fixed system prompt (AIS-757, AIS-784).

AIS-740 caps compaction for Suite models at 80k prompt tokens; whatever the
fixed part (system prompt + tool schemas) takes is gone from every turn. This
builds the stable tier for a representative co-worker session — desktop/TUI
on AIMDS-Suite-Auto, the shipped SOUL.md, memory MCP, Microsoft 365 with Teams
and SharePoint, worktime tools, the tool_search bridge — and counts it with
the compression budget's own estimator (``estimate_tokens_rough``, ~4 chars
per token), so a regression shows up here before it shows up in a session.

Raise a budget only together with the reason in the commit message.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.model_metadata import estimate_tokens_rough

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SOUL = _REPO_ROOT / "installer" / "skills-hidden" / "aimds-loadout" / "identity" / "SOUL.md"
_VAULT = "/Users/jane.doe/Documents/AIMDS-Suite-Vault"

# AIS-784: after-trim size of the representative stable tier (9,033 tokens;
# 9,592 after AIS-757, 11,095 before it) plus ~3 % headroom. Skills index,
# environment hints, MCP status and the M365 identity line are
# environment-dependent and excluded.
SYSTEM_PROMPT_TOKEN_BUDGET = 9_300

# AIS-757: the trimmed blocks individually (after-trim size + ~4 % headroom),
# so a regression names the block that grew.
BLOCK_TOKEN_BUDGETS = {
    "soul": 810,         # 778 (was 1,408)
    "memory": 1_190,     # 1,144 incl. the workspace block (was 1,591)
    "teams": 885,        # 849 (was 1,157)
    "sharepoint": 555,   # 531 (was 650)
    # AIS-784: the remaining M365 blocks and the data-handling ladder (its
    # workdays rung now points to the workdays description).
    "outlook_memory": 170,       # 163 (was 236)
    "outlook_signature": 325,    # 311 (was 525)
    "contact_profiling": 245,    # 234 (was 292)
    "ai_attribution": 196,       # 188 (was 284)
    "data_handling": 760,        # 730 (was 847; the workdays rung 250 -> 133)
}
# AIS-757: the workdays tool schema rides along with every request (2,168
# tokens; 2,430 before).
WORKDAYS_SCHEMA_TOKEN_BUDGET = 2_250
# AIS-793: so does clarify; `questions` (several per ask) took it from 320 to
# 352 tokens.
CLARIFY_SCHEMA_TOKEN_BUDGET = 365

_M365 = "mcp_MSOffice365MCP_"
_MEMORY = "mcp_AIMDSSuiteMCP_mcp_memory-"
_TOOL_NAMES = frozenset(
    {
        "read_file", "write_file", "patch", "search_files", "terminal", "clarify", "memory",
        "session_search", "skills_list", "skill_view", "skill_manage", "todo", "cronjob",
        "workdays", "sql", "kb_search", "tool_search", "tool_call", "tool_describe",
        "office_word", "office_excel", "delegate_task",
    }
    | {
        _M365 + n
        for n in (
            "m365_list_accounts", "m365_generate_admin_consent_url", "m365_initiate_login",
            "m365_send_email", "m365_list_emails", "m365_get_email", "m365_download_email_attachments",
            "m365_list_calendars", "m365_list_events", "m365_create_event", "m365_list_chats",
            "m365_find_chat", "m365_send_chat_message", "m365_send_channel_message",
            "m365_download_drive_file", "m365_get_or_create_direct_chat", "m365_list_contacts",
            "m365_list_sharepoint_sites", "m365_list_sharepoint_drives", "m365_list_sharepoint_files",
            "m365_search_sharepoint_files", "m365_upload_sharepoint_file", "m365_create_sharepoint_folder",
            "m365_list_chat_messages", "m365_get_chat_style", "m365_download_chat_files",
            "m365_index_search", "m365_index_refresh", "m365_find_contact", "m365_get_my_signature",
            "m365_get_mail_style", "m365_move_email", "m365_trash_email", "m365_get_audit_log",
        )
    }
    | {
        _MEMORY + n
        for n in (
            "memory_context", "memory_save", "memory_search", "memory_read", "memory_list",
            "memory_manage", "memory_summarize_session", "skill",
        )
    }
)


@pytest.fixture
def representative(monkeypatch, tmp_path):
    import agent.prompt_builder as pb

    # A plain (non-git) working directory: the co-worker posture, not the
    # coding brief a repo checkout would add.
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", _VAULT)
    monkeypatch.setattr(pb, "resolve_agent_cwd", lambda: Path(_VAULT))
    return pb


def _stable_prompt() -> str:
    from agent.system_prompt import build_system_prompt_parts

    agent = SimpleNamespace(
        load_soul_identity=True,
        skip_context_files=False,
        valid_tool_names=set(_TOOL_NAMES),
        _task_completion_guidance=True,
        _tool_use_enforcement="auto",
        _environment_probe=False,
        _kanban_worker_guidance="",
        _memory_store=None,
        _memory_manager=None,
        _user_profile_enabled=False,
        model="AIMDS-Suite-Auto",
        provider="aimds-suite-prod",
        platform="tui",
        pass_session_id=False,
        session_id="",
    )
    with (
        patch("run_agent.load_soul_md", return_value=_SOUL.read_text(encoding="utf-8").strip()),
        patch("run_agent.build_environment_hints", return_value=""),
        patch("run_agent.build_context_files_prompt", return_value=""),
        patch("run_agent.build_skills_system_prompt", return_value=""),
        patch("run_agent.build_mcp_status_prompt", return_value=""),
        patch("run_agent.build_m365_identity_prompt", return_value=""),
    ):
        return build_system_prompt_parts(agent)["stable"]


def test_representative_system_prompt_stays_within_budget(representative):
    stable = _stable_prompt()
    tokens = estimate_tokens_rough(stable)
    assert tokens <= SYSTEM_PROMPT_TOKEN_BUDGET, (
        f"stable system prompt is {tokens:,} tokens, budget {SYSTEM_PROMPT_TOKEN_BUDGET:,} (AIS-757)"
    )
    # The representative setup really carries the trimmed blocks.
    for heading in (
        "# Memory vault & storage strategy",
        "# Workspace (Obsidian vault)",
        "# Teams: send to a person without guessing",
        "# SharePoint: resolve the site, then act",
        "Executive Principal & Chief of Staff",
        "# Outlook/M365 + Memory",
        "# Outlook/M365: HTML formatting, signature & per-contact tone",
        "# Outlook/M365: opt-in contact profiling",
        "# AI attribution footer",
        "# Data handling — preferred path first",
    ):
        assert heading in stable, heading


def test_trimmed_blocks_stay_within_their_budgets(representative):
    pb = representative
    blocks = {
        "soul": _SOUL.read_text(encoding="utf-8").strip(),
        "memory": pb.build_remote_mcp_memory_prompt(_TOOL_NAMES),
        "teams": pb.build_teams_send_guidance(_TOOL_NAMES),
        "sharepoint": pb.build_sharepoint_guidance(_TOOL_NAMES),
        "outlook_memory": pb.build_outlook_memory_guidance(_TOOL_NAMES),
        "outlook_signature": pb.build_outlook_signature_guidance(_TOOL_NAMES),
        "contact_profiling": pb.build_outlook_contact_profiling_guidance(_TOOL_NAMES),
        "ai_attribution": pb.build_ai_attribution_guidance(_TOOL_NAMES),
        "data_handling": pb.build_data_handling_guidance(_TOOL_NAMES),
    }
    over = {
        name: (estimate_tokens_rough(text), BLOCK_TOKEN_BUDGETS[name])
        for name, text in blocks.items()
        if estimate_tokens_rough(text) > BLOCK_TOKEN_BUDGETS[name]
    }
    assert not over, f"blocks over budget (tokens, budget): {over}"


def test_workdays_schema_stays_within_budget():
    from tools.workdays_tool import WORKDAYS_SCHEMA

    # Same accounting as estimate_request_tokens_rough: str() of the tool entry.
    tokens = estimate_tokens_rough(str({"type": "function", "function": WORKDAYS_SCHEMA}))
    assert tokens <= WORKDAYS_SCHEMA_TOKEN_BUDGET, tokens


def test_clarify_schema_stays_within_budget():
    from tools.clarify_tool import CLARIFY_SCHEMA

    tokens = estimate_tokens_rough(str({"type": "function", "function": CLARIFY_SCHEMA}))
    assert tokens <= CLARIFY_SCHEMA_TOKEN_BUDGET, tokens


def test_trim_kept_the_behavioural_rules(representative):
    """The trim removed repetition and recipes, not rules: approvals, the
    user's language, safety guardrails and the hard mail, Teams and
    SharePoint rules are all still in the representative prompt."""
    stable = _stable_prompt()
    for rule in (
        # SOUL guardrails and communication
        "Never dispatch emails, messages, or external mutations without explicit user approval",
        "Never delete files, drop database tables, or purge records without explicit user confirmation",
        "never exfiltrate credentials",
        "I answer in the user's language",
        "I never re-propose an action the user just declined",
        # memory vault
        "exactly ONCE",
        "do NOT repeat it every turn",
        "Act only after the user confirms; never silently filter or delete.",
        "Do NOT output a multi-question plain-text list.",
        # workspace
        "never invent or guess paths",
        "UPDATE it instead of adding variants",
        # Teams
        "Never take a chat_id from memory",
        "never pick silently",
        "wait for approval, then send the identical Markdown",
        "no technical detail about how you got the information",
        "never fetch it with curl or a script",
        # SharePoint
        "get approval, then upload",
        "only when the user explicitly asked to overwrite",
        "do not narrate a plan in prose instead of calling the tool",
        "no workaround with credentials found elsewhere",
        # AIS-784: Outlook memory
        "save a short `notes` entry via",
        "promising a check you cannot perform",
        "alone is not enough",
        # signature and per-contact tone
        "as HTML, never plain text",
        "do not add the email signature",
        "Signature (MANDATORY, derive once, reuse silently)",
        "with `confidence: low` ask the user once instead of guessing",
        "Draft the email only after this step",
        "never assume the same greeting/register for every recipient",
        # contact profiling
        "offer once via the `clarify` tool (yes/no choices in the user's language)",
        "so you never ask again",
        "NEVER bulk-import the full Outlook/Org contact directory",
        "This is a one-time backfill",
        # AI attribution
        "on its own line right after the signature/closing",
        "the default is NO attribution line",
        "adapted to the conversation's language",
        "use it instead of the default",
        # data handling, workdays rung: the rest lives in the workdays description
        "follow the rules in its description",
        "pick the action that answers the question in one call",
        "Never type week numbers",
        "confirm it instead of answering with incomplete numbers",
        # AIS-793: open decisions are questions, not a prose list
        "one `questions` entry per decision",
    ):
        assert rule in stable, rule


def test_workdays_schema_kept_its_rules_and_fields():
    from tools.workdays_tool import ACTIONS, WORKDAYS_SCHEMA

    description = WORKDAYS_SCHEMA["description"]
    for rule in (
        "NEVER type calendars",
        "never compute Easter yourself",
        "never write that file yourself",
        "CONFIRM before configure",
        "region is never estimated",
        "never assume a state or week model",
        "partial_holidays_unresolved",
        "Present results in the user's language",
    ):
        assert rule in description, rule
    for action in ACTIONS:
        assert f"'{action}'" in description, action
    props = WORKDAYS_SCHEMA["parameters"]["properties"]
    assert "Never set without asking the user" in props["partial_holidays"]["description"]
    assert "reports/worklog/" in props["write"]["description"]
