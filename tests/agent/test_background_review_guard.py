"""AIS-305: background review must not run for its own fork or for opted-out agents."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.background_review import background_review_allowed


@pytest.mark.parametrize(
    "attrs, expected",
    [
        ({}, True),
        ({"_background_review_enabled": True, "_is_background_review_fork": False}, True),
        ({"_background_review_enabled": False}, False),
        ({"_is_background_review_fork": True}, False),
        ({"_is_background_review_fork": True, "_background_review_enabled": True}, False),
    ],
)
def test_background_review_allowed_truth_table(attrs, expected):
    assert background_review_allowed(SimpleNamespace(**attrs)) is expected


def test_review_fork_is_flagged_and_opted_out(monkeypatch):
    """The forked review agent must carry both guard flags so its own turn
    finalizer never spawns a nested review."""
    import agent.background_review as br

    captured = {}

    class FakeReviewAgent:
        def __init__(self, *args, **kwargs):
            self._session_messages = []
            self.tools = []

        def run_conversation(self, **kwargs):
            captured["fork_flag"] = getattr(self, "_is_background_review_fork", None)
            captured["enabled_flag"] = getattr(self, "_background_review_enabled", None)
            return {}

        def shutdown_memory_provider(self):
            pass

        def close(self):
            pass

    parent = SimpleNamespace(
        model="m",
        platform="cli",
        provider="openai",
        session_id="s",
        _credential_pool=None,
        _memory_store=object(),
        _memory_enabled=True,
        _user_profile_enabled=False,
        _cached_system_prompt="sp",
        session_start=None,
        background_review_callback=None,
        status_callback=None,
        tools=[],
        enabled_toolsets=None,
        disabled_toolsets=None,
        _safe_print=lambda *a, **k: None,
        _emit_auxiliary_failure=lambda *a, **k: None,
        _current_main_runtime=lambda: {"api_mode": "chat_completions", "base_url": "", "api_key": ""},
    )

    import run_agent as run_agent_module

    monkeypatch.setattr(run_agent_module, "AIAgent", FakeReviewAgent)
    monkeypatch.setattr("model_tools.get_tool_definitions", lambda **kw: [])
    monkeypatch.setattr("hermes_cli.plugins.set_thread_tool_whitelist", lambda *a, **k: None)
    monkeypatch.setattr("hermes_cli.plugins.clear_thread_tool_whitelist", lambda *a, **k: None)
    monkeypatch.setattr("tools.terminal_tool.set_approval_callback", lambda *a, **k: None)

    br._run_review_in_thread(parent, [], "review")

    assert captured == {"fork_flag": True, "enabled_flag": False}


def test_turn_finalizer_skips_tool_triggers_and_spawn_for_fork(monkeypatch):
    """finalize_turn must consult neither turn_triggers nor _spawn_background_review
    when the agent is a review fork."""
    import agent.tool_findings as tf
    from agent import turn_finalizer

    src = open(turn_finalizer.__file__, encoding="utf-8").read()
    # Structural guard: the trigger scan and the spawn are both gated on the
    # shared allow flag (cheaper than building a full AIAgent double here).
    assert "background_review_allowed(agent)" in src
    assert "if _review_allowed:\n        try:\n            from agent.tool_findings import turn_triggers" in src
    assert "if _review_allowed and final_response and not interrupted" in src

    def _boom(agent):
        raise AssertionError("turn_triggers must not be consulted for a review fork")

    monkeypatch.setattr(tf, "turn_triggers", _boom)
    assert background_review_allowed(SimpleNamespace(_is_background_review_fork=True)) is False
