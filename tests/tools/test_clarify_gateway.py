"""Tests for the gateway-side clarify primitive (tools/clarify_gateway.py).

The clarify tool needs to ask the user a question and block the agent
thread until they respond.  These tests cover the module-level state
machine: register, wait, resolve via button, resolve via text-fallback,
"Other"-button text-capture flip, timeout, session boundary cleanup.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest



def _clear_clarify_state():
    """Reset module-level state between tests."""
    from tools import clarify_gateway as cm
    with cm._lock:
        cm._entries.clear()
        cm._session_index.clear()
        cm._notify_cbs.clear()


class TestClarifyPrimitive:
    """Core register/wait/resolve mechanics."""

    def setup_method(self):
        _clear_clarify_state()

    def test_button_choice_resolves_wait(self):
        """resolve_gateway_clarify unblocks wait_for_response with the chosen string."""
        from tools import clarify_gateway as cm

        cm.register("id1", "sk1", "Pick one", ["A", "B", "C"])

        def resolver():
            time.sleep(0.05)
            cm.resolve_gateway_clarify("id1", "B")

        threading.Thread(target=resolver).start()
        result = cm.wait_for_response("id1", timeout=2.0)
        assert result == "B"

    def test_open_ended_auto_awaits_text(self):
        """Clarify with no choices is in text-capture mode immediately."""
        from tools import clarify_gateway as cm

        entry = cm.register("id2", "sk2", "Free form?", None)
        assert entry.awaiting_text is True

        # get_pending_for_session returns the entry so the gateway
        # text-intercept can find it.
        pending = cm.get_pending_for_session("sk2")
        assert pending is not None
        assert pending.clarify_id == "id2"

    def test_button_choice_does_not_auto_await(self):
        """Multi-choice clarify should NOT be in text-capture mode initially."""
        from tools import clarify_gateway as cm

        entry = cm.register("id3", "sk3", "Pick", ["X", "Y"])
        assert entry.awaiting_text is False
        assert cm.get_pending_for_session("sk3") is None

    def test_other_button_flips_to_text_mode(self):
        """mark_awaiting_text makes get_pending_for_session find the entry."""
        from tools import clarify_gateway as cm

        cm.register("id4", "sk4", "Pick", ["X", "Y"])
        assert cm.get_pending_for_session("sk4") is None

        flipped = cm.mark_awaiting_text("id4")
        assert flipped is True

        pending = cm.get_pending_for_session("sk4")
        assert pending is not None
        assert pending.clarify_id == "id4"

    def test_mark_awaiting_text_unknown_id(self):
        """mark_awaiting_text on a non-existent id returns False."""
        from tools import clarify_gateway as cm

        assert cm.mark_awaiting_text("nope") is False

    def test_timeout_returns_none(self):
        """wait_for_response returns None when no resolve fires within the timeout."""
        from tools import clarify_gateway as cm

        cm.register("id5", "sk5", "Q?", ["A"])
        result = cm.wait_for_response("id5", timeout=0.2)
        assert result is None

    def test_resolve_unknown_id_returns_false(self):
        """resolve_gateway_clarify is idempotent on unknown ids."""
        from tools import clarify_gateway as cm

        assert cm.resolve_gateway_clarify("nope", "anything") is False

    def test_resolve_after_wait_completes_is_noop(self):
        """A late resolve on a finished entry doesn't blow up."""
        from tools import clarify_gateway as cm

        cm.register("id6", "sk6", "Q?", ["A"])
        # Time out, entry gets cleaned up
        cm.wait_for_response("id6", timeout=0.1)
        # Late button click — should not raise
        result = cm.resolve_gateway_clarify("id6", "A")
        assert result is False

    def test_clear_session_cancels_pending_entries(self):
        """clear_session unblocks blocked threads with empty response."""
        from tools import clarify_gateway as cm

        cm.register("id7", "sk7", "Q?", ["A"])

        def waiter():
            return cm.wait_for_response("id7", timeout=10.0)

        with ThreadPoolExecutor(1) as pool:
            fut = pool.submit(waiter)
            time.sleep(0.05)
            cancelled = cm.clear_session("sk7")
            assert cancelled == 1
            result = fut.result(timeout=2.0)
            # clear_session sets response="" then the wait returns it
            assert result == ""

    def test_has_pending(self):
        from tools import clarify_gateway as cm

        cm.register("id8", "sk8", "Q?", ["A"])
        assert cm.has_pending("sk8") is True
        assert cm.has_pending("nonexistent") is False

    def test_notify_register_unregister_clears_pending(self):
        """unregister_notify cancels any pending clarify so threads unwind."""
        from tools import clarify_gateway as cm

        cm.register("id9", "sk9", "Q?", ["A"])

        def waiter():
            return cm.wait_for_response("id9", timeout=10.0)

        with ThreadPoolExecutor(1) as pool:
            fut = pool.submit(waiter)
            time.sleep(0.05)

            cm.register_notify("sk9", lambda entry: None)
            cm.unregister_notify("sk9")

            # unregister_notify calls clear_session; thread unwinds
            result = fut.result(timeout=2.0)
            assert result == ""

    def test_session_index_isolation(self):
        """Entries from different sessions don't leak across get_pending lookups."""
        from tools import clarify_gateway as cm

        cm.register("idA", "alpha", "Q?", None)  # auto-await text
        cm.register("idB", "beta", "Q?", None)   # auto-await text

        a = cm.get_pending_for_session("alpha")
        b = cm.get_pending_for_session("beta")
        assert a is not None and a.clarify_id == "idA"
        assert b is not None and b.clarify_id == "idB"

    def test_clarify_timeout_config_default(self):
        """get_clarify_timeout returns 600 by default."""
        from tools import clarify_gateway as cm

        timeout = cm.get_clarify_timeout()
        # Default 600s OR whatever is in the user's loaded config.
        # Floor check: must be a positive int, not crashed.
        assert isinstance(timeout, int)
        assert timeout > 0


