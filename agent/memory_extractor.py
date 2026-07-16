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
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

# Only attempt extraction when the combined exchange text is at least this long.
# Short exchanges rarely contain durable facts worth saving.
MIN_CHARS_FOR_EXTRACTION = 120

# Cap extracted facts per turn to avoid noisy saves on verbose responses.
MAX_FACTS_PER_TURN = 5
EXTRACTION_AUDIT_FILENAME = "MCP_MIRROR_AUDIT.jsonl"
EXTRACTION_AUDIT_VERSION = 1

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
  "confidence": optional float in range [0.0, 1.0]

Rules:
- "user" scope = personal preferences, habits, communication style, language.
- "project" scope = project-specific facts, tools, decisions, context.
- Only include facts that would meaningfully help a future assistant response.
- Be language-agnostic: extract from ANY language and mixed-language messages.
- Do not require the assistant to restate a fact; user-origin facts are valid.
- Prefer durable items such as role/title, recurring workflow, technical focus,
  review priorities, stable constraints, or long-lived project context.
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
        confidence = item.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except Exception:
            confidence = None
        if confidence is not None:
            confidence = max(0.0, min(1.0, confidence))
        valid.append({
            "title": title,
            "content": content,
            "type": mem_type,
            "scope": scope,
            "tags": tags,
            "confidence": confidence,
        })
    return valid[:MAX_FACTS_PER_TURN]


def _looks_natural_language(text: str) -> bool:
    """Language-neutral heuristic for deciding if text is semantic prose."""
    t = str(text or "").strip()
    if len(t) < 25:
        return False
    token_count = len(re.findall(r"\w+", t, flags=re.UNICODE))
    if token_count < 5:
        return False
    alnum_ratio = sum(1 for ch in t if ch.isalnum()) / max(1, len(t))
    return alnum_ratio >= 0.40


def _extraction_audit_path() -> Path:
    mem_dir = get_hermes_home() / "memories"
    mem_dir.mkdir(parents=True, exist_ok=True)
    return mem_dir / EXTRACTION_AUDIT_FILENAME


