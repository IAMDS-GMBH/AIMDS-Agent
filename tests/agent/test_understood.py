"""Tests for agent.understood — the per-turn "what the model understood" line (AIS-789)."""

import threading
from unittest.mock import MagicMock, patch

import pytest

from agent.understood import (
    TurnUnderstanding,
    generate_understood,
    last_assistant_text,
    sanitize_understood,
    wants_understanding,
)


@pytest.fixture(autouse=True)
def _understood_enabled(monkeypatch):
    """tests/conftest.py disables the side call process-wide."""
    monkeypatch.delenv("HERMES_DISABLE_UNDERSTOOD", raising=False)


def _response(text):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = text
    return response


class TestSanitize:
    def test_keeps_a_plain_line(self):
        assert sanitize_understood("Du willst die drei Optionen verglichen haben.") == (
            "Du willst die drei Optionen verglichen haben."
        )

    def test_none_and_empty_mean_no_line(self):
        assert sanitize_understood("NONE") is None
        assert sanitize_understood(" none. ") is None
        assert sanitize_understood("") is None
        assert sanitize_understood(None) is None

    def test_strips_think_blocks_prefixes_quotes_and_newlines(self):
        raw = '<think>the user wants…</think>\n"Understood: You want\na short summary."'
        assert sanitize_understood(raw) == "You want a short summary."

    def test_caps_at_a_word(self):
        line = sanitize_understood("You want " + "very " * 80 + "much.")
        assert len(line) <= 282
        assert line.endswith(" …")
        assert "  " not in line


class TestGenerate:
    def test_skips_slash_commands_skills_and_empty_turns(self):
        with patch("agent.understood.call_llm") as call:
            assert generate_understood("/compress") is None
            assert generate_understood("   ") is None
            assert generate_understood('[IMPORTANT: The user has invoked the "x" skill') is None
            call.assert_not_called()
        assert wants_understanding("Vergleiche die Optionen") is True
        assert wants_understanding(None) is False

    def test_prompt_is_english_and_names_the_language_rule(self):
        with patch("agent.understood.call_llm", return_value=_response("Du willst X.")) as call:
            assert generate_understood("Mach X", "Soll ich X oder Y machen?") == "Du willst X."
        kwargs = call.call_args.kwargs
        system, user = kwargs["messages"]
        assert kwargs["task"] == "title_generation"
        assert "language of the user's message" in system["content"]
        assert "no technical terms" in system["content"]
        assert "NONE" in system["content"]
        assert user["content"].startswith("Previous reply: Soll ich X oder Y machen?")
        assert user["content"].endswith("User message: Mach X")

    def test_caps_the_inputs(self):
        with patch("agent.understood.call_llm", return_value=_response("You want it.")) as call:
            generate_understood("q" * 5000, "z" * 5000)
        content = call.call_args.kwargs["messages"][1]["content"]
        assert content.count("q") == 1500
        assert content.count("z") == 500

    def test_failure_means_no_line(self):
        with patch("agent.understood.call_llm", side_effect=RuntimeError("no provider")):
            assert generate_understood("Vergleiche die Optionen") is None

    def test_last_assistant_text(self):
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [{"type": "text", "text": "Option A or B?"}]},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        ]
        assert last_assistant_text(history) == "Option A or B?"
        assert last_assistant_text([]) == ""


class TestTurnUnderstanding:
    def _run(self, persist_results, *, turn_first):
        emitted, persisted = [], []
        results = list(persist_results)

        def persist(text):
            persisted.append(text)
            return results.pop(0) if results else False

        turn = TurnUnderstanding(emit=emitted.append, persist=persist)
        gate = threading.Event()

        def fake_generate(*_args, **_kwargs):
            gate.wait(2)
            return "You want X."

        with patch("agent.understood.generate_understood", side_effect=fake_generate):
            assert turn.start("Do X") is True
            if turn_first:
                turn.turn_stored()
            gate.set()
            turn.thread.join(2)
            if not turn_first:
                turn.turn_stored()
        return emitted, persisted

    def test_line_before_the_stored_turn_is_written_when_the_turn_is_stored(self):
        emitted, persisted = self._run([False, True], turn_first=False)
        assert emitted == ["You want X."]
        assert persisted == ["You want X.", "You want X."]

    def test_line_after_the_stored_turn_is_written_once(self):
        emitted, persisted = self._run([True], turn_first=True)
        assert emitted == ["You want X."]
        assert persisted == ["You want X."]

    def test_stored_once_even_when_both_sides_try(self):
        emitted, persisted = self._run([True, True], turn_first=False)
        assert persisted == ["You want X."]

    def test_no_line_no_emit_no_write(self):
        emitted, persisted = [], []
        turn = TurnUnderstanding(emit=emitted.append, persist=lambda t: persisted.append(t) or True)
        with patch("agent.understood.generate_understood", return_value=None):
            turn.start("Do X")
            turn.thread.join(2)
        turn.turn_stored()
        assert emitted == [] and persisted == []

    def test_kill_switch(self, monkeypatch):
        monkeypatch.setenv("HERMES_DISABLE_UNDERSTOOD", "1")
        turn = TurnUnderstanding(emit=lambda t: None, persist=lambda t: True)
        assert turn.start("Do X") is False
        assert turn.thread is None


def test_live_chat_hint_asks_only_when_unclear():
    # AIS-789: ask back on an unclear request, never on a clear one.
    from agent.prompt_builder import PLATFORM_HINTS

    for platform in ("tui", "desktop"):
        hint = PLATFORM_HINTS[platform]
        assert "ask with clarify before working instead of guessing" in hint
        assert "A clear request gets no question." in hint
