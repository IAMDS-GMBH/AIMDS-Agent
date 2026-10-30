"""What the model understood: one short line per chat turn (AIS-789).

At the start of a desktop turn a cheap auxiliary call restates the user's
request in one or two sentences ("You want ..."), in the user's language.
The desktop pins it as a collapsed notice above the answer, so a user who
scrolls a long reply still sees what it answers — and can tell at a glance
when the request was taken the wrong way.

It runs beside the turn, like the session title: no change to the main
prompt and no latency for the main model. It uses the title-generation
model slot (the cheap model installs already configure for short calls).
Anything that goes wrong — no model, a timeout, an empty or unusable answer,
a greeting without a request — means no line, and the desktop shows no
notice. The line is stored on the user message's row
(``messages.understood``), so it survives a reload, a compression rotation
and the transcript sync.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any, Callable, Dict, Optional

from agent.agent_runtime_helpers import strip_think_blocks
from agent.auxiliary_client import call_llm

logger = logging.getLogger(__name__)

#: The prompt is English on purpose (fewer tokens); the answer follows the
#: language of the user's message.
_UNDERSTOOD_PROMPT = (
    "Restate a chat request so its author can check it was understood. Write "
    "one or two short sentences in the language of the user's message, "
    "addressed to the user, saying what they want (for example \"You want "
    "...\"). Use plain everyday words: no technical terms and no tool, file "
    "format or system names the user did not use. Do not answer the request, "
    "do not promise anything and add nothing the user did not say. Use the "
    "previous reply only to resolve references like \"do that\". If the "
    "message is only a greeting, a thank-you or a reaction without a request "
    "of its own, reply with NONE."
)

#: Inputs are capped so the call stays small whatever was pasted.
USER_MESSAGE_MAX_CHARS = 1500
PREVIOUS_REPLY_MAX_CHARS = 500
#: A longer answer is no longer "one line"; it is cut at a word.
UNDERSTOOD_MAX_CHARS = 280
DEFAULT_TIMEOUT_S = 20.0
#: Same slot as the session title: a short, cheap side call.
AUX_TASK = "title_generation"

_NONE_RE = re.compile(r"^\W*none\W*$", re.IGNORECASE)
_PREFIX_RE = re.compile(r"^(?:understood|summary|restatement)\s*:\s*", re.IGNORECASE)
_SKILL_RE = re.compile(r"\[IMPORTANT:\s+The user (?:has )?invoked", re.IGNORECASE)

EmitCallback = Callable[[str], None]
PersistCallback = Callable[[str], bool]


def disabled() -> bool:
    """Kill switch; the test suite sets it process-wide (no stray aux threads)."""
    return os.environ.get("HERMES_DISABLE_UNDERSTOOD", "").strip().lower() in ("1", "true", "yes")


def wants_understanding(user_message: Any) -> bool:
    """A typed request in plain text — not a slash command, skill bootstrap or empty turn."""
    if not isinstance(user_message, str):
        return False
    text = user_message.strip()
    return bool(text) and not text.startswith("/") and not _SKILL_RE.search(text)


def sanitize_understood(raw: Any) -> Optional[str]:
    """The model's answer as one display line, or None when it is unusable."""
    if not isinstance(raw, str):
        return None
    quotes = "\"'„“”«»"
    text = (strip_think_blocks(None, raw) or "").strip().strip(quotes).strip()
    text = _PREFIX_RE.sub("", text).strip().strip(quotes).strip()
    text = " ".join(text.split())
    if not text or _NONE_RE.match(text):
        return None
    if len(text) > UNDERSTOOD_MAX_CHARS:
        cut = text[:UNDERSTOOD_MAX_CHARS]
        space = cut.rfind(" ")
        text = (cut[:space] if space > UNDERSTOOD_MAX_CHARS // 2 else cut).rstrip(",;: ") + " …"
    return text


def generate_understood(
    user_message: str,
    previous_reply: str = "",
    *,
    main_runtime: Optional[Dict[str, Any]] = None,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> Optional[str]:
    """One or two sentences of what the user asks for, or None."""
    if not wants_understanding(user_message):
        return None
    user = user_message.strip()[:USER_MESSAGE_MAX_CHARS]
    previous = (previous_reply or "").strip()[:PREVIOUS_REPLY_MAX_CHARS]
    content = f"Previous reply: {previous}\n\nUser message: {user}" if previous else f"User message: {user}"
    try:
        response = call_llm(
            task=AUX_TASK,
            messages=[
                {"role": "system", "content": _UNDERSTOOD_PROMPT},
                {"role": "user", "content": content},
            ],
            max_tokens=200,
            temperature=0.2,
            timeout=timeout,
            main_runtime=main_runtime,
        )
        return sanitize_understood(response.choices[0].message.content)
    except Exception as exc:
        # A missing line is the designed fallback, not an incident.
        logger.info("Understood line skipped: %s", exc)
        return None


def last_assistant_text(history: Any) -> str:
    """Text of the newest assistant reply in an OpenAI-style history."""
    for message in reversed(history or []):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, list):
            content = " ".join(
                str(part.get("text") or "") for part in content if isinstance(part, dict)
            )
        if isinstance(content, str) and content.strip():
            return content
    return ""


class TurnUnderstanding:
    """The understood line of one turn: generated beside it, stored once.

    ``emit`` shows the line live as soon as it is ready. ``persist`` writes it
    to the turn's user message and returns False while that row does not exist
    yet (the agent stores the user message with its first tool round or at the
    end of the turn); :meth:`turn_stored` retries then. Whichever comes second
    — the line or the stored turn — writes it.
    """

    def __init__(self, emit: EmitCallback, persist: PersistCallback) -> None:
        self._emit = emit
        self._persist = persist
        self._lock = threading.Lock()
        self._text: Optional[str] = None
        self._stored = False
        self._turn_stored = False
        self.thread: Optional[threading.Thread] = None

    def start(
        self,
        user_message: str,
        previous_reply: str = "",
        *,
        main_runtime: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if disabled() or not wants_understanding(user_message):
            return False
        self.thread = threading.Thread(
            target=self._run,
            args=(user_message, previous_reply, main_runtime),
            daemon=True,
            name="turn-understood",
        )
        self.thread.start()
        return True

    def _run(self, user_message: str, previous_reply: str, main_runtime: Optional[Dict[str, Any]]) -> None:
        text = generate_understood(user_message, previous_reply, main_runtime=main_runtime)
        if not text:
            return
        with self._lock:
            self._text = text
        # Stored before it is shown, so a reload the event triggers finds it.
        self._try_persist()
        try:
            self._emit(text)
        except Exception:
            logger.debug("Understood line: emit failed", exc_info=True)

    def turn_stored(self) -> None:
        """The turn's messages are in the session DB now."""
        with self._lock:
            self._turn_stored = True
        self._try_persist()

    def _try_persist(self) -> None:
        with self._lock:
            if self._stored or not self._text:
                return
            try:
                self._stored = bool(self._persist(self._text))
            except Exception:
                logger.debug("Understood line: persist failed", exc_info=True)


__all__ = [
    "TurnUnderstanding",
    "generate_understood",
    "last_assistant_text",
    "sanitize_understood",
    "wants_understanding",
]
