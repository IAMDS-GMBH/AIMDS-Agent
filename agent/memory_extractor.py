"""LLM-assisted memory extraction — runs in a background daemon thread.

After each turn (when ``capture_mode=auto``), a single structured chat
completion extracts durable facts from the exchange and upserts them into
the structured JSONL mirror store.  This is a higher-quality alternative
to the regex-heuristic auto-capture path: the model understands context,
can infer scope (user preference vs project note), and won't fire on
every phrase that pattern-matches a keyword.

Design invariants
-----------------
* **Non-blocking** — always runs in a daemon thread; never delays the response.
* **No full agent loop** — single synchronous ``chat.completions.create``
  call using the parent's live OpenAI client credentials.
* **Pre-filtered** — only fires when the exchange contains preference-like
  language (detected by the existing heuristic) OR the combined user+assistant
  text exceeds ``MIN_CHARS_FOR_EXTRACTION`` characters.
* **Idempotent** — slug-based upsert means re-running on the same content is
  safe (updates in-place rather than duplicating).
* **Isolated** — exceptions are swallowed; this path must never affect the
  main turn or break the app.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import contextlib
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Only attempt extraction when the combined exchange text is at least this long.
# Short exchanges rarely contain durable facts worth saving.
MIN_CHARS_FOR_EXTRACTION = 120

# Cap extracted facts per turn to avoid noisy saves on verbose responses.
MAX_FACTS_PER_TURN = 5

# User-origin durable fact hints (short-turn fast path).
# These patterns are intentionally conservative to avoid noisy extraction.
_USER_FACT_HINT_RE = re.compile(
    r"\b("
    r"i\s+(?:am|i'm|prefer|like|usually|typically|work|use|need|want|always|never)\b|"
    r"my\s+(?:role|title|team|workflow|stack|project)\b|"
    r"for\s+code\s+reviews\b"
    r")",
    re.I,
)

_EXTRACTION_SYSTEM_PROMPT = """\
You are a memory extraction assistant. Your sole job is to identify durable facts \
from a conversation exchange that are worth saving to persistent memory.

Return ONLY a JSON array (no prose, no markdown fences).
Each element must be an object with these fields:
  "title"   : short label (≤60 chars)
  "content" : the fact in one clear sentence
  "type"    : one of: profile | notes | project | reference | rule
  "scope"   : one of: user | project
  "tags"    : array of 1-3 lowercase keyword strings

Rules:
- "user" scope = personal preferences, habits, communication style, language.
- "project" scope = project-specific facts, tools, decisions, context.
- Only include facts that would meaningfully help a future assistant response.
- If nothing is worth saving, return an empty array: []
- Maximum """ + str(MAX_FACTS_PER_TURN) + """ facts.
- Do NOT include transient state (current task steps, what you just did).\
"""

_EXTRACTION_USER_TEMPLATE = """\
Extract durable facts from this conversation exchange.

USER:
{user_message}