class TestGatewayTextIntercept:
    """The gateway's _handle_message intercepts text replies to pending clarifies."""

    def setup_method(self):
        _clear_clarify_state()

    def test_get_pending_for_session_returns_oldest_text_awaiting(self):
        """When two clarifies are pending, get_pending_for_session returns the
        first that is awaiting_text (the older one if both)."""
        from tools import clarify_gateway as cm

        # Older multi-choice (not awaiting text)
        cm.register("first", "sk", "Q1?", ["A"])
        # Newer open-ended (awaiting text)
        cm.register("second", "sk", "Q2?", None)

        pending = cm.get_pending_for_session("sk")
        # The newer one is awaiting text; the older isn't.
        assert pending is not None
        assert pending.clarify_id == "second"

        # Now flip the first to text mode too.  Both are awaiting text,
        # FIFO returns the older one.
        cm.mark_awaiting_text("first")
        pending2 = cm.get_pending_for_session("sk")
        assert pending2 is not None
        assert pending2.clarify_id == "first"
    def test_text_fallback_enables_awaiting_text_for_multi_choice(self):
        """When base send_clarify renders choices as text, mark_awaiting_text
        is called so the gateway text-intercept can capture the reply."""
        from tools import clarify_gateway as cm

        entry = cm.register("id-tf", "sk-tf", "Pick one", ["A", "B", "C"])
        # Initially, multi-choice does NOT await text (button path)
        assert entry.awaiting_text is False

        # After the base send_clarify text fallback calls mark_awaiting_text:
        flipped = cm.mark_awaiting_text("id-tf")
        assert flipped is True

        # Now get_pending_for_session should find it
        pending = cm.get_pending_for_session("sk-tf")
        assert pending is not None
        assert pending.clarify_id == "id-tf"
        
        # Clean up
        cm.clear_session("sk-tf")


