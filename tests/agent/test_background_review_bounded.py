"""AIS-740: the background review replays a bounded history, not the full transcript."""

from __future__ import annotations

import json
from types import SimpleNamespace

from agent.background_review import REVIEW_HISTORY_TOKEN_BUDGET, bounded_review_history
from agent.model_metadata import estimate_messages_tokens_rough


def _round(i: int, result_chars: int = 20_000) -> list:
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": f"c{i}", "type": "function", "function": {"name": "terminal", "arguments": json.dumps({"command": f"ls {i}"})}}
            ],
        },
        {"role": "tool", "tool_call_id": f"c{i}", "name": "terminal", "content": "o" * result_chars},
    ]


def _turn(i: int, rounds: int = 3) -> list:
    msgs = [{"role": "user", "content": f"task {i}"}]
    for r in range(rounds):
        msgs += _round(i * 100 + r)
    msgs.append({"role": "assistant", "content": f"done {i}"})
    return msgs


def _assert_well_formed(history: list) -> None:
    assert history[0]["role"] == "user"
    call_ids = {
        tc["id"]
        for m in history
        if m.get("role") == "assistant"
        for tc in (m.get("tool_calls") or [])
    }
    for m in history:
        if m.get("role") == "tool":
            assert m["tool_call_id"] in call_ids, "tool result without its call"


def test_short_history_passes_through_unchanged():
    history = _turn(1, rounds=1)
    history[2]["content"] = "short"
    assert bounded_review_history(history) == history


def test_long_session_is_bounded_to_the_budget():
    snapshot = [m for i in range(12) for m in _turn(i)]  # ~180k rough tokens
    assert estimate_messages_tokens_rough(snapshot) > 5 * REVIEW_HISTORY_TOKEN_BUDGET
    history = bounded_review_history(snapshot)
    assert estimate_messages_tokens_rough(history) <= REVIEW_HISTORY_TOKEN_BUDGET + 200
    _assert_well_formed(history)
    # The latest turn is in the excerpt, the omission is stated up front.
    assert history[-1]["content"] == "done 11"
    assert history[0]["content"].startswith("[Earlier turns of this conversation are omitted")
    # The snapshot itself is never mutated.
    assert snapshot[2]["content"] == "o" * 20_000


def test_one_turn_longer_than_the_budget_keeps_its_user_message():
    snapshot = _turn(1, rounds=60)  # one long tool loop, ~300k rough tokens
    history = bounded_review_history(snapshot)
    assert estimate_messages_tokens_rough(history) <= REVIEW_HISTORY_TOKEN_BUDGET + 200
    _assert_well_formed(history)
    assert history[0]["role"] == "user" and history[0]["content"].endswith("task 1")
    assert history[1]["role"] == "assistant"
    assert history[-1]["content"] == "done 1"


def test_bounding_is_deterministic():
    snapshot = [m for i in range(8) for m in _turn(i)]
    assert bounded_review_history(snapshot) == bounded_review_history(snapshot)


def test_review_fork_gets_bounded_history_and_no_forced_memory_loads(monkeypatch):
    import agent.background_review as br
    import run_agent as run_agent_module

    captured = {}

    class FakeReviewAgent:
        def __init__(self, *args, **kwargs):
            self._session_messages = []
            self.tools = []

        def run_conversation(self, **kwargs):
            captured["history"] = kwargs["conversation_history"]
            captured["enforce_initial"] = getattr(self, "_enforce_initial_memory_context", None)
            captured["enforce_personal"] = getattr(self, "_enforce_context_for_personal_queries", None)
            captured["prompt_date"] = getattr(self, "_cached_system_prompt_date", None)
            captured["system_prompt"] = getattr(self, "_cached_system_prompt", None)
            return {}

        def shutdown_memory_provider(self):
            pass

        def close(self):
            pass

    parent = SimpleNamespace(
        model="AIMDS-Suite-Auto",
        platform="tui",
        provider="aimds-suite-prod",
        session_id="s",
        _credential_pool=None,
        _memory_store=object(),
        _memory_enabled=True,
        _user_profile_enabled=False,
        _cached_system_prompt="sp",
        _cached_system_prompt_date="2026-10-22",
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
    monkeypatch.setattr(run_agent_module, "AIAgent", FakeReviewAgent)
    monkeypatch.setattr("model_tools.get_tool_definitions", lambda **kw: [])
    monkeypatch.setattr("hermes_cli.plugins.set_thread_tool_whitelist", lambda *a, **k: None)
    monkeypatch.setattr("hermes_cli.plugins.clear_thread_tool_whitelist", lambda *a, **k: None)
    monkeypatch.setattr("tools.terminal_tool.set_approval_callback", lambda *a, **k: None)

    snapshot = [m for i in range(12) for m in _turn(i)]
    br._run_review_in_thread(parent, snapshot, "Review the memory vault")

    assert estimate_messages_tokens_rough(captured["history"]) <= REVIEW_HISTORY_TOKEN_BUDGET + 200
    assert captured["enforce_initial"] is False
    assert captured["enforce_personal"] is False
    # The inherited prompt keeps its date, so the fork does not rebuild it.
    assert captured["system_prompt"] == "sp"
    assert captured["prompt_date"] == "2026-10-22"
