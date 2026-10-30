"""Tests for tools/clarify_tool.py - Interactive clarifying questions."""

import json
from typing import List, Optional


import pytest

from tools.clarify_tool import (
    clarify_tool,
    check_clarify_requirements,
    ANSWER_STATES,
    MAX_CHOICES,
    MAX_QUESTIONS,
    CLARIFY_SCHEMA,
)

# AIS-793 (session 20261030_150532_431390): four open points the model packed
# into one "Wie soll es weitergehen?" — as four questions with topics.
FOUR_OPEN_POINTS = [
    {
        "topic": "QS-38",
        "question": "Should the load test run before or after the release?",
        "choices": ["Before the release", "After the release"],
    },
    {
        "topic": "QS-41",
        "question": "Who signs off the acceptance test?",
        "choices": ["Johannes", "Tobias", "The product team"],
    },
    {
        "topic": "Release 0.7.15",
        "question": "Ship the desktop build with this release?",
        "choices": ["Yes", "No, next week"],
    },
    {"topic": "Docs", "question": "Where should the release notes go?"},
]


def _card_callback(answers):
    """A platform with a question card: answers every question at once."""
    seen = {}

    def callback(question, choices):  # pragma: no cover - must not be used
        raise AssertionError("the single-question path must not be used")

    def ask_questions(questions):
        seen["questions"] = questions
        return answers

    callback.ask_questions = ask_questions
    return callback, seen


class TestClarifyToolBasics:
    """Basic functionality tests for clarify_tool."""

    def test_simple_question_with_callback(self):
        """Should return user response for simple question."""
        def mock_callback(question: str, choices: Optional[List[str]]) -> str:
            assert question == "What color?"
            assert choices is None
            return "blue"

        result = json.loads(clarify_tool("What color?", callback=mock_callback))
        assert result["question"] == "What color?"
        assert result["choices_offered"] is None
        assert result["user_response"] == "blue"
        assert result["response_state"] == "answered"
        assert result["resolved"] is True

    def test_question_with_choices(self):
        """Should pass choices to callback and return response."""
        def mock_callback(question: str, choices: Optional[List[str]]) -> str:
            assert question == "Pick a number"
            assert choices == ["1", "2", "3"]
            return "2"

        result = json.loads(clarify_tool(
            "Pick a number",
            choices=["1", "2", "3"],
            callback=mock_callback
        ))
        assert result["question"] == "Pick a number"
        assert result["choices_offered"] == ["1", "2", "3"]
        assert result["user_response"] == "2"
        assert result["response_state"] == "answered"
        assert result["resolved"] is True

    def test_empty_question_returns_error(self):
        """Should return error for empty question."""
        result = json.loads(clarify_tool("", callback=lambda q, c: "ignored"))
        assert "error" in result
        assert "required" in result["error"].lower()

    def test_whitespace_only_question_returns_error(self):
        """Should return error for whitespace-only question."""
        result = json.loads(clarify_tool("   \n\t  ", callback=lambda q, c: "ignored"))
        assert "error" in result

    def test_no_callback_returns_error(self):
        """Should return error when no callback is provided."""
        result = json.loads(clarify_tool("What do you want?"))
        assert "error" in result
        assert "not available" in result["error"].lower()


class TestClarifyToolChoicesValidation:
    """Tests for choices parameter validation."""

    def test_choices_trimmed_to_max(self):
        """Should trim choices to MAX_CHOICES."""
        choices_passed = []

        def mock_callback(question: str, choices: Optional[List[str]]) -> str:
            choices_passed.extend(choices or [])
            return "picked"

        many_choices = ["a", "b", "c", "d", "e", "f", "g"]
        clarify_tool("Pick one", choices=many_choices, callback=mock_callback)

        assert len(choices_passed) == MAX_CHOICES

    def test_empty_choices_become_none(self):
        """Empty choices list should become None (open-ended)."""
        choices_received = ["marker"]

        def mock_callback(question: str, choices: Optional[List[str]]) -> str:
            choices_received.clear()
            if choices is not None:
                choices_received.extend(choices)
            return "answer"

        clarify_tool("Open question?", choices=[], callback=mock_callback)
        assert choices_received == []  # Was cleared, nothing added

    def test_choices_with_only_whitespace_stripped(self):
        """Whitespace-only choices should be stripped out."""
        choices_received = []

        def mock_callback(question: str, choices: Optional[List[str]]) -> str:
            choices_received.extend(choices or [])
            return "answer"

        clarify_tool("Pick", choices=["valid", "  ", "", "also valid"], callback=mock_callback)
        assert choices_received == ["valid", "also valid"]

    def test_invalid_choices_type_returns_error(self):
        """Non-list choices should return error."""
        result = json.loads(clarify_tool(
            "Question?",
            choices="not a list",  # type: ignore
            callback=lambda q, c: "ignored"
        ))
        assert "error" in result
        assert "list" in result["error"].lower()

    def test_choices_converted_to_strings(self):
        """Non-string choices should be converted to strings."""
        choices_received = []

        def mock_callback(question: str, choices: Optional[List[str]]) -> str:
            choices_received.extend(choices or [])
            return "answer"

        clarify_tool("Pick", choices=[1, 2, 3], callback=mock_callback)  # type: ignore
        assert choices_received == ["1", "2", "3"]