class TestClarifyTimeoutResolution:
    """AIS-333: one resolution order for every surface."""

    def test_clarify_section_wins(self):
        from tools import clarify_gateway as cm

        assert cm.get_clarify_timeout({"clarify": {"timeout": 45}, "agent": {"clarify_timeout": 300}}) == 45

    def test_agent_section_is_the_fallback(self):
        from tools import clarify_gateway as cm

        assert cm.get_clarify_timeout({"agent": {"clarify_timeout": "300"}}) == 300

    def test_default_is_600(self):
        from tools import clarify_gateway as cm

        assert cm.get_clarify_timeout({}) == 600
        assert cm.get_clarify_timeout({"clarify": {"timeout": ""}, "agent": {}}) == 600

    def test_non_positive_and_garbage_fall_through(self):
        from tools import clarify_gateway as cm

        assert cm.get_clarify_timeout({"clarify": {"timeout": 0}, "agent": {"clarify_timeout": 90}}) == 90
        assert cm.get_clarify_timeout({"clarify": {"timeout": "soon"}}) == 600


# AIS-793: four open points as four questions with topics.
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


@pytest.fixture
def english(monkeypatch):
    from agent.i18n import reset_language_cache

    monkeypatch.setenv("HERMES_LANGUAGE", "en")
    reset_language_cache()
    yield
    reset_language_cache()


class TestQuestionsReplyParsing:
    """AIS-793: one messaging reply answers several numbered questions."""

    def _parse(self, text, open_indexes=None):
        from tools.clarify_gateway import parse_questions_reply

        return parse_questions_reply(text, FOUR_OPEN_POINTS, open_indexes)

    def test_number_letter_pairs(self, english):
        assert self._parse("1b 2a 3B") == {
            0: {"state": "answered", "response": "After the release"},
            1: {"state": "answered", "response": "Johannes"},
            2: {"state": "answered", "response": "No, next week"},
        }

    def test_colon_free_text_and_later(self, english):
        found = self._parse("1: before, please\n2) The product team\n4: into the project notes, see item 2 there\n3 later")
        assert found[0] == {"state": "answered", "response": "before, please"}
        # Choice text is matched case-insensitively and kept as offered.
        assert found[1] == {"state": "answered", "response": "The product team"}
        assert found[2] == {"state": "skipped", "response": ""}
        assert found[3] == {"state": "answered", "response": "into the project notes, see item 2 there"}

    def test_line_numbers_and_a_date_inside_an_answer(self, english):
        found = self._parse("1. a\n4. on 3. November in the wiki")
        assert found == {
            0: {"state": "answered", "response": "Before the release"},
            3: {"state": "answered", "response": "on 3. November in the wiki"},
        }

    def test_choice_number_after_a_colon(self, english):
        assert self._parse("2: 3")[1] == {"state": "answered", "response": "The product team"}

    def test_plain_letters_answer_the_open_questions_in_order(self, english):
        assert self._parse("b, a", open_indexes=[1, 2]) == {
            1: {"state": "answered", "response": "Tobias"},
            2: {"state": "answered", "response": "Yes"},
        }

    def test_plain_later_defers_every_open_question(self, english):
        assert self._parse("later", open_indexes=[2, 3]) == {
            2: {"state": "skipped", "response": ""},
            3: {"state": "skipped", "response": ""},
        }
        assert self._parse("Später!", open_indexes=[3]) == {3: {"state": "skipped", "response": ""}}

    def test_plain_text_answers_the_only_open_question(self, english):
        assert self._parse("In the project notes", open_indexes=[3]) == {
            3: {"state": "answered", "response": "In the project notes"},
        }

    def test_unrecognised_text_with_several_open_questions(self, english):
        assert self._parse("no idea yet") == {}
        # Numbers beyond the questions are no markers.
        assert self._parse("7: something", open_indexes=[0, 1]) == {}

    def test_localized_later_word(self, monkeypatch):
        from agent.i18n import reset_language_cache
        from tools.clarify_gateway import parse_questions_reply

        monkeypatch.setenv("HERMES_LANGUAGE", "fr")
        reset_language_cache()
        try:
            assert parse_questions_reply("2 plus tard", FOUR_OPEN_POINTS)[1] == {"state": "skipped", "response": ""}
        finally:
            reset_language_cache()


