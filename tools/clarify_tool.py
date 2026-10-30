#!/usr/bin/env python3
"""
Clarify Tool Module - Interactive Clarifying Questions

Allows the agent to present structured multiple-choice questions or open-ended
prompts to the user. In CLI mode, choices are navigable with arrow keys. On
messaging platforms, choices are rendered as a numbered list.

The actual user-interaction logic lives in the platform layer (cli.py for CLI,
gateway/run.py for messaging). This module defines the schema, validation, and
a thin dispatcher that delegates to a platform-provided callback.

Platform callbacks
------------------
``callback(question, choices)`` asks one question and returns the answer: a
string, or a dict with ``user_response`` / ``response_state`` / ``resolved`` /
``reason_code`` (and ``timeout_seconds``).

AIS-793: several open decisions travel in one call (``questions``). A platform
that shows them together (desktop question card, TUI, messaging) also exposes
``callback.ask_questions(questions)``: it receives the normalized
``[{question, choices?, topic?}]`` list and returns one answer per question in
the same order (each a string or such a dict; ``state`` / ``response`` are
accepted as well). Without it the questions are asked one after another through
``callback`` — the CLI and every older platform keep working unchanged.

The result carries the ``clarify_questions`` contract (``v``, ``questions``,
``answers``; agent/contracts/clarify_questions.schema.json). A single question
keeps the original fields next to it.
"""

import json
import re
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple


# Maximum number of predefined choices the agent can offer.
# A 5th "Other (type your answer)" option is always appended by the UI.
MAX_CHOICES = 4

# AIS-793: at most this many questions in one call, so the card stays readable.
MAX_QUESTIONS = 4
# clarify_questions contract: topic maxLength and version.
MAX_TOPIC_CHARS = 80
CLARIFY_CONTRACT_VERSION = 1

# Answer states of the clarify_questions contract (also the single form's
# ``response_state``). "skipped" is the user's "later": no answer now, the
# question is kept in the workspace open questions for the morning brief.
ANSWER_STATES = ("answered", "skipped", "timeout", "empty", "delivery_failed", "unavailable")
# A platform that timed out or could not deliver one question will not reach
# the user for the next one either — the rest inherit that state unasked.
_TERMINAL_STATES = frozenset({"timeout", "delivery_failed", "unavailable"})

# Thread-local access to the platform's clarify callback. Set by the tool
# executor before dispatching *any* tool call (mirroring the
# tools.environments.base activity-callback pattern) so that other tools
# (e.g. outlook_write_email) can invoke a real, interactive confirmation
# synchronously from within their own execution — instead of relying on the
# model to correctly orchestrate a separate 'clarify' tool call plus a
# follow-up call repeating all the original arguments. That two-call
# choreography was proven unreliable in practice (models skipped showing the
# preview, mangled the follow-up JSON, or resent it with fields missing/
# empty), so tools with irreversible side effects (sending an email, etc.)
# should prefer resolving confirmation internally via this callback whenever
# it's available, only falling back to the multi-call contract when it isn't
# (e.g. non-interactive contexts).
_clarify_callback_local = threading.local()


def set_clarify_callback(cb: Optional[Callable[[str, Optional[List[str]]], Any]]) -> None:
    """Register the platform's clarify callback for the current thread."""
    _clarify_callback_local.callback = cb


def get_clarify_callback() -> Optional[Callable[[str, Optional[List[str]]], Any]]:
    return getattr(_clarify_callback_local, "callback", None)

def _normalize_legacy_response_state(response: str) -> tuple[str, bool, str]:
    lowered = str(response or "").strip().lower()
    if lowered.startswith("[clarify prompt could not be delivered]"):
        return "delivery_failed", False, "clarify_prompt_delivery_failed"
    if lowered.startswith("[user did not respond within"):
        return "timeout", False, "clarify_timeout"
    if not lowered:
        return "empty", False, "clarify_empty_response"
    return "answered", True, ""


