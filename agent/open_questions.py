"""Workspace `_open-questions.md` persistence helpers."""

from __future__ import annotations

import re
import threading
from datetime import timezone
from pathlib import Path
from typing import Optional, Tuple

from agent.runtime_cwd import resolve_agent_cwd
from hermes_time import now as _hermes_now

_OPEN_QUESTIONS_HEADER = """---
type: open-questions
updated: ""
---

# Open questions

> Things the agent needs clarified. Captured here instead of lost in chat. The agent
> surfaces these in the morning brief.

<!-- - [ ] question (raised YYYY-MM-DD, re: what) -->
"""

_open_questions_file_lock = threading.Lock()
_dedupe_lock = threading.Lock()
_seen_dedupe_keys: set[str] = set()


def _to_compact_line(text: str, *, limit: int) -> str:
    line = re.sub(r"\s+", " ", str(text or "").strip()).replace("|", "/")
    return line[:limit]


def append_open_question_entry(
    *,
    context: str,
    needed: str,
    source: str = "",
    turn_id: str = "",
    dedupe_key: str = "",
) -> Optional[Path]:
    """Append one open-question entry to workspace `_open-questions.md`.

    Returns the file path when a line was appended, or ``None`` when skipped.
    """
    context_line = _to_compact_line(context, limit=220)
    needed_line = _to_compact_line(needed, limit=220)
    if not context_line or not needed_line:
        return None

    key = str(dedupe_key or "").strip()
    if key:
        with _dedupe_lock:
            if key in _seen_dedupe_keys:
                return None
            _seen_dedupe_keys.add(key)
            if len(_seen_dedupe_keys) > 2048:
                _seen_dedupe_keys.clear()

    root = resolve_agent_cwd().expanduser().resolve()
    questions_path = root / "_open-questions.md"

    timestamp = (
        _hermes_now()
        .astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    parts = [
        f"ts={timestamp}",
        f"context={context_line}",
        f"needed={needed_line}",
    ]
    src = _to_compact_line(source, limit=120)
    if src:
        parts.append(f"source={src}")
    tid = _to_compact_line(turn_id, limit=80)
    if tid:
        parts.append(f"turn={tid}")
    entry = "- " + " | ".join(parts) + "\n"

    with _open_questions_file_lock:
        if not questions_path.exists():
            questions_path.write_text(_OPEN_QUESTIONS_HEADER, encoding="utf-8")
        with questions_path.open("a", encoding="utf-8") as fh:
            fh.write(entry)
    return questions_path


def derive_blocking_open_question_from_review_text(text: str) -> Optional[Tuple[str, str]]:
    """Extract a `(context, needed)` pair from review output when blocked."""
    cleaned: list[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("```"):
            continue
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"^[-*]\s+", "", line)
        line = re.sub(r"^\d+\.\s+", "", line)
        line = _to_compact_line(line, limit=280)
        if line:
            cleaned.append(line)

    if not cleaned:
        return None

    context = cleaned[0]
    patterns = (
        "open question:",
        "needs input",
        "need input",
        "needs clarification",
        "need clarification",
        "awaiting input",
        "cannot proceed",
        "can't proceed",
        "blocked",
        "requires decision",
    )
    for line in cleaned:
        lowered = line.lower()
        if any(token in lowered for token in patterns):
            if lowered.startswith("open question:"):
                needed = line.split(":", 1)[1].strip() or line
            else:
                needed = line
            return context, needed

    return None


def derive_open_question_from_clarify_result(question: str, result_payload: dict) -> Optional[Tuple[str, str, str]]:
    """Return `(context, needed, reason_code)` when clarify stayed unresolved."""
    q = _to_compact_line(question, limit=220)
    if not q:
        return None
    payload = result_payload if isinstance(result_payload, dict) else {}

    error_text = str(payload.get("error") or "").strip()
    if error_text:
        return (
            f"Clarify required for: {q}",
            error_text,
            "clarify_error",
        )

    response = str(payload.get("user_response") or "").strip()
    lowered = response.lower()
    if not response:
        return (
            f"Clarify required for: {q}",
            "No user input was captured for the clarify question.",
            "clarify_empty_response",
        )
    if lowered.startswith("[clarify prompt could not be delivered]"):
        return (
            f"Clarify required for: {q}",
            "Clarify prompt delivery failed; user decision is still needed.",
            "clarify_prompt_delivery_failed",
        )
    if lowered.startswith("[user did not respond within"):
        return (
            f"Clarify required for: {q}",
            f"User did not answer in time ({response}).",
            "clarify_timeout",
        )

    return None
