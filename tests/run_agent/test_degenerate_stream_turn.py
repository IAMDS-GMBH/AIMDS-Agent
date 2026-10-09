"""AIS-524: a degenerated stream gets one fresh attempt, then the fallback
model, then a clear end of the turn (SUP-20261007-172008)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.stream_degeneration import DegenerateStreamError, Degeneration
from run_agent import AIAgent


def _make_agent() -> AIAgent:
    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("hermes_cli.config.load_config", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            max_iterations=5,
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
    agent.client = MagicMock()
    agent._cached_system_prompt = "You are helpful."
    agent._use_prompt_caching = False
    agent.compression_enabled = False
    agent.save_trajectories = False
    return agent


def _ok(content="done"):
    msg = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg, finish_reason="stop")], model="test/model", usage=None,
    )


def _loop_error():
    return DegenerateStreamError(Degeneration(reason="counting", channel="reasoning", chars=1800))


@pytest.fixture(autouse=True)
def _german(monkeypatch):
    from agent.i18n import reset_language_cache

    monkeypatch.setenv("HERMES_LANGUAGE", "de")
    monkeypatch.setattr(AIAgent, "_spawn_background_review", lambda self, *a, **k: None)
    reset_language_cache()
    yield
    reset_language_cache()


def _run(agent, side_effect, *, fallback=False, explanation="", answer=None):
    statuses = []
    agent._emit_status = lambda text, *a, **k: statuses.append(text)
    agent.clarify_callback = (lambda question, choices: choices[0] if answer == "yes" else choices[1]) if answer else None
    with (
        patch.object(agent, "_interruptible_api_call", side_effect=side_effect) as api,
        patch.object(agent, "_request_toolless_answer", return_value=explanation),
        patch.object(agent, "_try_activate_fallback", return_value=fallback) as fb,
        patch("hermes_cli.auto_incidents.report_in_background") as incident,
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("buche meine Zeiten")
    return result, statuses, api, fb, incident


def test_one_loop_is_retried_once_and_the_turn_continues():
    agent = _make_agent()
    result, statuses, api, fb, incident = _run(agent, [_loop_error(), _ok()])

    assert api.call_count == 2
    assert result["final_response"] == "done"
    assert any("wiederholen" in s for s in statuses)
    fb.assert_not_called()
    incident.assert_not_called()


def test_second_loop_without_fallback_ends_the_turn_with_a_notice():
    agent = _make_agent()
    result, statuses, api, fb, incident = _run(agent, [_loop_error(), _loop_error(), _ok()])

    assert api.call_count == 2
    assert result["failed"] is True
    assert result["failure_reason"] == "degenerate_stream"
    # No model explanation: the fixed, translated text; no user to ask: no report.
    assert result["final_response"].startswith("Die Antwort hat sich immer wieder wiederholt")
    assert result["messages"][-1] == {"role": "assistant", "content": result["final_response"]}
    fb.assert_called_once()
    incident.assert_not_called()


def test_the_model_explains_and_the_user_decides_about_the_report():
    agent = _make_agent()
    explanation = (
        "Meine Antwort ist in eine Schleife geraten, deshalb habe ich abgebrochen. "
        "Frag bitte noch einmal.\n---SUPPORT---\nReasoning degenerated into counting twice."
    )
    result, _, _, _, incident = _run(
        agent, [_loop_error(), _loop_error()], explanation=explanation, answer="yes",
    )
    assert result["final_response"].startswith("Meine Antwort ist in eine Schleife geraten")
    incident.assert_called_once()
    assert incident.call_args.args[0].startswith("degenerate-stream-")
    assert incident.call_args.args[2].startswith("Reasoning degenerated into counting twice")
    assert incident.call_args.kwargs["context_type"] == "degenerate_stream"


def test_no_report_when_the_user_says_no():
    agent = _make_agent()
    _, _, _, _, incident = _run(agent, [_loop_error(), _loop_error()], answer="no")
    incident.assert_not_called()


def test_a_degenerated_explanation_falls_back_to_the_fixed_text():
    agent = _make_agent()
    looping = "Ich buche jetzt die Zeit. " * 200
    result, *_ = _run(agent, [_loop_error(), _loop_error()], explanation=looping)
    assert result["final_response"].startswith("Die Antwort hat sich immer wieder wiederholt")


def test_second_loop_switches_to_the_fallback_model():
    agent = _make_agent()
    result, statuses, api, fb, incident = _run(
        agent, [_loop_error(), _loop_error(), _ok("vom Ersatzmodell")], fallback=True,
    )

    assert api.call_count == 3
    assert result["final_response"] == "vom Ersatzmodell"
    fb.assert_called_once()
    incident.assert_not_called()