class TestQuestionsAsk:
    """AIS-793: register several questions, answer them over one or more replies."""

    def setup_method(self):
        _clear_clarify_state()

    def test_message_has_numbered_blocks_with_lettered_choices(self, english):
        from tools.clarify_gateway import format_questions_message

        text = format_questions_message(FOUR_OPEN_POINTS)
        assert text.startswith("❓ 4 questions — answer in one message")
        assert '"2 later"' in text
        assert "\n\n1. QS-38 — Should the load test run before or after the release?\n   a) Before the release\n   b) After the release" in text
        assert "\n\n4. Docs — Where should the release notes go?" in text
        assert text.count(") ") == 7  # 2 + 3 + 2 lettered choices

    def test_partial_replies_then_complete(self, english):
        from tools import clarify_gateway as cm

        entry = cm.register("q1", "sk-q", "first", None, questions=FOUR_OPEN_POINTS)
        assert entry.awaiting_text is True
        assert cm.get_pending_for_session("sk-q") is entry

        assert cm.submit_questions_reply("q1", "1a 2c") == [3, 4]
        assert cm.format_still_open([3, 4], FOUR_OPEN_POINTS) == 'Still open: 3, 4. Answer e.g. with "3a", or write "later".'
        assert cm.format_still_open([4], FOUR_OPEN_POINTS) == 'Still open: 4. Answer e.g. with "4: …", or write "later".'
        assert cm.submit_questions_reply("q1", "hmm") == [3, 4]
        # A later reply may correct an earlier answer.
        assert cm.submit_questions_reply("q1", "2b 3 later") == [4]

        result = {}

        def waiter():
            result["answers"] = cm.wait_for_answers("q1", timeout=2.0)

        thread = threading.Thread(target=waiter)
        thread.start()
        time.sleep(0.05)
        assert cm.submit_questions_reply("q1", "In the wiki") == []
        thread.join(timeout=3)

        assert result["answers"] == [
            {"state": "answered", "response": "Before the release"},
            {"state": "answered", "response": "Tobias"},
            {"state": "skipped", "response": ""},
            {"state": "answered", "response": "In the wiki"},
        ]
        assert cm.submit_questions_reply("q1", "1a") is None

    def test_timeout_keeps_the_answers_given_so_far(self, english):
        from tools import clarify_gateway as cm

        cm.register("q2", "sk-q2", "first", None, questions=FOUR_OPEN_POINTS[:2])
        assert cm.submit_questions_reply("q2", "1b") == [2]
        assert cm.wait_for_answers("q2", timeout=0.1) == [
            {"state": "answered", "response": "After the release"},
            {"state": "timeout", "response": ""},
        ]
        assert not cm.has_pending("sk-q2")

    def test_session_end_releases_the_ask(self):
        from tools import clarify_gateway as cm

        cm.register("q3", "sk-q3", "first", None, questions=FOUR_OPEN_POINTS[:2])
        threading.Timer(0.05, lambda: cm.clear_session("sk-q3")).start()
        assert [a["state"] for a in cm.wait_for_answers("q3", timeout=2.0)] == ["timeout", "timeout"]

    def test_a_single_question_entry_is_not_a_questions_ask(self):
        from tools import clarify_gateway as cm

        cm.register("s1", "sk-s", "Free form?", None)
        assert cm.submit_questions_reply("s1", "1a") is None
        assert cm.submit_questions_reply("missing", "1a") is None
