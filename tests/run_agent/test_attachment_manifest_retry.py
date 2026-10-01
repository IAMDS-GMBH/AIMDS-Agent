"""AIS-456: a turn whose request with large inline attachments was cut off
gets exactly one more attempt with the attachments as a file manifest."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import run_agent
from agent.context_references import ATTACHED_FILES_MARKER
from run_agent import AIAgent


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    import time as _time

    monkeypatch.setattr(_time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(run_agent, "jittered_backoff", lambda *a, **k: 0.0)


@pytest.fixture()
def agent():
    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        a = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
        a.client = MagicMock()
        a._cached_system_prompt = "You are helpful."
        a._use_prompt_caching = False
        a.tool_delay = 0
        a.compression_enabled = False
        a.save_trajectories = False
        return a


def _ok(content="Extract ready"):
    msg = SimpleNamespace(content=content, tool_calls=None, reasoning_content=None, reasoning=None)
    resp = SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")], model="test/model")
    resp.usage = None
    return resp


def _expanded_message(files=3):
    body = "x IN TXT v=spf1 include:example\n" * 300
    blocks = "\n\n".join(
        f"📄 @file:zones/z{i}.txt (2400 tokens)\n```text\n{body}\n```" for i in range(files)
    )
    return f"Sind doch mehr geworden\n\n--- Attached Context ---\n\n{blocks}"


def _last_user_text(kwargs):
    messages = kwargs.get("messages") or []
    user = [m for m in messages if m.get("role") == "user"][-1]
    content = user.get("content")
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content or ""


def _run(agent, side_effect):
    agent.client.chat.completions.create.side_effect = side_effect
    with (
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
        patch.object(agent, "_dump_api_request_debug", return_value=None),
    ):
        return agent.run_conversation(_expanded_message())


def test_cut_off_attachments_retry_once_as_a_manifest(agent):
    seen = []

    def provider(*_a, **kwargs):
        text = _last_user_text(kwargs)
        seen.append(ATTACHED_FILES_MARKER in text)
        if ATTACHED_FILES_MARKER in text:
            assert "Sind doch mehr geworden" in text and "@file:zones/z2.txt" in text
            assert "include:example\nx IN TXT" not in text
            return _ok()
        raise ConnectionError("Connection error.")

    result = _run(agent, provider)
    assert result.get("final_response") == "Extract ready"
    assert seen[-1] is True and seen.count(True) == 1 and seen[0] is False


def test_the_manifest_retry_happens_only_once(agent):
    calls = {"manifest": 0}

    def provider(*_a, **kwargs):
        if ATTACHED_FILES_MARKER in _last_user_text(kwargs):
            calls["manifest"] += 1
        raise ConnectionError("Connection error.")

    result = _run(agent, provider)
    assert result.get("failed") is True
    assert calls["manifest"] >= 1
    # every manifest attempt belongs to the single extra round
    assert agent._attachments_manifest_turn is not None


def test_no_retry_without_inline_attachments(agent):
    agent.client.chat.completions.create.side_effect = ConnectionError("Connection error.")
    with (
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
        patch.object(agent, "_dump_api_request_debug", return_value=None),
    ):
        result = agent.run_conversation("just a question")
    assert result.get("failed") is True
    assert getattr(agent, "_attachments_manifest_turn", None) is None


def test_error_message_says_cut_off_when_the_suite_was_up(agent, monkeypatch):
    monkeypatch.setenv("HERMES_LANGUAGE", "en")
    from agent import i18n

    i18n.reset_language_cache()
    agent._last_suite_liveness = "up"
    text = agent._format_user_friendly_api_error("Connection error.", 3, provider="aimds-suite-prod", model="AIMDS-Suite-Auto")
    assert "is reachable" in text and "(aimds-suite-prod / AIMDS-Suite-Auto)" in text and "3 attempts" in text
    agent._last_suite_liveness = ""
    text = agent._format_user_friendly_api_error("Connection error.", 3, provider="aimds-suite-prod", model="AIMDS-Suite-Auto")
    assert "nicht hergestellt" in text  # unchanged path when nothing is known
