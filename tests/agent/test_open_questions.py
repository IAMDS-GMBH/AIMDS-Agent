from __future__ import annotations

import json
from types import SimpleNamespace

from agent.open_questions import derive_blocking_open_question_from_review_text
from agent.tool_executor import _maybe_persist_blocking_clarify_open_question


def test_persists_clarify_timeout_into_workspace_open_questions(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    agent = SimpleNamespace(_current_turn_id="turn-openq-1", platform="gateway")

    _maybe_persist_blocking_clarify_open_question(
        agent,
        function_name="clarify",
        function_args={"question": "Which owner should approve release?"},
        function_result=json.dumps(
            {
                "question": "Which owner should approve release?",
                "user_response": "[user did not respond within 10m]",
            },
            ensure_ascii=False,
        ),
    )

    content = (tmp_path / "_open-questions.md").read_text(encoding="utf-8")
    assert "type: open-questions" in content
    assert "context=Clarify required for: Which owner should approve release?" in content
    assert "needed=User did not answer in time ([user did not respond within 10m])." in content


def test_clarify_open_question_dedupes_within_same_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    agent = SimpleNamespace(_current_turn_id="turn-openq-2", platform="gateway")
    result = json.dumps(
        {
            "question": "Pick deployment window",
            "user_response": "[clarify prompt could not be delivered]",
        },
        ensure_ascii=False,
    )

    _maybe_persist_blocking_clarify_open_question(
        agent,
        function_name="clarify",
        function_args={"question": "Pick deployment window"},
        function_result=result,
    )
    _maybe_persist_blocking_clarify_open_question(
        agent,
        function_name="clarify",
        function_args={"question": "Pick deployment window"},
        function_result=result,
    )

    lines = [
        line
        for line in (tmp_path / "_open-questions.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("- ts=")
    ]
    assert len(lines) == 1


def test_extracts_blocking_open_question_from_review_text():
    extracted = derive_blocking_open_question_from_review_text(
        "## Weekly review\nBlocked: needs clarification on who signs off roadmap."
    )
    assert extracted == (
        "Weekly review",
        "Blocked: needs clarification on who signs off roadmap.",
    )