ASSISTANT:
{assistant_message}
"""


def _build_extraction_messages(
    user_message: str,
    assistant_message: str,
) -> List[Dict[str, Any]]:
    user_content = _EXTRACTION_USER_TEMPLATE.format(
        user_message=str(user_message or "").strip()[:2000],
        assistant_message=str(assistant_message or "").strip()[:3000],
    )
    return [
        {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _parse_extraction_response(text: str) -> List[Dict[str, Any]]:
    """Parse JSON array from LLM response, returning empty list on any failure."""
    text = str(text or "").strip()
    if not text:
        return []
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(
            line for line in lines
            if not line.strip().startswith("```")
        ).strip()
    try:
        parsed = json.loads(text)
    except Exception:
        # Try to find a JSON array in the text
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
            except Exception:
                return []
        else:
            return []

    if not isinstance(parsed, list):
        return []

    valid: List[Dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        content = str(item.get("content") or "").strip()
        if not title or not content:
            continue
        mem_type = str(item.get("type") or "notes").strip().lower()
        if mem_type not in {"profile", "notes", "project", "reference", "rule"}:
            mem_type = "notes"
        scope = str(item.get("scope") or "project").strip().lower()
        if scope not in {"user", "project"}:
            scope = "project"
        tags = item.get("tags")
        if not isinstance(tags, list):
            tags = []
        tags = [str(t).strip().lower() for t in tags if str(t).strip()][:3]
        valid.append({
            "title": title,
            "content": content,
            "type": mem_type,
            "scope": scope,
            "tags": tags,
        })
    return valid[:MAX_FACTS_PER_TURN]


def _run_extraction(
    agent: Any,
    user_message: str,
    assistant_message: str,
    effective_task_id: str,
) -> None:
    """Worker: make a single LLM call and upsert extracted facts. Runs in daemon thread."""
    try:
        from agent.memory_dual_write import (
            upsert_structured_mirror_record,
            build_structured_mirror_record,
        )

        messages = _build_extraction_messages(user_message, assistant_message)

        # Use the parent agent's live OpenAI client + model
        client = getattr(agent, "client", None)
        model = getattr(agent, "model", None) or ""

        if client is None or not model:
            logger.debug("memory_extractor: no client/model on agent, skipping")
            return

        with open(os.devnull, "w", encoding="utf-8") as _devnull, \
             contextlib.redirect_stdout(_devnull), \
             contextlib.redirect_stderr(_devnull):
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=600,
                temperature=0.0,
            )

        content = ""
        if response and hasattr(response, "choices") and response.choices:
            msg = response.choices[0].message
            content = str(getattr(msg, "content", "") or "").strip()

        facts = _parse_extraction_response(content)
        if not facts:
            logger.debug("memory_extractor: no facts extracted")
            return

        written = 0
        for fact in facts:
            # Override type to "profile" for user-scope facts (scope drives type in our schema)
            if fact["scope"] == "user" and fact["type"] not in {"profile"}:
                fact["type"] = "profile"
            record = build_structured_mirror_record(
                tool_args=fact,
                write_meta={"write_origin": "llm_extraction", "scope": fact["scope"]},
                tool_name="llm_extractor",
                effective_task_id=str(effective_task_id or ""),
            )
            if record:
                upsert_structured_mirror_record(record)
                written += 1

        if written:
            logger.debug("memory_extractor: saved %d fact(s)", written)

    except Exception as exc:
        logger.debug("memory_extractor: extraction failed: %s", exc)


def should_attempt_extraction(
    user_message: str,
    assistant_message: str,
) -> bool:
    """Pre-filter: decide whether to attempt LLM extraction for this exchange.

    Returns True when the exchange is likely to contain durable facts.
    Avoids spending an LLM call on every short/tool-only turn.
    """
    combined = (str(user_message or "") + " " + str(assistant_message or "")).strip()
    user_text = str(user_message or "").strip()
    if len(combined) < MIN_CHARS_FOR_EXTRACTION:
        # Short-turn fast path: allow extraction when the user message itself
        # strongly looks like a durable preference/profile/project fact.
        if len(user_text) >= 25 and _USER_FACT_HINT_RE.search(user_text):
            return True
        return False

    # Always attempt when the combined text is substantial
    if len(combined) > 800:
        return True

    # For shorter exchanges, pre-filter with the heuristic
    from agent.memory_dual_write import detect_preference_candidates
    return bool(detect_preference_candidates(combined))


def spawn_memory_extraction_thread(
    agent: Any,
    user_message: str,
    assistant_message: str,
    effective_task_id: str = "",
) -> None:
    """Launch background memory extraction if conditions are met.

    Safe to call unconditionally — checks capture_mode and pre-filter internally.
    Exceptions are swallowed; this must never affect the main turn.
    """
    try:
        if not should_attempt_extraction(user_message, assistant_message):
            return

        t = threading.Thread(
            target=_run_extraction,
            args=(agent, user_message, assistant_message, effective_task_id),
            daemon=True,
            name="memory-extractor",
        )
        t.start()
    except Exception as exc:
        logger.debug("memory_extractor: failed to spawn thread: %s", exc)
