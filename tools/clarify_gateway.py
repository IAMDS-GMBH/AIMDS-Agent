"""Gateway-side clarify primitive (blocking event-based queue).

The ``clarify`` tool needs to ask the user a question and block the agent
thread until they respond.  In CLI mode this is trivial — ``input()`` is
synchronous.  In gateway mode the agent runs on a worker thread while the
event loop handles the user's reply, so we need a thread-safe primitive
that:

  * stores a pending clarify request (with a generated ``clarify_id``),
  * blocks the agent thread on an ``Event``,
  * resolves the wait when the gateway's button-callback or text-intercept
    fires ``resolve_gateway_clarify(clarify_id, response)``,
  * supports timeouts so a user who never responds does NOT hang the agent
    thread forever (which would also pin the gateway's running-agent guard).

State is module-level (same shape as ``tools.approval``) so platform
adapters can call ``resolve_gateway_clarify`` without holding a back-
reference to the ``GatewayRunner`` instance.

Two delivery paths from the adapter:

  1. **Button UI** — adapters override ``send_clarify`` to render inline
     buttons (e.g. Telegram ``InlineKeyboardMarkup``).  The button
     callback resolves with the chosen string.  A final "Other (type
     answer)" button enters text-capture mode for free-form responses.

  2. **Text fallback** — adapters without rich UI render a numbered list.
     The user replies with a number ("2") or with free text; the gateway's
     ``_handle_message`` intercepts the reply and resolves directly.

AIS-793: several questions in one ask go out as ONE message with numbered
blocks (``format_questions_message``). The user answers them in one reply or
in several ("1b 2a", "2: free text", "3 later"); ``submit_questions_reply``
parses each reply and resolves the ask once every question has an answer.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Choice letters in the numbered-blocks message ("1b" = question 1, choice b).
_CHOICE_LETTERS = "abcd"
# Words that defer a question ("later"), besides the localized one.
_LATER_WORDS = frozenset({"later", "skip", "später", "spaeter", "überspringen"})


# =========================================================================
# Module-level state
# =========================================================================

@dataclass
class _ClarifyEntry:
    """One pending clarify request inside a gateway session."""
    clarify_id: str
    session_key: str
    question: str
    choices: Optional[List[str]]
    event: threading.Event = field(default_factory=threading.Event)
    response: Optional[str] = None
    awaiting_text: bool = False  # set when user picked "Other" or clarify is open-ended
    # AIS-793: several questions answered by text; index → {state, response}.
    questions: Optional[List[Dict[str, Any]]] = None
    answers: Dict[int, Dict[str, str]] = field(default_factory=dict)

    def signature(self) -> Dict[str, object]:
        return {
            "clarify_id": self.clarify_id,
            "session_key": self.session_key,
            "question": self.question,
            "choices": list(self.choices) if self.choices else None,
        }

    def open_numbers(self) -> List[int]:
        """1-based numbers of the questions still without an answer."""
        return [n + 1 for n in range(len(self.questions or [])) if n not in self.answers]


_lock = threading.RLock()
# clarify_id → _ClarifyEntry  (primary lookup for button callbacks)
_entries: Dict[str, _ClarifyEntry] = {}
# session_key → list[clarify_id]  (FIFO; for text-fallback intercept and session cleanup)
_session_index: Dict[str, List[str]] = {}


# =========================================================================
# Public API — agent-thread side
# =========================================================================

def register(
    clarify_id: str,
    session_key: str,
    question: str,
    choices: Optional[List[str]],
    questions: Optional[List[Dict[str, Any]]] = None,
) -> _ClarifyEntry:
    """Register a pending clarify request and return the entry.

    The caller (gateway clarify_callback) will then send the prompt to the
    user and block on ``wait_for_response(clarify_id, timeout)`` — or, with
    ``questions`` (AIS-793), on ``wait_for_answers``.
    """
    entry = _ClarifyEntry(
        clarify_id=clarify_id,
        session_key=session_key,
        question=question,
        choices=list(choices) if choices else None,
        # Open-ended (no choices) → next message IS the response, no buttons
        # needed; several questions are always answered by text.
        awaiting_text=not bool(choices) or bool(questions),
        questions=[dict(q) for q in questions] if questions else None,
    )
    with _lock:
        _entries[clarify_id] = entry
        _session_index.setdefault(session_key, []).append(clarify_id)
    return entry


def wait_for_response(clarify_id: str, timeout: float) -> Optional[str]:
    """Block on the entry's event until resolved or timeout fires.

    Polls in 1-second slices so the agent's inactivity heartbeat keeps
    firing — without this, ``Event.wait(timeout=600)`` blocks the thread
    for 10 minutes with zero activity touches and the gateway's inactivity
    watchdog kills the agent while the user is still typing.

    Returns the resolved response string, or ``None`` on timeout.
    """
    entry = _wait(clarify_id, timeout)
    return entry.response if entry is not None else None


def wait_for_answers(clarify_id: str, timeout: float) -> Optional[List[Dict[str, str]]]:
    """Block like ``wait_for_response`` for an ask with several questions.

    Returns one ``{state, response}`` per question in order; questions the
    user had not answered when the deadline passed (or the session ended)
    are ``timeout`` — answers given before stay. ``None`` for an unknown id.
    """
    entry = _wait(clarify_id, timeout)
    if entry is None:
        return None
    with _lock:
        given = dict(entry.answers)
    return [
        given.get(n) or {"state": "timeout", "response": ""}
        for n in range(len(entry.questions or []))
    ]


def _wait(clarify_id: str, timeout: float) -> Optional[_ClarifyEntry]:
    with _lock:
        entry = _entries.get(clarify_id)
    if entry is None:
        return None

    try:
        from tools.environments.base import touch_activity_if_due
    except Exception:  # pragma: no cover - optional
        touch_activity_if_due = None

    deadline = time.monotonic() + max(timeout, 0.0)
    activity_state = {"last_touch": time.monotonic(), "start": time.monotonic()}
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if entry.event.wait(timeout=min(1.0, remaining)):
            break
        if touch_activity_if_due is not None:
            touch_activity_if_due(activity_state, "waiting for user clarify response")

    with _lock:
        # Remove from indices regardless of resolution outcome.
        _entries.pop(clarify_id, None)
        ids = _session_index.get(entry.session_key)
        if ids and clarify_id in ids:
            ids.remove(clarify_id)
            if not ids:
                _session_index.pop(entry.session_key, None)

    return entry


# =========================================================================
# Public API — gateway / adapter side
# =========================================================================

def resolve_gateway_clarify(clarify_id: str, response: str) -> bool:
    """Unblock the agent thread waiting on ``clarify_id``.

    Returns True if an entry was found and resolved, False otherwise
    (already resolved, expired, or never existed).
    """
    with _lock:
        entry = _entries.get(clarify_id)
        if entry is None:
            return False
    entry.response = str(response) if response is not None else ""
    entry.event.set()
    return True


def submit_questions_reply(clarify_id: str, text: str) -> Optional[List[int]]:
    """Apply one text reply to an ask with several questions (AIS-793).

    Returns the 1-based numbers still open afterwards — ``[]`` when every
    question has an answer (the waiting agent thread is released) — or
    ``None`` when no such ask is pending.
    """
    with _lock:
        entry = _entries.get(clarify_id)
        if entry is None or not entry.questions:
            return None
        open_before = [n - 1 for n in entry.open_numbers()]
    found = parse_questions_reply(text, entry.questions, open_before)
    with _lock:
        entry.answers.update(found)
        still_open = entry.open_numbers()
    if not still_open:
        entry.response = "answered"
        entry.event.set()
    return still_open


def _later_words() -> frozenset:
    try:
        from agent.i18n import t

        return _LATER_WORDS | {t("clarify.later").strip().lower()}
    except Exception:
        return _LATER_WORDS


def _marker_re(later: frozenset) -> "re.Pattern[str]":
    """A question marker: "1b" / "1 b" (choice letter), "1 later", "1:" / "1)"
    after a separator, "1." / "1 -" only at a line start (so "am 3. November"
    inside an answer is no marker)."""
    later_alt = "|".join(re.escape(word) for word in sorted(later, key=len, reverse=True))
    return re.compile(
        r"(?:^|(?<=[\s,;]))([1-9])"
        r"(?:\s*(?=[a-d](?![^\W\d_]))|\s+(?=(?:" + later_alt + r")(?![^\W\d_]))|\s*[:)]\s*)"
        r"|^[ \t]*([1-9])[ \t]*[.\-–][ \t]*",
        re.IGNORECASE | re.MULTILINE,
    )


def _interpret(segment: str, item: Dict[str, Any], later: frozenset) -> Optional[Dict[str, str]]:
    """One answer segment as ``{state, response}``; ``None`` when empty."""
    text = segment.strip().strip(",;").strip()
    if not text:
        return None
    word = text.rstrip(".!").strip().lower()
    if word in later:
        return {"state": "skipped", "response": ""}
    choices = list(item.get("choices") or [])
    if choices:
        if len(word) == 1 and word in _CHOICE_LETTERS[: len(choices)]:
            return {"state": "answered", "response": choices[_CHOICE_LETTERS.index(word)]}
        if word.isdigit() and 1 <= int(word) <= len(choices):
            return {"state": "answered", "response": choices[int(word) - 1]}
        for choice in choices:
            if word == choice.strip().lower():
                return {"state": "answered", "response": choice}
    return {"state": "answered", "response": text}


def parse_questions_reply(
    text: str,
    questions: List[Dict[str, Any]],
    open_indexes: Optional[List[int]] = None,
) -> Dict[int, Dict[str, str]]:
    """Answers found in one reply, by 0-based question index.

    Understands "1b 2a", "1: free text", "2) later", a plain "later" (every
    open question), "b a" (letters for the open questions in order) and a
    plain answer when only one question is open. Unrecognised text yields
    ``{}`` so the caller can say what is still open.
    """
    raw = str(text or "").strip()
    if not raw or not questions:
        return {}
    later = _later_words()
    open_indexes = list(range(len(questions))) if open_indexes is None else list(open_indexes)

    matches = [
        (int(m.group(1) or m.group(2)) - 1, m.start(), m.end())
        for m in _marker_re(later).finditer(raw)
    ]
    matches = [(index, start, end) for index, start, end in matches if 0 <= index < len(questions)]
    found: Dict[int, Dict[str, str]] = {}
    if matches:
        for pos, (index, _start, end) in enumerate(matches):
            stop = matches[pos + 1][1] if pos + 1 < len(matches) else len(raw)
            answer = _interpret(raw[end:stop], questions[index], later)
            if answer is not None:
                found[index] = answer
        return found

    if raw.rstrip(".!").strip().lower() in later:
        return {index: {"state": "skipped", "response": ""} for index in open_indexes}
    tokens = [token for token in re.split(r"[\s,;]+", raw.lower()) if token]
    if len(open_indexes) > 1 and len(tokens) == len(open_indexes) and all(
        len(token) == 1 and token in _CHOICE_LETTERS for token in tokens
    ):
        for index, token in zip(open_indexes, tokens):
            answer = _interpret(token, questions[index], later)
            if answer is not None:
                found[index] = answer
        return found
    if len(open_indexes) == 1:
        answer = _interpret(raw, questions[open_indexes[0]], later)
        return {open_indexes[0]: answer} if answer is not None else {}
    return {}


def format_questions_message(questions: List[Dict[str, Any]]) -> str:
    """Several questions as one message with numbered blocks (AIS-793)."""
    from agent.i18n import t

    later = t("clarify.later")
    lines = [f"❓ {t('clarify.questions_intro', count=len(questions), later=later)}"]
    for number, item in enumerate(questions, start=1):
        lines.append("")
        topic = str(item.get("topic") or "").strip()
        lines.append(f"{number}. {topic + ' — ' if topic else ''}{item.get('question') or ''}")
        for letter, choice in zip(_CHOICE_LETTERS, item.get("choices") or []):
            lines.append(f"   {letter}) {choice}")
    return "\n".join(lines)


def format_still_open(numbers: List[int], questions: List[Dict[str, Any]]) -> str:
    """The reply to a partial answer: which questions are still open."""
    from agent.i18n import t

    first = numbers[0] if numbers else 1
    has_choices = bool(questions[first - 1].get("choices")) if 0 < first <= len(questions) else False
    example = f"{first}a" if has_choices else f"{first}: …"
    return t(
        "clarify.still_open",
        numbers=", ".join(str(n) for n in numbers),
        example=example,
        later=t("clarify.later"),
    )


def get_pending_for_session(session_key: str) -> Optional[_ClarifyEntry]:
    """Return the OLDEST pending clarify entry for a session, or None.

    Used by the text-fallback intercept in ``_handle_message`` — when a
    clarify is awaiting a free-form text response, the next user message
    in that session is captured as the answer.
    """
    with _lock:
        ids = _session_index.get(session_key) or []
        for cid in ids:
            entry = _entries.get(cid)
            if entry is None:
                continue
            if entry.awaiting_text:
                return entry
        return None


def mark_awaiting_text(clarify_id: str) -> bool:
    """Flip an entry into text-capture mode (user picked the 'Other' button).

    Returns True if the entry exists and was flipped, False otherwise.
    """
    with _lock:
        entry = _entries.get(clarify_id)
        if entry is None:
            return False
        entry.awaiting_text = True
        return True


def has_pending(session_key: str) -> bool:
    """Return True when this session has at least one pending clarify entry."""
    with _lock:
        ids = _session_index.get(session_key) or []
        return any(_entries.get(cid) is not None for cid in ids)


def clear_session(session_key: str) -> int:
    """Resolve and drop every pending clarify for a session.

    Used by session-boundary cleanup (e.g. ``/new``, gateway shutdown,
    cached-agent eviction) so blocked agent threads don't hang past the
    end of their session.  Returns the number of entries cancelled.
    """
    with _lock:
        ids = list(_session_index.pop(session_key, []) or [])
        entries = [_entries.pop(cid, None) for cid in ids]
    cancelled = 0
    for entry in entries:
        if entry is None:
            continue
        # Empty string sentinel — agent code can distinguish from a real
        # response by inspecting the wait_for_response return value
        # alongside its own timeout deadline.  Most callers just treat any
        # falsy result as "user did not respond".
        entry.response = ""
        entry.event.set()
        cancelled += 1
    return cancelled


# =========================================================================
# Config
# =========================================================================

DEFAULT_CLARIFY_TIMEOUT = 600


def get_clarify_timeout(config: "dict | None" = None) -> int:
    """Resolve the clarify response timeout (seconds) from config.

    Single source of truth for every surface (gateway, TUI/desktop, CLI):

    1. ``clarify.timeout`` — the user-facing key (documented in
       cli-config.yaml.example),
    2. ``agent.clarify_timeout`` — the shipped default section
       (hermes_cli/config.py),
    3. 600 — ten minutes: long enough for the user to think about a
       multi-choice onboarding question, short enough that an abandoned prompt
       eventually unblocks the agent thread.

    AIS-333 / SUP-20260914-092956: the desktop and CLI used to fall back to
    120 s while the gateway used 600 s, so an onboarding question silently
    expired while the user was still thinking.
    """
    try:
        cfg = config
        if cfg is None:
            from hermes_cli.config import load_config
            cfg = load_config() or {}
        clarify_cfg = cfg.get("clarify") if isinstance(cfg, dict) else None
        if isinstance(clarify_cfg, dict) and clarify_cfg.get("timeout") not in (None, ""):
            value = int(float(clarify_cfg.get("timeout")))
            if value > 0:
                return value
        agent_cfg = cfg.get("agent") if isinstance(cfg, dict) else None
        if isinstance(agent_cfg, dict) and agent_cfg.get("clarify_timeout") not in (None, ""):
            value = int(float(agent_cfg.get("clarify_timeout")))
            if value > 0:
                return value
        return DEFAULT_CLARIFY_TIMEOUT
    except Exception:
        return DEFAULT_CLARIFY_TIMEOUT


# =========================================================================
# Per-session notify hook (gateway → adapter bridge)
# =========================================================================
# Mirrors tools.approval's _gateway_notify_cbs: the gateway registers a
# per-session callback that sends the clarify prompt to the user.  The
# callback bridges sync→async (runs on the agent thread; schedules the
# adapter ``send_clarify`` call on the event loop).

_notify_cbs: Dict[str, Callable[[_ClarifyEntry], None]] = {}


def register_notify(session_key: str, cb: Callable[[_ClarifyEntry], None]) -> None:
    """Register a per-session notify callback used by ``clarify_callback``."""
    with _lock:
        _notify_cbs[session_key] = cb


def unregister_notify(session_key: str) -> None:
    """Drop the per-session notify callback and cancel any pending clarify entries."""
    with _lock:
        _notify_cbs.pop(session_key, None)
    # Cancel any pending entries so blocked threads unwind when the run
    # ends (interrupt, completion, gateway shutdown).
    clear_session(session_key)


def get_notify(session_key: str) -> Optional[Callable[[_ClarifyEntry], None]]:
    with _lock:
        return _notify_cbs.get(session_key)
