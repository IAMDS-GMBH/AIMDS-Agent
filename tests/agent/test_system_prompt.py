"""Tests for agent/system_prompt.py — context-file cwd wiring."""

from types import SimpleNamespace
from unittest.mock import patch

from agent.system_prompt import build_system_prompt_parts
from hermes_cli.config import SINGLE_MCP_SERVER_NAME


def _make_agent(**overrides):
    base = dict(
        load_soul_identity=False,
        skip_context_files=False,
        valid_tool_names=[],
        _task_completion_guidance=False,
        _tool_use_enforcement=False,
        _environment_probe=False,
        _kanban_worker_guidance="",
        _memory_store=None,
        _memory_manager=None,
        model="",
        provider="",
        platform="",
        pass_session_id=False,
        session_id="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _captured_context_cwd(agent):
    """The cwd build_system_prompt_parts hands to build_context_files_prompt."""
    captured = {}

    def fake_context_files(cwd=None, skip_soul=False):
        captured["cwd"] = cwd
        return ""

    with (
        patch("run_agent.load_soul_md", return_value=""),
        patch("run_agent.build_environment_hints", return_value=""),
        patch("run_agent.build_context_files_prompt", side_effect=fake_context_files),
    ):
        build_system_prompt_parts(agent)
    return captured["cwd"]


class TestContextFileCwd:
    def test_none_when_terminal_cwd_unset(self, monkeypatch):
        # Unset → None, so discovery falls back to the launch dir inside
        # build_context_files_prompt (the local-CLI #19242 contract).
        monkeypatch.delenv("TERMINAL_CWD", raising=False)
        assert _captured_context_cwd(_make_agent()) is None

    def test_configured_dir_when_terminal_cwd_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        assert _captured_context_cwd(_make_agent()) == tmp_path


def _stable_prompt(agent):
    with (
        patch("run_agent.load_soul_md", return_value=""),
        patch("run_agent.build_environment_hints", return_value=""),
        patch("run_agent.build_context_files_prompt", return_value=""),
    ):
        return build_system_prompt_parts(agent)["stable"]


class TestCodingContextBlock:
    def test_injected_when_active(self, monkeypatch, tmp_path):
        import subprocess

        subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        agent = _make_agent(valid_tool_names=["read_file"], platform="cli")
        stable = _stable_prompt(agent)
        assert "coding agent" in stable
        assert "Workspace" in stable

    def test_absent_when_off(self, monkeypatch, tmp_path):
        import subprocess

        subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        agent = _make_agent(valid_tool_names=["read_file"], platform="cli")
        # Drive the real path: force the resolved mode to "off" via config.
        with patch("agent.coding_context._coding_mode", return_value="off"):
            stable = _stable_prompt(agent)
        assert "coding agent" not in stable

    def test_absent_without_tools(self, monkeypatch, tmp_path):
        import subprocess

        subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        agent = _make_agent(valid_tool_names=[], platform="cli")
        assert "coding agent" not in _stable_prompt(agent)


class TestRemoteMcpMemoryPrompt:
    _PREF_MCP_CONTEXT = f"mcp_{SINGLE_MCP_SERVER_NAME}_mcp_memory_memory_context"
    _PREF_MCP_SKILL_READ = f"mcp_{SINGLE_MCP_SERVER_NAME}_mcp_memory_skill_read"

    def test_uses_prefixed_remote_mcp_memory_context_tool_name(self):
        agent = _make_agent(valid_tool_names=[self._PREF_MCP_CONTEXT], platform="cli")
        stable = _stable_prompt(agent)
        assert "# Memory Context (mandatory)" in stable
        assert f"FIRST action in this session must be a call to `{self._PREF_MCP_CONTEXT}`" in stable
        assert "Do not emit pre-tool filler text" in stable
        assert "including `skill_view` and any memory read/list/search tools" in stable
        assert "asks for their name" in stable
        assert "primary source and call it before using any other memory tool" in stable
        assert "Use memory read/list/search tools only for explicit follow-up retrieval/editing tasks" in stable

    def test_uses_prefixed_mcp_memory_context_tool_name(self):
        agent = _make_agent(valid_tool_names=["mcp_memory_memory_context"], platform="cli")
        stable = _stable_prompt(agent)
        assert "# Memory Context (mandatory)" in stable
        assert "FIRST action in this session must be a call to `mcp_memory_memory_context`" in stable

    def test_not_emitted_when_memory_context_tool_missing(self):
        agent = _make_agent(valid_tool_names=["read_file"], platform="cli")
        stable = _stable_prompt(agent)
        assert "# Memory Context (mandatory)" not in stable

    def test_uses_mcp_memory_skill_read_for_init_hints_when_available(self):
        agent = _make_agent(
            valid_tool_names=[
                self._PREF_MCP_CONTEXT,
                self._PREF_MCP_SKILL_READ,
                "skill_view",
            ],
            platform="cli",
        )
        stable = _stable_prompt(agent)
        assert "init/onboarding skill" in stable
        assert f"`{self._PREF_MCP_SKILL_READ}`" in stable
        assert "not local `skill_view`" in stable

    def test_blocks_local_skill_view_init_when_mcp_skill_read_missing(self):
        agent = _make_agent(
            valid_tool_names=[
                self._PREF_MCP_CONTEXT,
                "skill_view",
            ],
            platform="cli",
        )
        stable = _stable_prompt(agent)
        assert "do not call local `skill_view` for init" in stable

    def test_requires_clarify_for_auto_init_onboarding_when_available(self):
        agent = _make_agent(
            valid_tool_names=[
                self._PREF_MCP_CONTEXT,
                self._PREF_MCP_SKILL_READ,
                "clarify",
            ],
            platform="cli",
        )
        stable = _stable_prompt(agent)
        assert "onboarding_question_flow_required=true" in stable
        assert "immediately call `clarify` with exactly one onboarding question" in stable
        assert "Do NOT output a multi-question plain-text list." in stable


class TestTodoPersistenceGuidance:
    def test_emits_in_main_prompt(self):
        stable = _stable_prompt(_make_agent(valid_tool_names=["todo"]))
        assert "If the `todo` tool is available, use it as the canonical place to store user todo tasks." in stable
        assert "power the Desktop Todos view" in stable

    def test_still_present_when_todo_tool_missing(self):
        stable = _stable_prompt(_make_agent(valid_tool_names=["read_file"]))
        assert "canonical place to store user todo tasks" in stable


class TestConfirmationRequiredEnforcement:
    """Universal (non-model-gated) clarify enforcement for confirmation_required
    tool results — see agent/prompt_builder.py::CONFIRMATION_REQUIRED_ENFORCEMENT.
    """

    def test_emitted_when_clarify_tool_active(self):
        agent = _make_agent(valid_tool_names=["clarify"], platform="cli")
        stable = _stable_prompt(agent)
        assert "# Confirmation-required tool results" in stable


class TestToolUseEnforcementWithToolSearch:
    def test_auto_enforces_when_tool_search_present_even_for_custom_model_alias(self):
        agent = _make_agent(
            valid_tool_names=["tool_search", "tool_call", "tool_describe"],
            _tool_use_enforcement="auto",
            model="AIMDS-Suite-Auto",
            platform="cli",
        )
        stable = _stable_prompt(agent)
        assert "# Tool-use enforcement" in stable
        assert "# Deferred tool search anti-hallucination directive" in stable
        assert "STRICT RULE: Only invoke function names that literally appear in your active JSON schema tools" in stable
        assert "Items listed under <available_skills> are NOT callable functions" in stable

    def test_auto_does_not_enforce_for_custom_model_alias_without_tool_search(self):
        agent = _make_agent(
            valid_tool_names=["read_file"],
            _tool_use_enforcement="auto",
            model="AIMDS-Suite-Auto",
            platform="cli",
        )
        stable = _stable_prompt(agent)
        assert "# Tool-use enforcement" not in stable

    def test_not_emitted_when_clarify_tool_missing(self):
        agent = _make_agent(valid_tool_names=["read_file"], platform="cli")
        stable = _stable_prompt(agent)
        assert "# Confirmation-required tool results" not in stable

    def test_emitted_regardless_of_unrecognized_model_alias(self):
        # A custom LiteLLM auto-router group name (e.g. from a SupraRouter
        # setup) never matches TOOL_USE_ENFORCEMENT_MODELS substrings, but
        # this guidance must still fire since it isn't model-gated.
        agent = _make_agent(
            valid_tool_names=["clarify"],
            model="AIMDS-Suite-Auto",
            platform="cli",
        )
        stable = _stable_prompt(agent)
        assert "# Confirmation-required tool results" in stable