def _append_extraction_audit_event(event: Dict[str, Any]) -> None:
    """Append one extraction audit event to local JSONL (best-effort)."""
    try:
        row = dict(event or {})
        row.setdefault("version", EXTRACTION_AUDIT_VERSION)
        row.setdefault("ts", int(time.time()))
        path = _extraction_audit_path()
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def read_extraction_audit_events(
    *,
    limit: int = 40,
    status: Optional[str] = None,
    reason: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Read extraction audit events from local JSONL with optional filters."""
    path = _extraction_audit_path()
    if not path.exists():
        return []

    status_f = str(status or "").strip().lower()
    reason_f = str(reason or "").strip().lower()
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        row_status = str(row.get("status") or "").strip().lower()
        row_reason = str(row.get("reason_code") or "").strip().lower()
        if status_f and row_status != status_f:
            continue
        if reason_f and row_reason != reason_f:
            continue
        rows.append(row)

    rows.sort(key=lambda r: int(r.get("ts") or 0), reverse=True)
    return rows[: max(1, int(limit or 40))]


def _run_extraction(
    agent: Any,
    user_message: str,
    assistant_message: str,
    effective_task_id: str,
) -> None:
    """Worker: make a single LLM call and upsert extracted facts. Runs in daemon thread."""
    started = time.time()
    user_len = len(str(user_message or ""))
    assistant_len = len(str(assistant_message or ""))
    session_id = str(getattr(agent, "session_id", "") or "")
    turn_id = str(getattr(agent, "_current_turn_id", "") or "")
    model = str(getattr(agent, "model", "") or "")
    base_event = {
        "session_id": session_id,
        "task_id": str(effective_task_id or ""),
        "turn_id": turn_id,
        "model": model,
        "raw_len_user": user_len,
        "raw_len_assistant": assistant_len,
    }
    try:
        from agent.memory_dual_write import (
            upsert_structured_mirror_record,
            build_structured_mirror_record,
        )

        messages = _build_extraction_messages(user_message, assistant_message)

        # Use the parent agent's live OpenAI client + model
        client = getattr(agent, "client", None)
        if client is None or not model:
            _append_extraction_audit_event({
                **base_event,
                "status": "skip",
                "reason_code": "skip_missing_client_or_model",
            })
            logger.debug("memory_extractor: no client/model on agent, skipping")
            return

        _append_extraction_audit_event({
            **base_event,
            "status": "trigger",
            "reason_code": "trigger_prefilter_passed",
        })

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
            _append_extraction_audit_event({
                **base_event,
                "status": "skip",
                "reason_code": "skip_no_facts_extracted",
                "latency_ms": int((time.time() - started) * 1000),
            })
            logger.debug("memory_extractor: no facts extracted")
            return

        written = 0
        confidences: List[float] = []
        for fact in facts:
            # Override type to "profile" for user-scope facts (scope drives type in our schema)
            if fact["scope"] == "user" and fact["type"] not in {"profile"}:
                fact["type"] = "profile"
            conf = fact.get("confidence")
            if isinstance(conf, (int, float)):
                confidences.append(float(conf))
            hints = {"extraction_confidence": conf} if isinstance(conf, (int, float)) else {}
            record = build_structured_mirror_record(
                tool_args={**fact, "hints": hints},
                write_meta={"write_origin": "llm_extraction", "scope": fact["scope"]},
                tool_name="llm_extractor",
                effective_task_id=str(effective_task_id or ""),
            )
            if record:
                upsert_structured_mirror_record(record)
                written += 1

        if written:
            avg_conf = (sum(confidences) / len(confidences)) if confidences else None
            _append_extraction_audit_event({
                **base_event,
                "status": "save",
                "reason_code": "save_facts_written",
                "saved_count": int(written),
                "confidence": avg_conf,
                "latency_ms": int((time.time() - started) * 1000),
            })
            logger.debug("memory_extractor: saved %d fact(s)", written)
        else:
            _append_extraction_audit_event({
                **base_event,
                "status": "skip",
                "reason_code": "skip_no_records_built",
                "latency_ms": int((time.time() - started) * 1000),
            })

    except Exception as exc:
        _append_extraction_audit_event({
            **base_event,
            "status": "error",
            "reason_code": "error_extraction_exception",
            "error": str(exc)[:300],
            "latency_ms": int((time.time() - started) * 1000),
        })
        logger.debug("memory_extractor: extraction failed: %s", exc)


def should_attempt_extraction(
    user_message: str,
    assistant_message: str,
) -> bool:
    """Pre-filter: decide whether to attempt LLM extraction for this exchange.

    Returns True when the exchange is likely to contain durable facts.
    Avoids spending an LLM call on every short/tool-only turn.
    """
    user_text = str(user_message or "").strip()
    assistant_text = str(assistant_message or "").strip()
    combined = (user_text + " " + assistant_text).strip()
    if len(combined) < MIN_CHARS_FOR_EXTRACTION:
        # Short-turn fast path: language-agnostic semantic text from user.
        return _looks_natural_language(user_text)

    # Always attempt when the combined text is substantial
    if len(combined) > 800:
        return True

    # Mid-size exchanges: run when either side looks like semantic prose.
    return _looks_natural_language(user_text) or _looks_natural_language(assistant_text)


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
            _append_extraction_audit_event({
                "status": "skip",
                "reason_code": "skip_prefilter",
                "session_id": str(getattr(agent, "session_id", "") or ""),
                "task_id": str(effective_task_id or ""),
                "turn_id": str(getattr(agent, "_current_turn_id", "") or ""),
                "model": str(getattr(agent, "model", "") or ""),
                "raw_len_user": len(str(user_message or "")),
                "raw_len_assistant": len(str(assistant_message or "")),
            })
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