class TestClarifyToolCallbackHandling:
    """Tests for callback error handling."""

    def test_callback_exception_returns_error(self):
        """Should return error if callback raises exception."""
        def failing_callback(question: str, choices: Optional[List[str]]) -> str:
            raise RuntimeError("User cancelled")

        result = json.loads(clarify_tool("Question?", callback=failing_callback))
        assert "error" in result
        assert "Failed to get user input" in result["error"]
        assert "User cancelled" in result["error"]

    def test_callback_receives_stripped_question(self):
        """Callback should receive trimmed question."""
        received_question = []

        def mock_callback(question: str, choices: Optional[List[str]]) -> str:
            received_question.append(question)
            return "answer"

        clarify_tool("  Question with spaces  \n", callback=mock_callback)
        assert received_question[0] == "Question with spaces"

    def test_user_response_stripped(self):
        """User response should be stripped of whitespace."""
        def mock_callback(question: str, choices: Optional[List[str]]) -> str:
            return "  response with spaces  \n"

        result = json.loads(clarify_tool("Q?", callback=mock_callback))
        assert result["user_response"] == "response with spaces"

    def test_structured_callback_result_passes_state(self):
        def mock_callback(question: str, choices: Optional[List[str]]):
            return {
                "user_response": "",
                "response_state": "timeout",
                "resolved": False,
                "reason_code": "clarify_timeout",
            }

        result = json.loads(clarify_tool("Q?", callback=mock_callback))
        assert result["response_state"] == "timeout"
        assert result["resolved"] is False
        assert result["reason_code"] == "clarify_timeout"


class TestCheckClarifyRequirements:
    """Tests for the requirements check function."""

    def test_always_returns_true(self):
        """clarify tool has no external requirements."""
        assert check_clarify_requirements() is True


class TestClarifySchema:
    """Tests for the OpenAI function-calling schema."""

    def test_schema_name(self):
        """Schema should have correct name."""
        assert CLARIFY_SCHEMA["name"] == "clarify"

    def test_schema_has_description(self):
        """Schema should have a description."""
        assert "description" in CLARIFY_SCHEMA
        assert len(CLARIFY_SCHEMA["description"]) > 50

    def test_schema_question_or_questions(self):
        """AIS-793: `question` or `questions` — the tool checks it at runtime
        (a top-level anyOf is not accepted by every provider)."""
        props = CLARIFY_SCHEMA["parameters"]["properties"]
        assert "question" in props and "questions" in props
        assert CLARIFY_SCHEMA["parameters"]["required"] == []
        result = json.loads(clarify_tool(callback=lambda q, c: "x"))
        assert "required" in result["error"].lower()

    def test_schema_questions_items(self):
        questions = CLARIFY_SCHEMA["parameters"]["properties"]["questions"]
        assert questions["maxItems"] == MAX_QUESTIONS == 4
        item = questions["items"]
        assert item["required"] == ["question"]
        assert set(item["properties"]) == {"question", "choices", "topic"}
        assert item["properties"]["choices"]["maxItems"] == MAX_CHOICES

    def test_schema_choices_optional(self):
        """Choices parameter should be optional."""
        assert "choices" not in CLARIFY_SCHEMA["parameters"]["required"]

    def test_schema_choices_max_items(self):
        """Schema should specify max items for choices."""
        choices_spec = CLARIFY_SCHEMA["parameters"]["properties"]["choices"]
        assert choices_spec.get("maxItems") == MAX_CHOICES

    def test_max_choices_is_four(self):
        """MAX_CHOICES constant should be 4."""
        assert MAX_CHOICES == 4


