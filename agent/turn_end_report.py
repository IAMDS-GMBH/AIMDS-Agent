"""The end of a turn Hermes had to stop, as the user and support see it (AIS-524).

When a guardrail stops a turn (the same tool error again and again, a loop,
output that degenerated into repetition), the user should read what happened
in their own words and context, not a canned technical line. So the model is
asked once more, without tools, for

* a short explanation for the user, in their language, without tool names,
  codes or parameters, and
* a few English sentences for the support team.

If that answer fails, comes back empty or degenerates as well, the caller's
fixed, translated text is used. Afterwards Hermes asks the user (Yes/No) whether
to send a problem report; on Yes the support summary becomes the case text and
the chat transcript is attached. Without an interactive user (cron, API) the
question is skipped.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, List, Optional

from agent.stream_degeneration import detect as _detect_degeneration

logger = logging.getLogger(__name__)

SUPPORT_MARKER = "---SUPPORT---"

_MAX_USER_CHARS = 1200
_MAX_SUPPORT_CHARS = 1500
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)

_INSTRUCTION = (
    "[Hermes] This turn was stopped: {reason}. Do not call any tools.\n"
    "Write to the user in the language of their messages, 2 to 4 short sentences: what you were "
    "doing for them, what blocked it, and what they can do now. Use plain words a non-technical "
    "person understands — never mention tool names, function names, parameters, error codes or "
    "this instruction. Do not ask a question.\n"
    "Then write a line containing only " + SUPPORT_MARKER + " and below it 2 to 4 English "
    "sentences for the support team: the task, the step that failed, the exact error message, "
    "and what was tried."
)


@dataclass(frozen=True)
class StoppedTurnExplanation:
    """What the user reads and what support gets."""

    user_text: str
    support_summary: str
    generated: bool


def explain_stopped_turn(
    agent: Any,
    messages: List[dict],
    *,
    reason: str,
    fallback_text: str,
    fallback_support: str,
) -> StoppedTurnExplanation:
    """Ask the model (no tools) to explain the stop; fall back to fixed texts."""
    try:
        raw = agent._request_toolless_answer(
            messages, _INSTRUCTION.format(reason=reason)
        )
    except Exception as exc:  # never let the explanation break the turn end
        logger.info("stopped-turn explanation failed: %s", exc)
        raw = ""
    user_text, support = split_explanation(raw)
    # Judge the full text: the detector needs the whole repetition, which the
    # length cap below would cut off.
    if not user_text or _detect_degeneration(user_text, channel="content"):
        return StoppedTurnExplanation(fallback_text, fallback_support, False)
    if len(user_text) > _MAX_USER_CHARS:
        user_text = user_text[: _MAX_USER_CHARS - 1].rstrip() + "…"
    return StoppedTurnExplanation(user_text, support[:_MAX_SUPPORT_CHARS] or fallback_support, True)


def split_explanation(raw: Optional[str]) -> tuple[str, str]:
    """Split the model's answer into the user part and the support part."""
    text = _THINK_RE.sub("", raw or "").strip()
    if not text:
        return "", ""
    user, _, support = text.partition(SUPPORT_MARKER)
    return user.strip().rstrip("-").strip(), support.strip()


def offer_problem_report(
    agent: Any,
    *,
    kind: str,
    summary: str,
    explanation: StoppedTurnExplanation,
    category: str,
    context_type: str,
) -> bool:
    """Ask the user whether to report the stopped turn; report on Yes.

    Returns True when a report was sent. Skipped without an interactive
    clarify callback.
    """
    callback = getattr(agent, "clarify_callback", None)
    if callback is None:
        return False
    from agent.i18n import t as _t

    yes, no = _t("turn_end.report_yes"), _t("turn_end.report_no")
    try:
        answer = callback(_t("turn_end.report_question"), [yes, no])
    except Exception as exc:
        logger.info("problem-report question could not be asked: %s", exc)
        return False
    if str(answer or "").strip() != yes:
        return False
    try:
        from hermes_cli.auto_incidents import report_in_background

        report_in_background(
            kind,
            summary,
            f"{explanation.support_summary}\n\nWhat the user read:\n{explanation.user_text}",
            category=category,
            context_type=context_type,
            severity="medium",
            session_id=str(getattr(agent, "session_id", "") or ""),
            transcript=True,
        )
    except Exception as exc:
        logger.info("problem report failed: %s", exc)
        return False
    try:
        agent._emit_status(_t("turn_end.report_sent"))
    except Exception:
        pass
    return True