def _normalize_answer(callback_result: Any) -> Dict[str, Any]:
    """One platform answer as ``user_response`` / ``response_state`` / ``resolved`` /
    ``reason_code`` / ``timeout_seconds``."""
    if isinstance(callback_result, dict):
        user_response = str(
            callback_result.get("user_response") or callback_result.get("response") or ""
        ).strip()
        response_state = str(
            callback_result.get("response_state") or callback_result.get("state") or ""
        ).strip().lower()
        reason_code = str(callback_result.get("reason_code") or "").strip().lower()
        resolved_raw = callback_result.get("resolved")
        if response_state:
            if response_state not in ANSWER_STATES:
                response_state = "answered" if user_response else "empty"
            resolved = bool(resolved_raw) if isinstance(resolved_raw, bool) else response_state == "answered"
        else:
            response_state, resolved, inferred_reason = _normalize_legacy_response_state(user_response)
            reason_code = reason_code or inferred_reason
        try:
            timeout_seconds = int(callback_result.get("timeout_seconds") or 0)
        except (TypeError, ValueError):
            timeout_seconds = 0
    else:
        user_response = str(callback_result).strip()
        response_state, resolved, reason_code = _normalize_legacy_response_state(user_response)
        timeout_seconds = 0
    if response_state == "skipped":
        resolved = False
        reason_code = reason_code or "clarify_skipped"
    return {
        "user_response": user_response,
        "response_state": response_state,
        "resolved": resolved,
        "reason_code": reason_code,
        "timeout_seconds": max(timeout_seconds, 0),
    }


def _normalize_choices(choices: Any) -> Tuple[Optional[List[str]], str]:
    """Trimmed, non-empty choices (at most MAX_CHOICES), or None for open-ended."""
    if choices is None:
        return None, ""
    if not isinstance(choices, list):
        return None, "choices must be a list of strings."
    cleaned = [str(c).strip() for c in choices if str(c).strip()][:MAX_CHOICES]
    return (cleaned or None), ""


def _normalize_questions(questions: Any) -> Tuple[List[Dict[str, Any]], str]:
    """The ``questions`` argument as contract items ``{question, choices?, topic?}``."""
    shape_error = "questions must be a list of {question, choices?, topic?} objects."
    if isinstance(questions, str) and questions.strip().startswith("["):
        # Some models send the array as a JSON string.
        try:
            questions = json.loads(questions)
        except ValueError:
            return [], shape_error
    if not isinstance(questions, list):
        return [], shape_error
    items: List[Dict[str, Any]] = []
    for raw in questions:
        if isinstance(raw, str):
            raw = {"question": raw}
        if not isinstance(raw, dict):
            return [], shape_error
        text = str(raw.get("question") or "").strip()
        if not text:
            continue
        choices, error = _normalize_choices(raw.get("choices"))
        if error:
            return [], f"questions[].{error}"
        item: Dict[str, Any] = {"question": text}
        if choices:
            item["choices"] = choices
        topic = re.sub(r"\s+", " ", str(raw.get("topic") or "")).strip()[:MAX_TOPIC_CHARS].strip()
        if topic:
            item["topic"] = topic
        items.append(item)
    if len(items) > MAX_QUESTIONS:
        return [], f"At most {MAX_QUESTIONS} questions per call; ask the most important ones first."
    return items, ""


def _prompt_text(item: Dict[str, Any], index: int, total: int) -> str:
    """A question for a platform that asks one at a time: ``(2/3) QS-38: …``."""
    text = item["question"]
    if item.get("topic"):
        text = f"{item['topic']}: {text}"
    if total > 1:
        text = f"({index + 1}/{total}) {text}"
    return text


def _ask(items: List[Dict[str, Any]], callback: Callable) -> List[Dict[str, Any]]:
    """Ask every item through the platform; one normalized answer per item."""
    ask_many = getattr(callback, "ask_questions", None)
    if callable(ask_many) and (len(items) > 1 or "topic" in items[0]):
        raw = ask_many([dict(item) for item in items])
        if isinstance(raw, dict) and isinstance(raw.get("answers"), list):
            raw = raw["answers"]
        if not isinstance(raw, list):
            # One outcome for the whole ask (e.g. the platform is unavailable).
            raw = [raw] * len(items)
        answers = [_normalize_answer(entry) for entry in raw[: len(items)]]
        while len(answers) < len(items):
            answers.append(_normalize_answer(""))
        return answers

    answers: List[Dict[str, Any]] = []
    for index, item in enumerate(items):
        if answers and answers[-1]["response_state"] in _TERMINAL_STATES:
            answers.append({**answers[-1], "user_response": ""})
            continue
        answers.append(
            _normalize_answer(callback(_prompt_text(item, index, len(items)), item.get("choices")))
        )
    return answers


