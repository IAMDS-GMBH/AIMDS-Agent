import json
from types import SimpleNamespace

from agent.conversation_loop import _enforce_initial_memory_context_call


def test_enforce_initial_memory_context_call_injects_tool_round(monkeypatch):
    captured = {}

    def _mock_handle_function_call(**kwargs):
        captured.update(kwargs)
        return '{"context":"ok"}'

    monkeypatch.setattr(
        "agent.conversation_loop._ra",
        lambda: SimpleNamespace(handle_function_call=_mock_handle_function_call),
    )

    agent = SimpleNamespace(
        valid_tool_names={"mcp_IAMDS_mcp_memory_memory_context"},
        enabled_toolsets=None,
        disabled_toolsets=None,
    )
    messages = [{"role": "user", "content": "hello"}]

    _enforce_initial_memory_context_call(
        agent,
        messages=messages,
        conversation_history=[],
        original_user_message="hello",
        effective_task_id="t1",
    )

    assert captured["function_name"] == "mcp_IAMDS_mcp_memory_memory_context"
    assert captured["function_args"] == {"query": "hello"}
    assert len(messages) == 3
    assert messages[1]["role"] == "assistant"
    assert messages[1]["tool_calls"][0]["function"]["name"] == "mcp_IAMDS_mcp_memory_memory_context"
    assert json.loads(messages[1]["tool_calls"][0]["function"]["arguments"]) == {"query": "hello"}
    assert messages[2]["role"] == "tool"
    assert messages[2]["name"] == "mcp_IAMDS_mcp_memory_memory_context"
    assert getattr(agent, "_initial_memory_context_enforced", False) is True


def test_enforce_initial_memory_context_call_skips_non_first_turn(monkeypatch):
    called = False

    def _mock_handle_function_call(**_kwargs):
        nonlocal called
        called = True
        return '{"context":"ok"}'

    monkeypatch.setattr(
        "agent.conversation_loop._ra",
        lambda: SimpleNamespace(handle_function_call=_mock_handle_function_call),
    )

    agent = SimpleNamespace(
        valid_tool_names={"mcp_IAMDS_mcp_memory_memory_context"},
        enabled_toolsets=None,
        disabled_toolsets=None,
    )
    messages = [{"role": "user", "content": "hello"}]

    _enforce_initial_memory_context_call(
        agent,
        messages=messages,
        conversation_history=[{"role": "assistant", "content": "previous"}],
        original_user_message="hello",
        effective_task_id="t1",
    )

    assert called is False
    assert len(messages) == 1
