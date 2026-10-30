from __future__ import annotations

import json
from types import SimpleNamespace

from agent.open_questions import derive_blocking_open_question_from_review_text
from agent.tool_executor import _maybe_persist_blocking_clarify_open_question


def test_clarify_timeout_is_not_recorded_as_open_question(tmp_path, monkeypatch):
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

    # A timeout is a transport outcome, not a decision the user still owes.
    assert not (tmp_path / "_open-questions.md").exists()


def test_clarify_open_question_dedupes_within_same_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    agent = SimpleNamespace(_current_turn_id="turn-openq-2", platform="gateway")
    result = json.dumps(
        {
            "question": "Pick deployment window",
            "user_response": "you decide",
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


def test_extracts_marker_based_open_question_from_review_text():
    extracted = derive_blocking_open_question_from_review_text(
        "Weekly review output\nOPEN_QUESTION_NEEDED: Wer gibt final frei?\nOther text"
    )
    assert extracted == ("Weekly review", "Wer gibt final frei?")


def test_persists_ambiguous_clarify_response_to_open_questions(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    agent = SimpleNamespace(_current_turn_id="turn-openq-3", platform="gateway")

    _maybe_persist_blocking_clarify_open_question(
        agent,
        function_name="clarify",
        function_args={"question": "Which price list should I use?"},
        function_result=json.dumps(
            {
                "question": "Which price list should I use?",
                "user_response": "use the usual one",
            },
            ensure_ascii=False,
        ),
    )

    content = (tmp_path / "_open-questions.md").read_text(encoding="utf-8")
    assert "context=Clarify required for: Which price list should I use?" in content
    assert "needed=User response stayed ambiguous (use the usual one)." in content


def test_structured_clarify_timeout_is_not_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    agent = SimpleNamespace(_current_turn_id="turn-openq-4", platform="gateway")

    _maybe_persist_blocking_clarify_open_question(
        agent,
        function_name="clarify",
        function_args={"question": "Who approves this deployment?"},
        function_result=json.dumps(
            {
                "question": "Who approves this deployment?",
                "user_response": "",
                "response_state": "timeout",
                "resolved": False,
                "reason_code": "clarify_timeout",
            },
            ensure_ascii=False,
        ),
    )

    assert not (tmp_path / "_open-questions.md").exists()


def test_persists_structured_ambiguous_clarify_result(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    agent = SimpleNamespace(_current_turn_id="turn-openq-5", platform="gateway")

    _maybe_persist_blocking_clarify_open_question(
        agent,
        function_name="clarify",
        function_args={"question": "Who approves this deployment?"},
        function_result=json.dumps(
            {
                "question": "Who approves this deployment?",
                "user_response": "not sure, either is fine",
                "response_state": "answered",
                "resolved": False,
                "reason_code": "clarify_ambiguous_response",
            },
            ensure_ascii=False,
        ),
    )

    content = (tmp_path / "_open-questions.md").read_text(encoding="utf-8")
    assert "context=Clarify required for: Who approves this deployment?" in content


def test_onboarding_clarify_timeout_is_recorded_as_open_question(tmp_path, monkeypatch):
    """AIS-333: an expired onboarding question is still owed — record it."""
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    agent = SimpleNamespace(
        _current_turn_id="turn-openq-onb",
        platform="tui",
        _onboarding_clarify_active=True,
    )

    _maybe_persist_blocking_clarify_open_question(
        agent,
        function_name="clarify",
        function_args={"question": "Which state/canton do you work in?"},
        function_result=json.dumps(
            {
                "question": "Which state/canton do you work in?",
                "user_response": "",
                "response_state": "timeout",
                "resolved": False,
                "reason_code": "clarify_timeout",
                "timeout_seconds": 600,
            },
            ensure_ascii=False,
        ),
    )

    text = (tmp_path / "_open-questions.md").read_text(encoding="utf-8")
    assert "Which state/canton do you work in?" in text
    assert "did not answer in time" in text


def test_non_onboarding_clarify_timeout_stays_unrecorded(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    agent = SimpleNamespace(_current_turn_id="turn-openq-plain", platform="tui", _onboarding_clarify_active=False)

    _maybe_persist_blocking_clarify_open_question(
        agent,
        function_name="clarify",
        function_args={"question": "Deploy now?"},
        function_result=json.dumps(
            {"question": "Deploy now?", "user_response": "", "response_state": "timeout", "resolved": False, "reason_code": "clarify_timeout"},
            ensure_ascii=False,
        ),
    )

    assert not (tmp_path / "_open-questions.md").exists()


# ── AIS-793: "later" answers land in the open questions, once ─────────────


def _workspace(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    vault = tmp_path / "vault"
    home.mkdir()
    vault.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("TERMINAL_CWD", str(vault))
    return vault


def _deferred_lines(vault):
    path = vault / "_open-questions.md"
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("- ts=")]


_FOUR_OPEN_POINTS_RESULT = {
    "v": 1,
    "questions": [
        {"topic": "QS-38", "question": "Should the load test run before or after the release?", "choices": ["Before", "After"]},
        {"topic": "QS-41", "question": "Who signs off the acceptance test?"},
        {"topic": "Release 0.7.15", "question": "Ship the desktop build with this release?"},
        {"topic": "Docs", "question": "Where should the release notes go?"},
    ],
    "answers": [
        {"state": "answered", "response": "Before"},
        {"state": "skipped", "response": ""},
        {"state": "timeout", "response": ""},
        {"state": "skipped", "response": ""},
    ],
}


def test_later_questions_are_recorded_with_topic_for_the_brief(tmp_path, monkeypatch):
    from cron.brief_sources.workspace import _essence

    vault = _workspace(tmp_path, monkeypatch)
    agent = SimpleNamespace(_current_turn_id="turn-later-1", platform="desktop")

    _maybe_persist_blocking_clarify_open_question(
        agent,
        function_name="clarify",
        function_args={"questions": _FOUR_OPEN_POINTS_RESULT["questions"]},
        function_result=json.dumps(_FOUR_OPEN_POINTS_RESULT, ensure_ascii=False),
    )

    lines = _deferred_lines(vault)
    # Only the two "later" questions: the answer is resolved, the timeout is noise.
    assert len(lines) == 2
    assert [_essence(line[2:]) for line in lines] == [
        "QS-41: Who signs off the acceptance test?",
        "Docs: Where should the release notes go?",
    ]
    assert "source=desktop clarify" in lines[0]


def test_later_question_is_recorded_once_across_sessions(tmp_path, monkeypatch):
    import agent.open_questions as oq

    vault = _workspace(tmp_path, monkeypatch)
    result = json.dumps(
        {
            "question": "Ship the desktop build with this release?",
            "choices_offered": None,
            "user_response": "",
            "response_state": "skipped",
            "resolved": False,
            "reason_code": "clarify_skipped",
            "v": 1,
            "questions": [{"question": "Ship the desktop build with this release?"}],
            "answers": [{"state": "skipped", "response": ""}],
        }
    )
    for turn in ("turn-a", "turn-b"):
        _maybe_persist_blocking_clarify_open_question(
            SimpleNamespace(_current_turn_id=turn, platform="tui"),
            function_name="clarify",
            function_args={"question": "Ship the desktop build with this release?"},
            function_result=result,
        )
        # A new process (next session) forgets its in-memory dedupe keys.
        oq._seen_dedupe_keys.clear()

    lines = _deferred_lines(vault)
    assert len(lines) == 1
    assert "needed=Ship the desktop build with this release?" in lines[0]
    # No second "Clarify required for" entry from the single-form path.
    assert "Clarify required" not in (vault / "_open-questions.md").read_text(encoding="utf-8")


def test_answered_questions_leave_no_open_question(tmp_path, monkeypatch):
    vault = _workspace(tmp_path, monkeypatch)
    answered = dict(_FOUR_OPEN_POINTS_RESULT, answers=[{"state": "answered", "response": "x"}] * 4)

    _maybe_persist_blocking_clarify_open_question(
        SimpleNamespace(_current_turn_id="turn-ok", platform="desktop"),
        function_name="clarify",
        function_args={},
        function_result=json.dumps(answered),
    )

    assert _deferred_lines(vault) == []