class TestClarifyTimeoutResult:
    """AIS-333: a platform that knows its deadline reports it in the result."""

    def test_timeout_dict_keeps_timeout_seconds(self):
        def cb(question, choices):
            return {
                "user_response": "",
                "response_state": "timeout",
                "resolved": False,
                "reason_code": "clarify_timeout",
                "timeout_seconds": 600,
            }

        result = json.loads(clarify_tool("Region?", callback=cb))
        assert result["response_state"] == "timeout"
        assert result["reason_code"] == "clarify_timeout"
        assert result["resolved"] is False
        assert result["timeout_seconds"] == 600

    def test_answered_dict_has_no_timeout_seconds(self):
        def cb(question, choices):
            return {"user_response": "Bavaria", "response_state": "answered", "resolved": True, "reason_code": ""}

        result = json.loads(clarify_tool("Region?", callback=cb))
        assert "timeout_seconds" not in result
        assert result["user_response"] == "Bavaria"


def _assert_contract(result: dict, count: int) -> None:
    """The clarify_questions contract (agent/contracts, AIS-797) by hand; the
    schema itself is validated too once the contracts package is present."""
    assert result["v"] == 1
    assert len(result["questions"]) == count
    assert len(result["answers"]) == count
    for item in result["questions"]:
        assert set(item) <= {"question", "choices", "topic"}
        assert item["question"]
        assert len(item.get("choices") or []) <= MAX_CHOICES
        assert len(item.get("topic") or "") <= 80
    for answer in result["answers"]:
        assert set(answer) == {"state", "response"}
        assert answer["state"] in ANSWER_STATES
        assert isinstance(answer["response"], str)
    try:
        from agent.contracts import validate
    except ImportError:
        return
    validate("clarify_questions", {k: result[k] for k in ("v", "questions", "answers")})


class TestClarifyQuestionsForm:
    """AIS-793: several questions in one ask, one answer per question."""

    def test_four_open_points_on_a_question_card(self):
        callback, seen = _card_callback([
            {"state": "answered", "response": "Before the release"},
            "Tobias",
            {"response_state": "skipped", "user_response": ""},
            {"user_response": "Into the project notes", "response_state": "answered"},
        ])
        result = json.loads(clarify_tool(questions=FOUR_OPEN_POINTS, callback=callback))

        assert [q["topic"] for q in seen["questions"]] == ["QS-38", "QS-41", "Release 0.7.15", "Docs"]
        assert set(result) == {"v", "questions", "answers"}
        _assert_contract(result, 4)
        assert result["answers"] == [
            {"state": "answered", "response": "Before the release"},
            {"state": "answered", "response": "Tobias"},
            {"state": "skipped", "response": ""},
            {"state": "answered", "response": "Into the project notes"},
        ]
        assert "choices" not in result["questions"][3]

    def test_without_a_card_the_questions_are_asked_one_after_another(self):
        asked = []

        def callback(question, choices):
            asked.append((question, choices))
            return f"answer {len(asked)}"

        result = json.loads(clarify_tool(questions=FOUR_OPEN_POINTS[:2], callback=callback))
        assert asked == [
            (
                "(1/2) QS-38: Should the load test run before or after the release?",
                ["Before the release", "After the release"],
            ),
            ("(2/2) QS-41: Who signs off the acceptance test?", ["Johannes", "Tobias", "The product team"]),
        ]
        _assert_contract(result, 2)
        assert [a["response"] for a in result["answers"]] == ["answer 1", "answer 2"]
        # The result keeps the questions as asked, without the prefixes.
        assert result["questions"][0]["question"] == FOUR_OPEN_POINTS[0]["question"]

    def test_a_timeout_ends_the_ask_for_the_remaining_questions(self):
        asked = []

        def callback(question, choices):
            asked.append(question)
            return {"user_response": "", "response_state": "timeout", "resolved": False, "reason_code": "clarify_timeout"}

        result = json.loads(clarify_tool(questions=FOUR_OPEN_POINTS, callback=callback))
        assert len(asked) == 1
        assert [a["state"] for a in result["answers"]] == ["timeout"] * 4

    def test_one_outcome_for_the_whole_card_applies_to_every_question(self):
        callback, _seen = _card_callback({"user_response": "", "response_state": "unavailable", "resolved": False})
        result = json.loads(clarify_tool(questions=FOUR_OPEN_POINTS[:3], callback=callback))
        assert [a["state"] for a in result["answers"]] == ["unavailable"] * 3

    def test_missing_answers_are_empty(self):
        callback, _seen = _card_callback(["Before the release"])
        result = json.loads(clarify_tool(questions=FOUR_OPEN_POINTS[:3], callback=callback))
        assert [a["state"] for a in result["answers"]] == ["answered", "empty", "empty"]

    def test_more_than_four_questions_is_an_error(self):
        five = FOUR_OPEN_POINTS + [{"question": "One more?"}]
        result = json.loads(clarify_tool(questions=five, callback=lambda q, c: "x"))
        assert "At most 4 questions" in result["error"]

    def test_questions_are_normalized(self):
        callback, seen = _card_callback(["a", "b"])
        clarify_tool(
            questions=[
                {"question": "  First?  ", "choices": ["1", " ", "2", "3", "4", "5"], "topic": "T" * 120},
                {"question": ""},
                "Second?",
            ],
            callback=callback,
        )
        first, second = seen["questions"]
        assert first == {"question": "First?", "choices": ["1", "2", "3", "4"], "topic": "T" * 80}
        assert second == {"question": "Second?"}

    def test_questions_as_a_json_string(self):
        callback, seen = _card_callback(["a", "b"])
        clarify_tool(questions=json.dumps(FOUR_OPEN_POINTS[:2]), callback=callback)
        assert len(seen["questions"]) == 2

    def test_bad_questions_shape_is_an_error(self):
        result = json.loads(clarify_tool(questions={"question": "x"}, callback=lambda q, c: "x"))
        assert "questions must be a list" in result["error"]
        result = json.loads(clarify_tool(questions=[{"question": "x", "choices": "a"}], callback=lambda q, c: "x"))
        assert "choices must be a list" in result["error"]

    def test_questions_win_over_question(self):
        callback, seen = _card_callback(["a", "b"])
        clarify_tool(question="Ignored heading?", questions=FOUR_OPEN_POINTS[:2], callback=callback)
        assert [q["question"] for q in seen["questions"]] == [q["question"] for q in FOUR_OPEN_POINTS[:2]]

    def test_callback_exception_returns_error(self):
        def callback(question, choices):
            raise RuntimeError("window closed")

        result = json.loads(clarify_tool(questions=FOUR_OPEN_POINTS[:2], callback=callback))
        assert "window closed" in result["error"]


