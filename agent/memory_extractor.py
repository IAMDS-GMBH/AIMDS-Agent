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
import uuid
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
DEFAULT_AUTO_SAVE_CONFIDENCE = 0.78
DEFAULT_ASK_CONFIDENCE = 0.45

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
            mirror_mcp_memory_save_to_local,
            tool_result_indicates_success,
            upsert_structured_mirror_record,
            build_structured_mirror_record,
        )
        from agent.open_questions import append_open_question_entry
        from agent.prompt_builder import _resolve_memory_save_tool_name
        import run_agent as _ra

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

        high_conf_written = 0
        low_conf_queued = 0
        skipped_low_conf = 0
        confidences: List[float] = []
        save_threshold, ask_threshold = _resolve_capture_thresholds(agent)
        valid_tools = set(getattr(agent, "valid_tool_names", []) or [])
        memory_save_tool = _resolve_memory_save_tool_name(valid_tools)

        for fact in facts:
            # Override type to "profile" for user-scope facts (scope drives type in our schema)
            if fact["scope"] == "user" and fact["type"] not in {"profile"}:
                fact["type"] = "profile"
            conf = fact.get("confidence")
            if isinstance(conf, (int, float)):
                confidences.append(float(conf))
            score = float(conf) if isinstance(conf, (int, float)) else 0.6
            score = max(0.0, min(1.0, score))

            if score < ask_threshold:
                skipped_low_conf += 1
                continue

            if score < save_threshold:
                context = f"Memory capture confirmation needed: {fact.get('title', 'durable topic')}"
                needed = (
                    f"Confirm whether this should be saved as durable memory: "
                    f"{str(fact.get('content') or '').strip()[:200]}"
                )
                dedupe_key = "|".join(
                    [
                        "memory-extractor",
                        str(fact.get("scope") or "project"),
                        str(fact.get("type") or "notes"),
                        str(fact.get("title") or "").strip().lower()[:80],
                    ]
                )
                append_open_question_entry(
                    context=context,
                    needed=needed,
                    source="memory-extractor",
                    turn_id=turn_id,
                    dedupe_key=dedupe_key,
                )
                low_conf_queued += 1
                continue

            hints = {"extraction_confidence": conf} if isinstance(conf, (int, float)) else {}
            mcp_saved = False
            if memory_save_tool:
                tool_args = {
                    "title": fact["title"],
                    "content": fact["content"],
                    "type": fact["type"],
                    "tags": fact.get("tags") or [],
                    "hints": hints,
                }
                call_id = f"memory-extractor-{uuid.uuid4().hex[:12]}"
                try:
                    mcp_result = _ra.handle_function_call(
                        memory_save_tool,
                        tool_args,
                        str(effective_task_id or ""),
                        tool_call_id=call_id,
                        session_id=session_id,
                        turn_id=turn_id,
                        api_request_id="",
                        enabled_tools=list(valid_tools) if valid_tools else None,
                        skip_pre_tool_call_hook=True,
                        skip_tool_request_middleware=True,
                        enabled_toolsets=getattr(agent, "enabled_toolsets", None),
                        disabled_toolsets=getattr(agent, "disabled_toolsets", None),
                        tool_request_middleware_trace=[],
                    )
                    if tool_result_indicates_success(mcp_result):
                        mcp_saved = True
                        mirror_mcp_memory_save_to_local(
                            agent,
                            memory_save_tool,
                            tool_args,
                            mcp_result,
                            effective_task_id=str(effective_task_id or ""),
                            tool_call_id=call_id,
                        )
                except Exception:
                    mcp_saved = False

            if not mcp_saved:
                # No MCP save tool (or it failed): the facade writes to the
                # Obsidian vault so the fact is not lost in the local mirror only.
                try:
                    from agent.memory_facade import MODE_NONE, MemoryFacade

                    _facade = MemoryFacade.for_agent(agent)
                    if _facade.mode != MODE_NONE:
                        mcp_saved = _facade.save(
                            title=fact["title"], content=fact["content"], type=fact["type"],
                            tags=list(fact.get("tags") or []), scope=str(fact.get("scope") or "project"),
                        ).ok
                except Exception:
                    pass

            # Local structured mirror fallback/secondary persistence.
            record = build_structured_mirror_record(
                tool_args={**fact, "hints": hints},
                write_meta={"write_origin": "llm_extraction", "scope": fact["scope"]},
                tool_name="llm_extractor",
                effective_task_id=str(effective_task_id or ""),
            )
            if record:
                upsert_structured_mirror_record(record)
                high_conf_written += 1
            elif mcp_saved:
                high_conf_written += 1

        if high_conf_written or low_conf_queued:
            avg_conf = (sum(confidences) / len(confidences)) if confidences else None
            _append_extraction_audit_event({
                **base_event,
                "status": "save",
                "reason_code": "save_facts_written",
                "saved_count": int(high_conf_written),
                "queued_for_confirmation_count": int(low_conf_queued),
                "skipped_low_confidence_count": int(skipped_low_conf),
                "save_threshold": float(save_threshold),
                "ask_threshold": float(ask_threshold),
                "confidence": avg_conf,
                "latency_ms": int((time.time() - started) * 1000),
            })
            logger.debug(
                "memory_extractor: saved=%d queued=%d skipped=%d",
                high_conf_written,
                low_conf_queued,
                skipped_low_conf,
            )
        else:
            _append_extraction_audit_event({
                **base_event,
                "status": "skip",
                "reason_code": "skip_no_records_built",
                "save_threshold": float(save_threshold),
                "ask_threshold": float(ask_threshold),
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


def _resolve_capture_thresholds(agent: Any) -> tuple[float, float]:
    """Read memory extraction thresholds from config with safe defaults.

    - auto-save when confidence >= save_threshold
    - ask/queue when ask_threshold <= confidence < save_threshold
    - skip below ask_threshold
    """
    save_threshold = DEFAULT_AUTO_SAVE_CONFIDENCE
    ask_threshold = DEFAULT_ASK_CONFIDENCE
    try:
        cfg = getattr(agent, "_agent_cfg", {}) if hasattr(agent, "_agent_cfg") else {}
        mem_cfg = cfg.get("memory", {}) if isinstance(cfg, dict) else {}
        managed_cfg = mem_cfg.get("managed_memory", {}) if isinstance(mem_cfg, dict) else {}
        raw_save = managed_cfg.get("auto_save_min_confidence")
        raw_ask = managed_cfg.get("ask_min_confidence")
        if raw_save is not None:
            save_threshold = float(raw_save)
        if raw_ask is not None:
            ask_threshold = float(raw_ask)
    except Exception:
        save_threshold = DEFAULT_AUTO_SAVE_CONFIDENCE
        ask_threshold = DEFAULT_ASK_CONFIDENCE

    save_threshold = max(0.0, min(1.0, float(save_threshold)))
    ask_threshold = max(0.0, min(1.0, float(ask_threshold)))
    if ask_threshold > save_threshold:
        ask_threshold = save_threshold
    return save_threshold, ask_threshold


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
