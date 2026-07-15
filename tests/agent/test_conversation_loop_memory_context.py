import json
from types import SimpleNamespace

from agent.conversation_loop import (
    _enforce_single_onboarding_clarify_question,
    _read_recent_onboarding_metadata_from_memory_context,
    _build_onboarding_context_line_from_recent_memory_context,
    _enforce_initial_memory_context_call,
)


def test_enforce_initial_memory_context_call_injects_tool_round(monkeypatch):
    captured = {}

    def _mock_execute_tool_calls(assistant_message, messages, effective_task_id, api_call_count):
        captured["effective_task_id"] = effective_task_id
        captured["api_call_count"] = api_call_count
        tc = assistant_message.tool_calls[0]
        captured["tool_name"] = tc.function.name
        captured["tool_args"] = json.loads(tc.function.arguments)
        messages.append(
            {
                "role": "tool",
                "name": tc.function.name,
                "tool_call_id": tc.id,
                "content": '{"context":"ok"}',
            }
        )

    emitted = []

    agent = SimpleNamespace(
        valid_tool_names={"mcp_IAMDS_mcp_memory_memory_context"},
        enabled_toolsets=None,
        disabled_toolsets=None,
        _emit_interim_assistant_message=lambda msg: emitted.append(msg),
        _execute_tool_calls=_mock_execute_tool_calls,
        log_prefix="",
    )
    messages = [{"role": "user", "content": "hello"}]

    _enforce_initial_memory_context_call(
        agent,
        messages=messages,
        conversation_history=[],
        original_user_message="hello",
        effective_task_id="t1",
    )

    assert captured["tool_name"] == "mcp_IAMDS_mcp_memory_memory_context"
    assert captured["tool_args"] == {}
    assert captured["effective_task_id"] == "t1"
    assert captured["api_call_count"] == 0
    assert len(messages) == 3
    assert messages[1]["role"] == "assistant"
    assert "Loading memory context" in messages[1]["content"]
    assert messages[1]["tool_calls"][0]["function"]["name"] == "mcp_IAMDS_mcp_memory_memory_context"
    assert json.loads(messages[1]["tool_calls"][0]["function"]["arguments"]) == {}
    assert messages[2]["role"] == "tool"
    assert messages[2]["name"] == "mcp_IAMDS_mcp_memory_memory_context"
    assert len(emitted) == 1
    assert getattr(agent, "_initial_memory_context_enforced", False) is True


def test_enforce_initial_memory_context_call_skips_non_first_turn(monkeypatch):
    called = False

    def _mock_execute_tool_calls(*_args, **_kwargs):
        nonlocal called
        called = True

    agent = SimpleNamespace(
        valid_tool_names={"mcp_IAMDS_mcp_memory_memory_context"},
        enabled_toolsets=None,
        disabled_toolsets=None,
        _emit_interim_assistant_message=lambda _msg: None,
        _execute_tool_calls=_mock_execute_tool_calls,
        log_prefix="",
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


def test_enforce_initial_memory_context_call_falls_back_to_error_on_execute_failure():
    agent = SimpleNamespace(
        valid_tool_names={"mcp_IAMDS_mcp_memory_memory_context"},
        enabled_toolsets=None,
        disabled_toolsets=None,
        _emit_interim_assistant_message=lambda _msg: None,
        _execute_tool_calls=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        log_prefix="",
    )
    messages = [{"role": "user", "content": "hello"}]

    _enforce_initial_memory_context_call(
        agent,
        messages=messages,
        conversation_history=[],
        original_user_message="hello",
        effective_task_id="t1",
    )

    assert messages[2]["role"] == "tool"
    assert "Initial memory_context call failed" in messages[2]["content"]


def test_build_onboarding_context_line_from_recent_memory_context_once():
    messages = [
        {
            "role": "tool",
            "name": "mcp_IAMDS_mcp_memory_memory_context",
            "content": json.dumps(
                {
                    "onboarding_init_context_required": True,
                    "onboarding_context_message": "Your profile is empty, so onboarding starts now.",
                },
                ensure_ascii=False,
            ),
        }
    ]

    line = _build_onboarding_context_line_from_recent_memory_context(
        messages=messages,
        valid_tool_names={"mcp_IAMDS_mcp_memory_memory_context"},
    )
    assert line == "Your profile is empty, so onboarding starts now."

    # The same tool result should not emit a second time.
    second = _build_onboarding_context_line_from_recent_memory_context(
        messages=messages,
        valid_tool_names={"mcp_IAMDS_mcp_memory_memory_context"},
    )
    assert second is None


def test_read_recent_onboarding_metadata_from_memory_context():
    messages = [
        {
            "role": "tool",
            "name": "mcp_IAMDS_mcp_memory_memory_context",
            "content": json.dumps(
                {
                    "onboarding_question_flow_required": True,
                    "onboarding_first_question": "What is your role/title?",
                },
                ensure_ascii=False,
            ),
        }
    ]
    payload = _read_recent_onboarding_metadata_from_memory_context(
        messages=messages,
        valid_tool_names={"mcp_IAMDS_mcp_memory_memory_context"},
    )
    assert payload is not None
    assert payload["onboarding_first_question"] == "What is your role/title?"


def test_enforce_single_onboarding_clarify_question_rewrites_multi_prompt():
    clarify_call = SimpleNamespace(
        function=SimpleNamespace(
            name="clarify",
            arguments=json.dumps(
                {
                    "question": (
                        "Please provide your role/title, preferred style, and any other details."
                    )
                },
                ensure_ascii=False,
            ),
        )
    )
    assistant_message = SimpleNamespace(tool_calls=[clarify_call])

    _enforce_single_onboarding_clarify_question(
        assistant_message,
        onboarding_payload={
            "onboarding_first_question": "What is your role/title?",
            "onboarding_questions": ["What is your role/title?", "What is your tech stack?"],
        },
    )

    args = json.loads(assistant_message.tool_calls[0].function.arguments)
    assert args["question"] == "What is your role/title?"