class TestClarifySingleFormCompatibility:
    """The single form keeps its fields and gains the contract next to them."""

    def test_single_form_carries_legacy_fields_and_contract(self):
        result = json.loads(clarify_tool("Which region?", choices=["Bavaria", "Berlin"], callback=lambda q, c: "Bavaria"))
        assert result["question"] == "Which region?"
        assert result["choices_offered"] == ["Bavaria", "Berlin"]
        assert result["user_response"] == "Bavaria"
        assert result["response_state"] == "answered"
        assert result["resolved"] is True
        _assert_contract(result, 1)
        assert result["questions"] == [{"question": "Which region?", "choices": ["Bavaria", "Berlin"]}]
        assert result["answers"] == [{"state": "answered", "response": "Bavaria"}]

    def test_single_form_never_uses_the_card_path(self):
        calls = []

        def callback(question, choices):
            calls.append(question)
            return "Bavaria"

        callback.ask_questions = lambda questions: pytest.fail("card path for a plain question")
        clarify_tool("Which region?", callback=callback)
        assert calls == ["Which region?"]

    def test_one_entry_in_questions_reads_like_the_single_form(self):
        result = json.loads(clarify_tool(questions=[{"question": "Region?"}], callback=lambda q, c: "Bavaria"))
        assert result["question"] == "Region?"
        assert result["user_response"] == "Bavaria"
        assert result["answers"] == [{"state": "answered", "response": "Bavaria"}]

    def test_one_entry_with_a_topic_goes_to_the_card(self):
        callback, seen = _card_callback([{"state": "answered", "response": "Yes"}])
        result = json.loads(clarify_tool(questions=[FOUR_OPEN_POINTS[2]], callback=callback))
        assert seen["questions"][0]["topic"] == "Release 0.7.15"
        assert result["user_response"] == "Yes"
        assert result["question"] == "Ship the desktop build with this release?"

    def test_later_is_skipped_and_unresolved(self):
        result = json.loads(clarify_tool(
            "Which region?",
            callback=lambda q, c: {"user_response": "", "response_state": "skipped"},
        ))
        assert result["response_state"] == "skipped"
        assert result["resolved"] is False
        assert result["reason_code"] == "clarify_skipped"
        assert result["answers"] == [{"state": "skipped", "response": ""}]

    def test_unknown_states_are_mapped_into_the_contract(self):
        result = json.loads(clarify_tool(
            "Which region?",
            callback=lambda q, c: {"user_response": "Bavaria", "response_state": "picked"},
        ))
        assert result["response_state"] == "answered"
        _assert_contract(result, 1)
