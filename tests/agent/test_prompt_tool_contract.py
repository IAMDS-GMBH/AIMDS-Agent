"""The prompt names only tools the session has.

Three failures this guards against, all observed in ~/.hermes/state.db:

* the prompt demanded `sql` in three places while no TUI/cron session had
  the tool (77 Python fallbacks, 0 sql calls),
* `skill_view` / `tool_search` were referenced in sessions whose toolset
  lacked them (18 rejected calls),
* `obsidian_search` / `obsidian_read_file` were instructed although no such
  tools exist anywhere.

The test builds the stable system prompt for the shipped default session
(installer defaults → `_get_platform_tools("cli")` → tool definitions) and
checks every backticked identifier that looks like a tool call against the
tools that session actually has. Guidance that names a tool must be gated
on it; a name nobody registered must not appear at all.
"""

from __future__ import annotations

import os
import re
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Names the prompt may mention that are not tools of the default session:
# bridge tools (present whenever tool_search is active), the vault fallback
# tool (present only when the MCP is absent), MCP tools that arrive at
# runtime from the primary memory server, and generic illustrations.
_ALLOWED_NON_SESSION = {
    "tool_search", "tool_describe", "tool_call",
    "vault_memory",
    "memory_save", "memory_context", "memory_search", "memory_summarize_session", "kb_search",
    "skills_read",  # legacy reader name kept as an alternative in the skills preamble
}

_CALL_RE = re.compile(r"`([a-z][a-z0-9_]{2,})\(")          # `name(` — an instruction to call it
_BACKTICK_RE = re.compile(r"`([a-z][a-z0-9_]{2,})`")        # `name` — a bare mention


def _default_session_tool_names() -> set[str]:
    from hermes_cli.tools_config import _get_platform_tools
    from installer.scripts.upsert_aimds_defaults import upsert_aimds_defaults
    import model_tools

    cfg = upsert_aimds_defaults({})
    enabled = sorted(_get_platform_tools(cfg, "cli", include_default_mcp_servers=True))
    defs = model_tools.get_tool_definitions(
        enabled_toolsets=enabled, quiet_mode=True, skip_tool_search_assembly=True,
    )
    return {td["function"]["name"] for td in defs}


def _stable_prompt(valid_tool_names: set[str]) -> str:
    from agent.system_prompt import build_system_prompt_parts

    agent = SimpleNamespace(
        load_soul_identity=True,
        skip_context_files=True,
        valid_tool_names=set(valid_tool_names),
        _task_completion_guidance=True,
        _tool_use_enforcement="auto",
        _environment_probe=False,
        _kanban_worker_guidance="",
        _memory_store=None,
        _memory_manager=None,
        _user_profile_enabled=False,
        model="AIMDS-Suite-Auto",
        provider="",
        platform="cli",
        pass_session_id=False,
        session_id="",
    )
    soul = open(os.path.join(_REPO_ROOT, "installer", "skills-hidden", "aimds-loadout", "identity", "SOUL.md")).read()
    with (
        patch("run_agent.load_soul_md", return_value=soul),
        patch("run_agent.build_context_files_prompt", return_value=""),
        patch("run_agent.build_skills_system_prompt", return_value=""),
    ):
        return build_system_prompt_parts(agent)["stable"]


@pytest.fixture(scope="module")
def session_tools():
    return _default_session_tool_names()


def test_default_session_has_the_tools_the_prompt_relies_on(session_tools):
    assert "sql" in session_tools, "sql must be in every default session (it was in none)"
    for name in ("read_file", "search_files", "skill_view", "skills_list", "terminal"):
        assert name in session_tools


def test_every_tool_the_prompt_tells_the_model_to_call_exists_in_the_session(session_tools):
    from tools.registry import registry

    names = set(session_tools) | {"tool_search", "tool_describe", "tool_call"}
    prompt = _stable_prompt(names)
    registered = {e.name for e in registry._snapshot_entries()}

    offenders = set()
    for token in _CALL_RE.findall(prompt):
        if token in names or token in _ALLOWED_NON_SESSION:
            continue
        offenders.add(token)
    assert not offenders, f"prompt instructs calls to tools the session does not have: {sorted(offenders)}"

    # Bare mentions of *registered core* tools must also be present in the
    # session — a registered tool that is mentioned but absent is the `sql`
    # case. Deferrable tools (office_*, MCP tools) may be mentioned: they are
    # reachable through tool_search even when not in the array.
    from tools.tool_search import is_deferrable_tool_name

    mentioned_registered = {t for t in _BACKTICK_RE.findall(prompt) if t in registered}
    missing = {
        t for t in mentioned_registered - names - _ALLOWED_NON_SESSION
        if not is_deferrable_tool_name(t)
    }
    assert not missing, f"prompt mentions registered tools the session lacks: {sorted(missing)}"


def test_phantom_tools_are_gone(session_tools):
    prompt = _stable_prompt(set(session_tools) | {"tool_search"})
    for phantom in ("obsidian_search", "obsidian_read_file", "search_tool("):
        assert phantom not in prompt, phantom


def test_guidance_is_gated_on_the_tools_it_names(session_tools):
    """Take skill_view / sql / tool_search away and the sentences that name them go too."""
    base = set(session_tools)
    with_all = _stable_prompt(base | {"tool_search"})
    assert "skill_view(name='hermes-agent')" in with_all
    assert "`sql`" in with_all
    assert "tool_search" in with_all

    without_skills = _stable_prompt((base - {"skill_view", "skills_list", "skill_manage"}) | {"tool_search"})
    assert "skill_view(" not in without_skills

    without_sql = _stable_prompt((base - {"sql"}) | {"tool_search"})
    assert "`sql` tool is not in this session" in without_sql
    assert "FROM mcp_records" not in without_sql

    without_search = _stable_prompt(base - {"tool_search"})
    assert "tool_search(" not in without_search
    assert "# Deferred tool search" not in without_search


def test_no_prohibition_without_an_available_alternative(session_tools):
    prompt = _stable_prompt(set(session_tools) | {"tool_search"})
    for banned in ("NEVER write throwaway Python", "STRICT PROHIBITION", "IMMEDIATELY callable", "PRIMARY, default"):
        assert banned not in prompt, banned
    assert "# Data handling — preferred path first" in prompt