def clarify_tool(
    question: str = "",
    choices: Optional[List[str]] = None,
    callback: Optional[Callable] = None,
    questions: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    Ask the user one question, or several in one ask, optionally with choices.

    Args:
        question:  The question text (single-question form).
        choices:   Up to 4 predefined answer choices. When omitted the
                   question is purely open-ended.
        callback:  Platform-provided function that handles the actual UI
                   interaction. Signature: callback(question, choices) -> str,
                   optionally with ``callback.ask_questions`` (see module doc).
                   Injected by the agent runner (cli.py / gateway).
        questions: AIS-793: up to 4 ``{question, choices?, topic?}`` entries,
                   one per decision. Takes precedence over ``question``.

    Returns:
        JSON string with the user's response(s).
    """
    items: List[Dict[str, Any]] = []
    if questions not in (None, "", []):
        items, error = _normalize_questions(questions)
        if error:
            return tool_error(error)

    if not items:
        if not question or not str(question).strip():
            return tool_error("Question text is required.")
        single_choices, error = _normalize_choices(choices)
        if error:
            return tool_error(error)
        item: Dict[str, Any] = {"question": str(question).strip()}
        if single_choices:
            item["choices"] = single_choices
        items = [item]

    if callback is None:
        return json.dumps(
            {"error": "Clarify tool is not available in this execution context."},
            ensure_ascii=False,
        )

    try:
        answers = _ask(items, callback)
    except Exception as exc:
        return json.dumps(
            {"error": f"Failed to get user input: {exc}"},
            ensure_ascii=False,
        )

    contract = {
        "v": CLARIFY_CONTRACT_VERSION,
        "questions": items,
        "answers": [
            {"state": answer["response_state"], "response": answer["user_response"]}
            for answer in answers
        ],
    }
    if len(items) > 1:
        return json.dumps(contract, ensure_ascii=False)

    answer = answers[0]
    payload = {
        "question": items[0]["question"],
        "choices_offered": items[0].get("choices"),
        "user_response": answer["user_response"],
        "response_state": answer["response_state"],
        "resolved": answer["resolved"],
        "reason_code": answer["reason_code"],
    }
    # AIS-333: a platform that knows its deadline reports it so the UI can say
    # "timed out after N s" instead of silently collapsing the question card.
    if answer["timeout_seconds"] > 0:
        payload["timeout_seconds"] = answer["timeout_seconds"]
    payload.update(contract)
    return json.dumps(payload, ensure_ascii=False)


def check_clarify_requirements() -> bool:
    """Clarify tool has no external requirements -- always available."""
    return True


# =============================================================================
# OpenAI Function-Calling Schema
# =============================================================================

CLARIFY_SCHEMA = {
    "name": "clarify",
    "description": (
        "Ask the user when you need clarification, feedback, or a decision "
        "before proceeding. Multiple choice: up to 4 choices, the UI adds an "
        "'Other' option for a typed answer. Open-ended: omit choices. Several "
        "open decisions: one `questions` entry per decision in one call, not "
        "one combined question.\n\n"
        "Use it when the task is ambiguous and the user should choose the "
        "approach, for post-task feedback, to offer saving a skill or memory, "
        "or when a decision has trade-offs the user should weigh in on. Not "
        "for yes/no confirmation of dangerous commands (the terminal tool "
        "handles that). Prefer a reasonable default yourself when the decision "
        "is low-stakes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question (single-question form).",
            },
            "choices": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": MAX_CHOICES,
                "description": "Up to 4 answer choices; omit for an open-ended question.",
            },
            "questions": {
                "type": "array",
                "maxItems": MAX_QUESTIONS,
                "description": "Instead of question: several questions, each answered on its own.",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "choices": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": MAX_CHOICES,
                        },
                        "topic": {
                            "type": "string",
                            "description": "Short label, e.g. a ticket key.",
                        },
                    },
                    "required": ["question"],
                },
            },
        },
        # `question` or `questions`; checked at runtime (a top-level anyOf is
        # not accepted by every provider).
        "required": [],
    },
}


# --- Registry ---
from tools.registry import registry, tool_error

registry.register(
    name="clarify",
    toolset="clarify",
    schema=CLARIFY_SCHEMA,
    handler=lambda args, **kw: clarify_tool(
        question=args.get("question", ""),
        choices=args.get("choices"),
        callback=kw.get("callback"),
        questions=args.get("questions")),
    check_fn=check_clarify_requirements,
    emoji="❓",
)
