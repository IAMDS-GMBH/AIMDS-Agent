"""AIS-524: the end of a stopped turn as the user and support see it."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from agent.turn_end_report import (
    SUPPORT_MARKER,
    StoppedTurnExplanation,
    explain_stopped_turn,
    offer_problem_report,
    split_explanation,
)


def _agent(answer=None, clarify=True, raises=False):
    def _toolless(messages, instruction):
        if raises:
            raise RuntimeError("provider down")
        return answer

    asked = []

    def _clarify(question, choices):
        asked.append((question, choices))
        return clarify if isinstance(clarify, str) else choices[0]

    agent = SimpleNamespace(
        _request_toolless_answer=_toolless,
        clarify_callback=_clarify if clarify else None,
        session_id="s-1",
        statuses=[],
    )
    agent._emit_status = agent.statuses.append
    agent.asked = asked
    return agent


def test_split_keeps_user_and_support_parts_apart():
    user, support = split_explanation(f"<think>x</think>Hallo.\n{SUPPORT_MARKER}\nSupport text.")
    assert (user, support) == ("Hallo.", "Support text.")
    assert split_explanation("Nur Text") == ("Nur Text", "")
    assert split_explanation(None) == ("", "")


def test_model_text_is_used_and_capped():
    agent = _agent(answer="Kurz erklärt.\n" + SUPPORT_MARKER + "\nWhat failed.")
    result = explain_stopped_turn(agent, [], reason="r", fallback_text="F", fallback_support="FS")
    assert result == StoppedTurnExplanation("Kurz erklärt.", "What failed.", True)


def test_missing_support_part_uses_the_fallback_summary():
    result = explain_stopped_turn(_agent(answer="Kurz."), [], reason="r", fallback_text="F", fallback_support="FS")
    assert result.support_summary == "FS" and result.user_text == "Kurz."


def test_failure_or_empty_answer_falls_back():
    for agent in (_agent(answer=""), _agent(raises=True)):
        result = explain_stopped_turn(agent, [], reason="r", fallback_text="F", fallback_support="FS")
        assert result == StoppedTurnExplanation("F", "FS", False)


def test_report_only_on_yes():
    explanation = StoppedTurnExplanation("U", "S", True)
    with patch("hermes_cli.auto_incidents.report_in_background") as incident:
        yes = _agent(clarify=True)
        assert offer_problem_report(yes, kind="k", summary="s", explanation=explanation,
                                    category="chat_issue", context_type="turn_stopped") is True
        assert incident.call_count == 1
        assert incident.call_args.args[2].startswith("S")
        assert yes.statuses, "the user is told the report went out"

        no = _agent(clarify="Nein")
        assert offer_problem_report(no, kind="k", summary="s", explanation=explanation,
                                    category="chat_issue", context_type="turn_stopped") is False
        none = _agent(clarify=False)
        assert offer_problem_report(none, kind="k", summary="s", explanation=explanation,
                                    category="chat_issue", context_type="turn_stopped") is False
        assert incident.call_count == 1
