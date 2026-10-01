"""A gateway that cuts the same request before the first byte (AIS-458).

An Octavia load balancer cut every LLM request at ~50 s while the model was
still before its first token. The turn loop retried the identical request six
times (3 retries, client rebuild, 3 more) without ever shrinking it. A repeated
cut at the same point must compress the context once and retry instead.
"""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import run_agent
from agent.stream_diag import pre_first_byte_cut_elapsed
from run_agent import AIAgent


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    import time as _time

    monkeypatch.setattr(_time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(run_agent, "jittered_backoff", lambda *a, **k: 0.0)


def _tool_defs():
    return [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "web_search tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]


def _ok_response(content):
    msg = SimpleNamespace(content=content, tool_calls=None, reasoning_content=None, reasoning=None)
    resp = SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")], model="test/model")
    resp.usage = None
    return resp


def _cut_failure(elapsed=50.4, **overrides):
    failure = {
        "error_type": "APIConnectionError",
        "chain": "APIConnectionError(Connection error.) <- RemoteProtocolError(Server disconnected without sending a response.)",
        "http_status": None,
        "bytes": 0,
        "chunks": 0,
        "elapsed": elapsed,
        "ttfb": None,
        "headers": {},
        "at": time.time(),
    }
    failure.update(overrides)
    return failure


@pytest.fixture()
def agent():
    with (
        patch("run_agent.get_tool_definitions", return_value=_tool_defs()),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        a = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://suite.example.com/litellm/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
        a.client = MagicMock()
        a._cached_system_prompt = "You are helpful."
        a._use_prompt_caching = False
        a.tool_delay = 0
        a.compression_enabled = True
        a.save_trajectories = False
        return a


def _large_history():
    # ~30k tokens: large enough that compression shortens the time to first token.
    return [
        {"role": "user", "content": "inventory " + "x" * 60_000},
        {"role": "assistant", "content": "noted " + "y" * 60_000},
    ]


def _cutting_side_effect(agent, outcomes):
    """Each outcome is a cut elapsed (float) or a final response."""
    queue = list(outcomes)

    def _call(*_args, **_kwargs):
        outcome = queue.pop(0)
        if isinstance(outcome, float):
            agent._last_stream_failure = _cut_failure(elapsed=outcome)
            raise ConnectionError("Connection error.")
        return outcome

    return _call


def _run(agent, outcomes):
    agent.client.chat.completions.create.side_effect = _cutting_side_effect(agent, outcomes)
    with (
        patch.object(agent, "_compress_context") as mock_compress,
        patch.object(agent, "_try_recover_primary_transport", return_value=False) as mock_recover,
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        mock_compress.return_value = ([{"role": "user", "content": "ja"}], "compressed prompt")
        result = agent.run_conversation("ja", conversation_history=_large_history())
    return result, mock_compress, mock_recover


def test_repeated_cut_compresses_once_and_retries(agent):
    result, mock_compress, _ = _run(agent, [50.4, 50.6, _ok_response("answered after compression")])

    mock_compress.assert_called_once()
    assert result["completed"] is True
    assert result["final_response"] == "answered after compression"


def test_single_cut_retries_without_compression(agent):
    result, mock_compress, _ = _run(agent, [50.4, _ok_response("transient")])

    mock_compress.assert_not_called()
    assert result["final_response"] == "transient"


def test_cuts_at_different_points_are_not_one_gateway_timeout(agent):
    result, mock_compress, _ = _run(agent, [50.4, 31.0, _ok_response("varied")])

    mock_compress.assert_not_called()
    assert result["final_response"] == "varied"


def test_gateway_cut_skips_the_client_rebuild(agent):
    # Small context: no compression, so the retries run out. Rebuilding the
    # client cannot outlast a gateway timeout and must not be attempted.
    agent.client.chat.completions.create.side_effect = _cutting_side_effect(agent, [50.4] * 10)
    with (
        patch.object(agent, "_compress_context") as mock_compress,
        patch.object(agent, "_try_recover_primary_transport", return_value=False) as mock_recover,
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("ja")

    mock_compress.assert_not_called()
    mock_recover.assert_not_called()
    assert result.get("failed") is True


class TestPreFirstByteCutElapsed:
    def test_detects_a_fresh_cut(self):
        assert pre_first_byte_cut_elapsed(_cut_failure(elapsed=50.6)) == pytest.approx(50.6)

    @pytest.mark.parametrize(
        "overrides",
        [
            {"bytes": 512},
            {"http_status": 502},
            {"elapsed": 0.3},
            {"chain": "APIConnectionError(Connection error.) <- ConnectError(connection refused)"},
            {"at": time.time() - 600},
        ],
    )
    def test_ignores_other_failures(self, overrides):
        assert pre_first_byte_cut_elapsed(_cut_failure(**overrides)) is None

    def test_ignores_missing_failure(self):
        assert pre_first_byte_cut_elapsed(None) is None
