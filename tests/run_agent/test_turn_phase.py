"""Structured turn phases for the desktop's wait indicator (AIS-460)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import run_agent
from run_agent import AIAgent


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    import time as _time

    monkeypatch.setattr(_time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(run_agent, "jittered_backoff", lambda *a, **k: 0.0)


def _ok(content):
    msg = SimpleNamespace(content=content, tool_calls=None, reasoning_content=None, reasoning=None)
    resp = SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")], model="m")
    resp.usage = None
    return resp


def _agent(phases):
    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://suite.example.com/litellm/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            turn_phase_callback=phases.append,
        )
    agent.client = MagicMock()
    agent._cached_system_prompt = "You are helpful."
    agent._use_prompt_caching = False
    agent.save_trajectories = False
    return agent


def _run(agent, side_effect):
    agent.client.chat.completions.create.side_effect = side_effect
    with (
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        return agent.run_conversation(
            "machen wir weiter",
            conversation_history=[{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}],
        )


def test_a_retried_connection_reports_waiting_retrying_waiting():
    phases = []
    agent = _agent(phases)

    result = _run(agent, [ConnectionError("Connection error."), _ok("done")])

    assert result["final_response"] == "done"
    names = [p["phase"] for p in phases]
    assert names[:3] == ["waiting", "retrying", "waiting"]
    first, retry, second = phases[:3]
    assert first["attempt"] == 1 and first["messages"] == 3
    assert retry["reason"] == "connection" and retry["attempt"] == 2
    assert second["attempt"] == 2 and second["max_attempts"] == first["max_attempts"]


def test_without_a_callback_nothing_breaks():
    agent = _agent([])
    agent.turn_phase_callback = None

    assert _run(agent, [_ok("fine")])["final_response"] == "fine"


# AIS-786: a mid-turn context summary ran 20-30 s with nothing on screen. Every
# pass now opens the "summarizing the conversation" phase and closes it again,
# however it ends, so the desktop can show and clear the sentence mid-turn.


def _summarizing_compressor() -> MagicMock:
    compressor = MagicMock()
    compressor.compress.return_value = [
        {"role": "user", "content": "[CONTEXT COMPACTION] summary"},
        {"role": "user", "content": "tail"},
    ]
    compressor.compression_count = 1
    compressor.last_prompt_tokens = 0
    compressor.last_completion_tokens = 0
    compressor._last_summary_error = None
    compressor._last_compress_aborted = False
    compressor._last_aux_model_failure_model = None
    compressor._last_aux_model_failure_error = None
    return compressor


def test_a_mid_turn_compression_starts_and_ends_the_summary_phase():
    phases = []
    agent = _agent(phases)
    agent.context_compressor = _summarizing_compressor()
    messages = [{"role": "user", "content": f"m{i}"} for i in range(20)]

    agent._compress_context(messages, "sys", approx_tokens=85_015)

    assert [p["phase"] for p in phases] == ["compressing", "compressed"]
    assert phases[0]["messages"] == 20


def test_the_summary_phase_ends_even_when_the_compression_fails():
    phases = []
    agent = _agent(phases)
    agent.context_compressor = _summarizing_compressor()
    agent.context_compressor.compress.side_effect = RuntimeError("summary model down")

    with pytest.raises(RuntimeError):
        agent._compress_context([{"role": "user", "content": "m"}] * 20, "sys", approx_tokens=85_015)

    assert [p["phase"] for p in phases] == ["compressing", "compressed"]


def test_an_aborted_compression_ends_the_summary_phase_too():
    phases = []
    agent = _agent(phases)
    compressor = _summarizing_compressor()
    compressor._last_compress_aborted = True
    compressor._last_summary_error = "summary model down"
    agent.context_compressor = compressor
    messages = [{"role": "user", "content": f"m{i}"} for i in range(20)]

    out, _ = agent._compress_context(messages, "sys", approx_tokens=85_015)

    assert out is messages
    assert [p["phase"] for p in phases] == ["compressing", "compressed"]
