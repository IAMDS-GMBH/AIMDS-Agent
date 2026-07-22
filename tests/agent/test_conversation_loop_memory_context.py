import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from agent.conversation_loop import (
    _enforce_single_onboarding_clarify_question,
    _read_recent_onboarding_metadata_from_memory_context,
    _build_onboarding_context_line_from_recent_memory_context,
    _apply_onboarding_clarify_context,
    _enforce_initial_memory_context_call,
    _memory_context_payload_needs_workspace_hydration,
    _resume_onboarding_clarify_if_needed,
    _has_memory_save_after_onboarding_answers,
    _maybe_translate_onboarding_prompts_via_llm,
    _parse_json_object_from_text,
    _build_stale_project_row,
    _sanitize_onboarding_context_line_text,
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
        _enforce_initial_memory_context=True,
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
    assert messages[1]["content"] == ""
    assert messages[1]["tool_calls"][0]["function"]["name"] == "mcp_IAMDS_mcp_memory_memory_context"
    assert json.loads(messages[1]["tool_calls"][0]["function"]["arguments"]) == {}
    assert messages[2]["role"] == "tool"
    assert messages[2]["name"] == "mcp_IAMDS_mcp_memory_memory_context"
    assert len(emitted) == 0
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
        _enforce_initial_memory_context=True,
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


def test_memory_context_payload_needs_workspace_hydration_detects_stale_and_missing():
    assert _memory_context_payload_needs_workspace_hydration({"context_missing": True})
    assert _memory_context_payload_needs_workspace_hydration({"result": {"context_stale": "stale"}})
    assert not _memory_context_payload_needs_workspace_hydration({"result": {"profile": [{"name": "Ada"}]}})


def test_enforce_initial_memory_context_call_adds_compact_workspace_hydration_when_context_missing(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    (workspace / "tasks").mkdir(parents=True)
    (workspace / "projects").mkdir(parents=True)
    (workspace / "tasks" / "thisweek.md").write_text(
        "# Focus\n- [ ] Ship AIS-171\n- [ ] Review deadlines\n",
        encoding="utf-8",
    )
    (workspace / "_findings.md").write_text(
        "\n".join([f"- Finding {i}" for i in range(1, 12)]),
        encoding="utf-8",
    )
    (workspace / "projects" / "alpha.md").write_text(
        "---\nstatus: active\ntitle: Alpha\ndeadline: 2026-07-30\n---\nbody\n",
        encoding="utf-8",
    )
    (workspace / "projects" / "beta.md").write_text(
        "---\nstatus: waiting\ntitle: Beta\n---\nbody\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("agent.conversation_loop.resolve_agent_cwd", lambda: workspace)

    def _mock_execute_tool_calls(assistant_message, messages, _effective_task_id, _api_call_count):
        tc = assistant_message.tool_calls[0]
        messages.append(
            {
                "role": "tool",
                "name": tc.function.name,
                "tool_call_id": tc.id,
                "content": json.dumps({"context_missing": True}),
            }
        )

    agent = SimpleNamespace(
        valid_tool_names={"mcp_IAMDS_mcp_memory_memory_context"},
        _enforce_initial_memory_context=True,
        _session_start_compact_workspace_hydration=True,
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
        conversation_history=[],
        original_user_message="hello",
        effective_task_id="t1",
    )

    hydration_msgs = [
        m for m in messages if m.get("role") == "system" and "Session-start workspace context" in str(m.get("content", ""))
    ]
    assert len(hydration_msgs) == 1
    content = hydration_msgs[0]["content"]
    assert "thisweek:" in content
    assert "findings_tail:" in content
    assert "Alpha [active, due 2026-07-30]" in content
    assert "Beta [waiting]" in content


def test_build_stale_project_row_flags_only_active_entries_after_14_days():
    now = datetime(2026, 7, 22, tzinfo=timezone.utc)
    stale = _build_stale_project_row(
        {
            "status": "active",
            "title": "Alpha",
            "updated_at": "2026-07-01",
            "deadline": "2026-08-01",
        },
        now=now,
    )
    assert stale == "Alpha [21d idle, due 2026-08-01]"

    fresh = _build_stale_project_row(
        {"status": "active", "title": "Fresh", "updated_at": "2026-07-15"},
        now=now,
    )
    assert fresh is None

    waiting = _build_stale_project_row(
        {"status": "waiting", "title": "Waiting", "updated_at": "2026-07-01"},
        now=now,
    )
    assert waiting is None


def test_compact_workspace_hydration_surfaces_stale_projects(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    (workspace / "projects").mkdir(parents=True)
    stale_updated = (datetime.now(timezone.utc) - timedelta(days=17)).strftime("%Y-%m-%d")
    fresh_updated = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
    (workspace / "projects" / "alpha.md").write_text(
        f"---\nstatus: active\ntitle: Alpha\nupdated_at: {stale_updated}\ndeadline: 2026-08-02\n---\nbody\n",
        encoding="utf-8",
    )
    (workspace / "projects" / "beta.md").write_text(
        f"---\nstatus: active\ntitle: Beta\nupdated_at: {fresh_updated}\n---\nbody\n",
        encoding="utf-8",
    )
    (workspace / "projects" / "gamma.md").write_text(
        "---\nstatus: waiting\ntitle: Gamma\nupdated_at: 2026-01-01\n---\nbody\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("agent.conversation_loop.resolve_agent_cwd", lambda: workspace)

    def _mock_execute_tool_calls(assistant_message, messages, _effective_task_id, _api_call_count):
        tc = assistant_message.tool_calls[0]
        messages.append(
            {
                "role": "tool",
                "name": tc.function.name,
                "tool_call_id": tc.id,
                "content": json.dumps({"context_missing": True}),
            }
        )

    agent = SimpleNamespace(
        valid_tool_names={"mcp_IAMDS_mcp_memory_memory_context"},
        _enforce_initial_memory_context=True,
        _session_start_compact_workspace_hydration=True,
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
        conversation_history=[],
        original_user_message="hello",
        effective_task_id="t1",
    )

    hydration = next(
        m for m in messages if m.get("role") == "system" and "Session-start workspace context" in str(m.get("content", ""))
    )["content"]
    assert "stale_projects:" in hydration
    assert "Alpha [" in hydration
    assert "due 2026-08-02" in hydration
    assert "Beta [" not in hydration.split("stale_projects:", 1)[1]


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


def test_build_onboarding_context_line_from_question_flow_without_flag():
    messages = [
        {
            "role": "tool",
            "name": "mcp_IAMDS_mcp_memory_memory_context",
            "content": json.dumps(
                {
                    "onboarding_question_flow_required": True,
                    "onboarding_questions": ["What is your role/title?"],
                },
                ensure_ascii=False,
            ),
        }
    ]

    line = _build_onboarding_context_line_from_recent_memory_context(
        messages=messages,
        valid_tool_names={"mcp_IAMDS_mcp_memory_memory_context"},
    )
    assert (
        line
        == "The profile in the remote server is not set yet, we will proceed with onboarding."
    )


def test_build_onboarding_context_line_strips_embedded_json_blob():
    malformed_line = (
        '{ "context_line": "The profile in the remote server is not set yet, we will proceed with onboarding.", '
        '"questions": [ "What is your role/title?" ] }'
        "The profile in the remote server is not set yet, we will proceed with onboarding."
    )
    messages = [
        {
            "role": "tool",
            "name": "mcp_IAMDS_mcp_memory_memory_context",
            "content": json.dumps(
                {
                    "onboarding_question_flow_required": True,
                    "onboarding_context_message": malformed_line,
                    "onboarding_questions": ["What is your role/title?"],
                },
                ensure_ascii=False,
            ),
        }
    ]

    line = _build_onboarding_context_line_from_recent_memory_context(
        messages=messages,
        valid_tool_names={"mcp_IAMDS_mcp_memory_memory_context"},
    )

    assert (
        line
        == "The profile in the remote server is not set yet, we will proceed with onboarding."
    )


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


def test_read_recent_onboarding_metadata_ignores_non_onboarding_question_text():
    messages = [
        {
            "role": "tool",
            "name": "mcp_IAMDS_mcp_memory_memory_context",
            "content": json.dumps(
                {
                    "result": json.dumps(
                        {
                            "profile": [{"title": "Senior Engineer"}],
                            "maintenance_hints": [
                                "Would you like me to compact stale memories?"
                            ],
                        },
                        ensure_ascii=False,
                    )
                },
                ensure_ascii=False,
            ),
        }
    ]

    payload = _read_recent_onboarding_metadata_from_memory_context(
        messages=messages,
        valid_tool_names={"mcp_IAMDS_mcp_memory_memory_context"},
    )

    assert payload is None


def test_read_recent_onboarding_metadata_preserves_outer_onboarding_fields_with_nested_result():
    messages = [
        {
            "role": "tool",
            "name": "mcp_IAMDS_mcp_memory_memory_context",
            "content": json.dumps(
                {
                    "result": json.dumps({"context_hint": "x"}, ensure_ascii=False),
                    "onboarding_question_flow_required": True,
                    "onboarding_first_question": "What is your role/title?",
                    "onboarding_questions": ["What is your role/title?", "What is your primary tech stack?"],
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
    assert payload["onboarding_questions"][1] == "What is your primary tech stack?"


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


def test_enforce_initial_memory_context_call_runs_onboarding_clarify_sequence():
    calls = []

    def _mock_execute_tool_calls(assistant_message, messages, _effective_task_id, _api_call_count):
        tc = assistant_message.tool_calls[0]
        tool_name = tc.function.name
        tool_args = json.loads(tc.function.arguments)
        calls.append((tool_name, tool_args))
        if tool_name == "mcp_IAMDS_mcp_memory_memory_context":
            messages.append(
                {
                    "role": "tool",
                    "name": tool_name,
                    "tool_call_id": tc.id,
                    "content": json.dumps(
                        {
                            "onboarding_init_context_required": True,
                            "onboarding_context_message": (
                                "The profile in the remote server is not set yet, we will proceed with onboarding."
                            ),
                            "onboarding_question_flow_required": True,
                            "onboarding_questions": ["What is your role/title?", "What is your primary tech stack?"],
                            "onboarding_first_question": "What is your role/title?",
                        },
                        ensure_ascii=False,
                    ),
                }
            )
            return

        if tool_name == "clarify":
            messages.append(
                {
                    "role": "tool",
                    "name": "clarify",
                    "tool_call_id": tc.id,
                    "content": json.dumps(
                        {
                            "question": tool_args.get("question"),
                            "user_response": "stub-answer",
                        },
                        ensure_ascii=False,
                    ),
                }
            )
            return

        raise AssertionError(f"unexpected tool: {tool_name}")

    emitted = []
    agent = SimpleNamespace(
        valid_tool_names={"mcp_IAMDS_mcp_memory_memory_context", "clarify"},
        _enforce_initial_memory_context=True,
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

    assert [name for name, _ in calls] == [
        "mcp_IAMDS_mcp_memory_memory_context",
        "clarify",
        "clarify",
    ]
    assert calls[1][1] == {"question": "What is your role/title?"}
    assert calls[2][1] == {"question": "What is your primary tech stack?"}
    assert any(
        m.get("role") == "assistant"
        and m.get("content") == "The profile in the remote server is not set yet, we will proceed with onboarding."
        and not m.get("tool_calls")
        for m in messages
    )
    assert getattr(agent, "_initial_onboarding_clarify_enforced", False) is True


def test_enforce_initial_memory_context_call_skips_onboarding_clarify_without_clarify_tool():
    calls = []

    def _mock_execute_tool_calls(assistant_message, messages, _effective_task_id, _api_call_count):
        tc = assistant_message.tool_calls[0]
        tool_name = tc.function.name
        calls.append(tool_name)
        if tool_name == "mcp_IAMDS_mcp_memory_memory_context":
            messages.append(
                {
                    "role": "tool",
                    "name": tool_name,
                    "tool_call_id": tc.id,
                    "content": json.dumps(
                        {
                            "onboarding_question_flow_required": True,
                            "onboarding_questions": ["What is your role/title?"],
                        },
                        ensure_ascii=False,
                    ),
                }
            )
            return
        raise AssertionError(f"unexpected tool: {tool_name}")

    agent = SimpleNamespace(
        valid_tool_names={"mcp_IAMDS_mcp_memory_memory_context"},
        _enforce_initial_memory_context=True,
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
        conversation_history=[],
        original_user_message="hello",
        effective_task_id="t1",
    )

    assert calls == ["mcp_IAMDS_mcp_memory_memory_context"]
    assert any(
        m.get("role") == "assistant"
        and m.get("content")
        == "The profile in the remote server is not set yet, we will proceed with onboarding."
        and not m.get("tool_calls")
        for m in messages
    )


def test_enforce_initial_memory_context_call_runs_onboarding_clarify_with_nested_questions():
    calls = []

    def _mock_execute_tool_calls(assistant_message, messages, _effective_task_id, _api_call_count):
        tc = assistant_message.tool_calls[0]
        tool_name = tc.function.name
        tool_args = json.loads(tc.function.arguments)
        calls.append((tool_name, tool_args))
        if tool_name == "mcp_IAMDS_mcp_memory_memory_context":
            messages.append(
                {
                    "role": "tool",
                    "name": tool_name,
                    "tool_call_id": tc.id,
                    "content": json.dumps(
                        {
                            "onboarding_init_context_required": True,
                            "onboarding_context_message": (
                                "The profile in the remote server is not set yet, we will proceed with onboarding."
                            ),
                            "onboarding_init_result": {
                                "questions": [
                                    "What is your role/title?",
                                    "What is your primary tech stack?",
                                ]
                            },
                        },
                        ensure_ascii=False,
                    ),
                }
            )
            return
        if tool_name == "clarify":
            messages.append(
                {
                    "role": "tool",
                    "name": "clarify",
                    "tool_call_id": tc.id,
                    "content": json.dumps(
                        {
                            "question": tool_args.get("question"),
                            "user_response": "stub-answer",
                        },
                        ensure_ascii=False,
                    ),
                }
            )
            return
        raise AssertionError(f"unexpected tool: {tool_name}")

    agent = SimpleNamespace(
        valid_tool_names={"mcp_IAMDS_mcp_memory_memory_context", "clarify"},
        _enforce_initial_memory_context=True,
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
        conversation_history=[],
        original_user_message="hello",
        effective_task_id="t1",
    )

    assert [name for name, _ in calls] == [
        "mcp_IAMDS_mcp_memory_memory_context",
        "clarify",
        "clarify",
    ]
    assert calls[1][1] == {"question": "What is your role/title?"}
    assert calls[2][1] == {"question": "What is your primary tech stack?"}
    assert getattr(agent, "_initial_onboarding_clarify_enforced", False) is True


def test_apply_onboarding_clarify_context_adds_context_and_single_question():
    messages = [
        {
            "role": "tool",
            "name": "mcp_IAMDS_mcp_memory_memory_context",
            "content": json.dumps(
                {
                    "onboarding_init_context_required": True,
                    "onboarding_context_message": (
                        "The profile in the remote server is not set yet, so we'll proceed with onboarding."
                    ),
                    "onboarding_question_flow_required": True,
                    "onboarding_first_question": "What is your role/title?",
                },
                ensure_ascii=False,
            ),
        }
    ]
    clarify_call = SimpleNamespace(
        function=SimpleNamespace(
            name="clarify",
            arguments=json.dumps(
                {
                    "question": (
                        "Tell me your role/title, stack, language preference, and communication style."
                    )
                },
                ensure_ascii=False,
            ),
        )
    )
    assistant_message = SimpleNamespace(content="", tool_calls=[clarify_call])

    _apply_onboarding_clarify_context(
        assistant_message,
        messages=messages,
        valid_tool_names={"mcp_IAMDS_mcp_memory_memory_context"},
        is_first_turn=True,
        has_visible_content_fn=lambda _text: False,
    )

    args = json.loads(assistant_message.tool_calls[0].function.arguments)
    assert args["question"] == "What is your role/title?"
    assert (
        assistant_message.content
        == "The profile in the remote server is not set yet, so we'll proceed with onboarding."
    )


def test_apply_onboarding_clarify_context_first_turn_replaces_blob_and_reasoning_with_clean_line():
    messages = [
        {
            "role": "tool",
            "name": "mcp_IAMDS_mcp_memory_memory_context",
            "content": json.dumps(
                {
                    "onboarding_init_context_required": True,
                    "onboarding_context_message": (
                        '{ "context_line": "The profile in the remote server is not set yet, we will proceed with onboarding.", '
                        '"questions": [ "What is your role/title?" ] }'
                        "The profile in the remote server is not set yet, we will proceed with onboarding."
                    ),
                    "onboarding_question_flow_required": True,
                    "onboarding_first_question": "What is your role/title?",
                },
                ensure_ascii=False,
            ),
        }
    ]
    clarify_call = SimpleNamespace(
        function=SimpleNamespace(
            name="clarify",
            arguments=json.dumps(
                {
                    "question": "Wrong merged question",
                },
                ensure_ascii=False,
            ),
        )
    )
    assistant_message = SimpleNamespace(
        content=(
            '{ "context_line": "The profile in the remote server is not set yet, we will proceed with onboarding.", '
            '"questions": [ "What is your role/title?" ] }'
            "The profile in the remote server is not set yet, we will proceed with onboarding.\n"
            "Let me reason about the onboarding sequence..."
        ),
        tool_calls=[clarify_call],
    )

    _apply_onboarding_clarify_context(
        assistant_message,
        messages=messages,
        valid_tool_names={"mcp_IAMDS_mcp_memory_memory_context"},
        is_first_turn=True,
        has_visible_content_fn=lambda _text: True,
    )

    args = json.loads(assistant_message.tool_calls[0].function.arguments)
    assert args["question"] == "What is your role/title?"
    assert (
        assistant_message.content
        == "The profile in the remote server is not set yet, we will proceed with onboarding."
    )


def test_apply_onboarding_clarify_context_followup_turn_strips_preamble_and_uses_next_question():
    messages = [
        {
            "role": "tool",
            "name": "mcp_IAMDS_mcp_memory_memory_context",
            "content": json.dumps(
                {
                    "onboarding_question_flow_required": True,
                    "onboarding_questions": [
                        "What is your role/title?",
                        "What is your primary tech stack?",
                    ],
                },
                ensure_ascii=False,
            ),
        },
        {
            "role": "tool",
            "name": "clarify",
            "content": json.dumps(
                {
                    "question": "What is your role/title?",
                    "user_response": "Office worker",
                },
                ensure_ascii=False,
            ),
        },
    ]

    clarify_call = SimpleNamespace(
        function=SimpleNamespace(
            name="clarify",
            arguments=json.dumps(
                {
                    "question": "Some wrong question?",
                },
                ensure_ascii=False,
            ),
        )
    )
    assistant_message = SimpleNamespace(
        content="Okay, let's break this down and reason about the onboarding flow...",
        tool_calls=[clarify_call],
    )

    _apply_onboarding_clarify_context(
        assistant_message,
        messages=messages,
        valid_tool_names={"mcp_IAMDS_mcp_memory_memory_context"},
        is_first_turn=False,
        has_visible_content_fn=lambda _text: True,
    )

    args = json.loads(assistant_message.tool_calls[0].function.arguments)
    assert args["question"] == "What is your primary tech stack?"
    assert assistant_message.content == ""


def test_apply_onboarding_clarify_context_reasks_unanswered_question_after_empty_response():
    messages = [
        {
            "role": "tool",
            "name": "mcp_IAMDS_mcp_memory_memory_context",
            "content": json.dumps(
                {
                    "onboarding_question_flow_required": True,
                    "onboarding_questions": [
                        "What is your role/title?",
                        "What is your primary tech stack?",
                    ],
                },
                ensure_ascii=False,
            ),
        },
        {
            "role": "tool",
            "name": "clarify",
            "content": json.dumps(
                {
                    "question": "What is your role/title?",
                    "user_response": "",
                },
                ensure_ascii=False,
            ),
        },
    ]
    clarify_call = SimpleNamespace(
        function=SimpleNamespace(
            name="clarify",
            arguments=json.dumps({"question": "Wrong"}, ensure_ascii=False),
        )
    )
    assistant_message = SimpleNamespace(content="thinking...", tool_calls=[clarify_call])

    _apply_onboarding_clarify_context(
        assistant_message,
        messages=messages,
        valid_tool_names={"mcp_IAMDS_mcp_memory_memory_context"},
        is_first_turn=False,
        has_visible_content_fn=lambda _text: True,
    )

    args = json.loads(assistant_message.tool_calls[0].function.arguments)
    assert args["question"] == "What is your role/title?"
    assert assistant_message.content == ""


def test_apply_onboarding_clarify_context_uses_recent_onboarding_payload_even_if_latest_memory_context_is_plain():
    messages = [
        {
            "role": "tool",
            "name": "mcp_IAMDS_mcp_memory_memory_context",
            "content": json.dumps(
                {
                    "onboarding_question_flow_required": True,
                    "onboarding_questions": [
                        "What is your role/title?",
                        "What is your primary tech stack?",
                    ],
                },
                ensure_ascii=False,
            ),
        },
        {
            "role": "tool",
            "name": "clarify",
            "content": json.dumps(
                {
                    "question": "What is your role/title?",
                    "user_response": "Office worker",
                },
                ensure_ascii=False,
            ),
        },
        {
            "role": "tool",
            "name": "mcp_IAMDS_mcp_memory_memory_context",
            "content": json.dumps(
                {
                    "context": "normal memory context without onboarding flags",
                },
                ensure_ascii=False,
            ),
        },
    ]

    clarify_call = SimpleNamespace(
        function=SimpleNamespace(
            name="clarify",
            arguments=json.dumps({"question": "wrong"}, ensure_ascii=False),
        )
    )
    assistant_message = SimpleNamespace(
        content="Reasoning that should be hidden during onboarding follow-up",
        tool_calls=[clarify_call],
    )

    _apply_onboarding_clarify_context(
        assistant_message,
        messages=messages,
        valid_tool_names={"mcp_IAMDS_mcp_memory_memory_context"},
        is_first_turn=False,
        has_visible_content_fn=lambda _text: True,
    )

    args = json.loads(assistant_message.tool_calls[0].function.arguments)
    assert args["question"] == "What is your primary tech stack?"
    assert assistant_message.content == ""


def test_has_memory_save_after_onboarding_answers_ignores_early_save():
    onboarding_payload = {
        "onboarding_questions": [
            "What is your role/title?",
            "What is your primary tech stack?",
        ]
    }
    messages = [
        {"role": "tool", "name": "mcp_IAMDS_mcp_memory_memory_save", "content": '{"ok":true}'},
        {
            "role": "tool",
            "name": "clarify",
            "content": json.dumps(
                {"question": "What is your role/title?", "user_response": "Engineer"},
                ensure_ascii=False,
            ),
        },
        {
            "role": "tool",
            "name": "clarify",
            "content": json.dumps(
                {"question": "What is your primary tech stack?", "user_response": "Python"},
                ensure_ascii=False,
            ),
        },
    ]
    assert (
        _has_memory_save_after_onboarding_answers(
            messages=messages, onboarding_payload=onboarding_payload
        )
        is False
    )


def test_has_memory_save_after_onboarding_answers_accepts_post_answer_save():
    onboarding_payload = {
        "onboarding_questions": [
            "What is your role/title?",
            "What is your primary tech stack?",
        ]
    }
    messages = [
        {
            "role": "tool",
            "name": "clarify",
            "content": json.dumps(
                {"question": "What is your role/title?", "user_response": "Engineer"},
                ensure_ascii=False,
            ),
        },
        {
            "role": "tool",
            "name": "clarify",
            "content": json.dumps(
                {"question": "What is your primary tech stack?", "user_response": "Python"},
                ensure_ascii=False,
            ),
        },
        {"role": "tool", "name": "mcp_IAMDS_mcp_memory_memory_save", "content": '{"ok":true}'},
    ]
    assert (
        _has_memory_save_after_onboarding_answers(
            messages=messages, onboarding_payload=onboarding_payload
        )
        is True
    )


def test_has_memory_save_after_onboarding_answers_accepts_hyphenated_save_tool():
    onboarding_payload = {
        "onboarding_questions": ["What is your role/title?"]
    }
    messages = [
        {
            "role": "tool",
            "name": "clarify",
            "content": json.dumps(
                {"question": "What is your role/title?", "user_response": "Engineer"},
                ensure_ascii=False,
            ),
        },
        {"role": "tool", "name": "mcp_IAMDS_mcp_memory-memory_save", "content": '{"ok":true}'},
    ]
    assert (
        _has_memory_save_after_onboarding_answers(
            messages=messages, onboarding_payload=onboarding_payload
        )
        is True
    )


def test_resume_onboarding_clarify_if_needed_reasks_pending_question_on_non_first_turn():
    calls = []

    def _mock_execute_tool_calls(assistant_message, messages, _effective_task_id, _api_call_count):
        tc = assistant_message.tool_calls[0]
        tool_name = tc.function.name
        tool_args = json.loads(tc.function.arguments)
        calls.append((tool_name, tool_args))
        if tool_name == "clarify":
            messages.append(
                {
                    "role": "tool",
                    "name": "clarify",
                    "tool_call_id": tc.id,
                    "content": json.dumps(
                        {
                            "question": tool_args.get("question"),
                            "user_response": "",
                        },
                        ensure_ascii=False,
                    ),
                }
            )
            return
        raise AssertionError(f"unexpected tool: {tool_name}")

    emitted = []
    agent = SimpleNamespace(
        valid_tool_names={"mcp_IAMDS_mcp_memory_memory_context", "clarify"},
        _emit_interim_assistant_message=lambda msg: emitted.append(msg),
        _execute_tool_calls=_mock_execute_tool_calls,
    )
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "tool",
            "name": "mcp_IAMDS_mcp_memory_memory_context",
            "content": json.dumps(
                {
                    "onboarding_question_flow_required": True,
                    "onboarding_questions": [
                        "What is your role/title?",
                        "What is your primary tech stack?",
                    ],
                },
                ensure_ascii=False,
            ),
        },
        {
            "role": "tool",
            "name": "clarify",
            "content": json.dumps(
                {
                    "question": "What is your role/title?",
                    "user_response": "",
                },
                ensure_ascii=False,
            ),
        },
    ]

    _resume_onboarding_clarify_if_needed(
        agent,
        messages=messages,
        conversation_history=[{"role": "user", "content": "older turn"}],
        effective_task_id="t1",
    )

    assert calls == [("clarify", {"question": "What is your role/title?"})]
    assert emitted and emitted[0]["tool_calls"][0]["function"]["name"] == "clarify"


def test_maybe_translate_onboarding_prompts_via_llm_translates_using_user_message_language():
    class _FakeTransport:
        @staticmethod
        def normalize_response(_response):
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "context_line": "Tu perfil en el servidor remoto aún no está configurado; procederemos con el onboarding.",
                        "questions": ["¿Cuál es tu rol o cargo?"],
                    },
                    ensure_ascii=False,
                )
            )

    agent = SimpleNamespace(
        _build_api_kwargs=lambda messages: {"model": "x", "messages": messages, "tools": [{"name": "x"}]},
        _interruptible_api_call=lambda kwargs: kwargs,
        _get_transport=lambda: _FakeTransport(),
    )

    line, questions = _maybe_translate_onboarding_prompts_via_llm(
        agent=agent,
        original_user_message="Hola, necesito configurar mi perfil",
        context_line="The profile in the remote server is not set yet, we will proceed with onboarding.",
        questions=["What is your role/title?"],
    )

    assert line.startswith("Tu perfil")
    assert questions == ["¿Cuál es tu rol o cargo?"]


def test_maybe_translate_onboarding_prompts_via_llm_falls_back_on_invalid_payload():
    class _FakeTransport:
        @staticmethod
        def normalize_response(_response):
            return SimpleNamespace(content="not-json")

    agent = SimpleNamespace(
        _build_api_kwargs=lambda messages: {"model": "x", "messages": messages},
        _interruptible_api_call=lambda kwargs: kwargs,
        _get_transport=lambda: _FakeTransport(),
    )

    original_line = "The profile in the remote server is not set yet, we will proceed with onboarding."
    original_questions = ["What is your role/title?"]
    line, questions = _maybe_translate_onboarding_prompts_via_llm(
        agent=agent,
        original_user_message="Hola",
        context_line=original_line,
        questions=original_questions,
    )

    assert line == original_line
    assert questions == original_questions


def test_maybe_translate_onboarding_prompts_via_llm_preserves_question_sequence_size():
    class _FakeTransport:
        @staticmethod
        def normalize_response(_response):
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "context_line": "Tu perfil aún no está configurado; empezamos onboarding.",
                        "questions": ["¿Cuál es tu rol o cargo?"],
                    },
                    ensure_ascii=False,
                )
            )

    agent = SimpleNamespace(
        _build_api_kwargs=lambda messages: {"model": "x", "messages": messages},
        _interruptible_api_call=lambda kwargs: kwargs,
        _get_transport=lambda: _FakeTransport(),
    )

    line, questions = _maybe_translate_onboarding_prompts_via_llm(
        agent=agent,
        original_user_message="Hola",
        context_line="The profile in the remote server is not set yet, we will proceed with onboarding.",
        questions=["What is your role/title?", "What is your primary tech stack?"],
    )

    assert line == "Tu perfil aún no está configurado; empezamos onboarding."
    assert questions == ["What is your role/title?", "What is your primary tech stack?"]


def test_parse_json_object_from_text_allows_trailing_text_after_json():
    payload = _parse_json_object_from_text(
        '{"context_line":"Hola","questions":["¿Cuál es tu rol?"]}texto-extra'
    )

    assert payload is not None
    assert payload["context_line"] == "Hola"


def test_parse_json_object_from_text_allows_prefixed_text_before_json():
    payload = _parse_json_object_from_text(
        'intro: {"context_line":"Hola","questions":["¿Cuál es tu rol?"]}'
    )

    assert payload is not None
    assert payload["context_line"] == "Hola"


def test_sanitize_onboarding_context_line_text_extracts_context_from_blob():
    dirty = (
        '{ "context_line": "The profile in the remote server is not set yet, we will proceed with onboarding.", '
        '"questions": [ "What is your role/title?" ] }The profile in the remote server is not set yet, we will proceed with onboarding.'
    )

    clean = _sanitize_onboarding_context_line_text(dirty)

    assert clean == "The profile in the remote server is not set yet, we will proceed with onboarding."
