"""Shared durable-topic capture service for cross-surface persistence.

This module provides one entrypoint for persisting durable topics so normal
chat, inbox dictation, and cron workflows can apply the same behavior:
1) confidence-gated save/queue/skip decision,
2) local structured memory write,
3) best-effort MCP ``memory_save`` write.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from agent.memory_dual_write import (
    build_structured_mirror_record,
    tool_result_indicates_success,
    upsert_structured_mirror_record,
)
from agent.open_questions import append_open_question_entry
from hermes_cli.config import load_config

logger = logging.getLogger(__name__)

DEFAULT_AUTO_SAVE_CONFIDENCE = 0.78
DEFAULT_ASK_CONFIDENCE = 0.45


@dataclass
class TopicCaptureResult:
    decision: str  # saved | queued_for_confirmation | skipped
    local_saved: bool
    mcp_saved: bool
    reason: str


def _resolve_thresholds() -> tuple[float, float]:
    save_threshold = DEFAULT_AUTO_SAVE_CONFIDENCE
    ask_threshold = DEFAULT_ASK_CONFIDENCE
    try:
        cfg = load_config(silent=True) or {}
        mem_cfg = cfg.get("memory", {}) if isinstance(cfg, dict) else {}
        managed_cfg = mem_cfg.get("managed_memory", {}) if isinstance(mem_cfg, dict) else {}
        raw_save = managed_cfg.get("auto_save_min_confidence")
        raw_ask = managed_cfg.get("ask_min_confidence")
        if raw_save is not None:
            save_threshold = float(raw_save)
        if raw_ask is not None:
            ask_threshold = float(raw_ask)
    except Exception as exc:
        logger.debug("topic_capture: failed reading thresholds from config: %s", exc)

    save_threshold = max(0.0, min(1.0, float(save_threshold)))
    ask_threshold = max(0.0, min(1.0, float(ask_threshold)))
    if ask_threshold > save_threshold:
        ask_threshold = save_threshold
    return save_threshold, ask_threshold


def _candidate_memory_save_tools() -> List[str]:
    candidates: List[str] = []
    try:
        cfg = load_config(silent=True) or {}
        include_tools = (
            (cfg.get("agent") or {}).get("include_tools") if isinstance(cfg, dict) else None
        ) or []
        for raw in include_tools:
            name = str(raw or "").strip()
            if not name:
                continue
            normalized = name.replace("-", "_")
            if normalized.endswith("_memory_upsert"):
                name = (
                    name.replace("_memory_upsert", "_memory_save")
                    .replace("-memory_upsert", "-memory_save")
                )
                normalized = name.replace("-", "_")
            if normalized.endswith("_memory_save") or normalized == "memory_save":
                candidates.append(name)
    except Exception as exc:
        logger.debug("topic_capture: include_tools discovery failed: %s", exc)

    for fallback in (
        "memory_save",
        "mcp_memory_memory_save",
        "mcp_IAMDS_mcp_memory_memory_save",
        "mcp_IAMDS_mcp_memory-memory_save",
    ):
        if fallback not in candidates:
            candidates.append(fallback)
    return candidates


def _try_mcp_memory_save(
    *,
    tool_args: Dict[str, Any],
    effective_task_id: str,
    session_id: str,
    turn_id: str,
) -> tuple[bool, str]:
    try:
        import run_agent as _ra
    except Exception as exc:
        return False, f"run_agent import unavailable: {exc}"

    for tool_name in _candidate_memory_save_tools():
        try:
            result = _ra.handle_function_call(
                tool_name,
                dict(tool_args),
                str(effective_task_id or ""),
                tool_call_id="topic-capture",
                session_id=str(session_id or ""),
                turn_id=str(turn_id or ""),
                api_request_id="",
                enabled_tools=[tool_name],
                skip_pre_tool_call_hook=True,
                skip_tool_request_middleware=True,
                enabled_toolsets=None,
                disabled_toolsets=None,
                tool_request_middleware_trace=[],
            )
            if tool_result_indicates_success(result):
                return True, f"saved_via_{tool_name}"
        except Exception as exc:
            logger.debug("topic_capture: MCP save tool %s failed: %s", tool_name, exc)
            continue

    return False, "no_memory_save_tool_succeeded"


def capture_durable_topic(
    *,
    source: str,
    title: str,
    content: str,
    confidence: float,
    memory_type: str = "notes",
    scope: str = "project",
    tags: Optional[List[str]] = None,
    effective_task_id: str = "",
    session_id: str = "",
    turn_id: str = "",
    ask_on_ambiguous: bool = True,
) -> TopicCaptureResult:
    """Persist one durable topic with confidence-gated behavior."""
    clean_title = str(title or "").strip()
    clean_content = str(content or "").strip()
    if not clean_title or not clean_content:
        return TopicCaptureResult(
            decision="skipped",
            local_saved=False,
            mcp_saved=False,
            reason="missing_title_or_content",
        )

    score = max(0.0, min(1.0, float(confidence)))
    save_threshold, ask_threshold = _resolve_thresholds()
    if score < ask_threshold:
        return TopicCaptureResult(
            decision="skipped",
            local_saved=False,
            mcp_saved=False,
            reason="below_ask_threshold",
        )

    if ask_on_ambiguous and score < save_threshold:
        needed = f"Confirm durable memory save: {clean_content[:220]}"
        dedupe_key = "|".join(
            [
                "topic-capture",
                str(source or "").lower(),
                str(scope or "project").lower(),
                str(memory_type or "notes").lower(),
                clean_title.lower()[:80],
            ]
        )
        append_open_question_entry(
            context=f"Memory capture confirmation needed: {clean_title}",
            needed=needed,
            source=f"topic-capture:{source}",
            turn_id=str(turn_id or ""),
            dedupe_key=dedupe_key,
        )
        return TopicCaptureResult(
            decision="queued_for_confirmation",
            local_saved=False,
            mcp_saved=False,
            reason="between_ask_and_save_threshold",
        )

    topic_tags = [str(tag).strip() for tag in (tags or []) if str(tag).strip()]
    hints = {"extraction_confidence": score, "source": str(source or "")}
    tool_args = {
        "title": clean_title,
        "content": clean_content,
        "type": str(memory_type or "notes").strip().lower(),
        "tags": topic_tags,
        "hints": hints,
    }
    local_saved = False
    mcp_saved = False

    record = build_structured_mirror_record(
        tool_args=tool_args,
        write_meta={
            "write_origin": f"topic_capture:{source}",
            "scope": str(scope or "project"),
            "session_id": str(session_id or ""),
            "task_id": str(effective_task_id or ""),
            "turn_id": str(turn_id or ""),
        },
        tool_name=f"topic_capture:{source}",
        effective_task_id=str(effective_task_id or ""),
    )
    if record:
        upsert_structured_mirror_record(record)
        local_saved = True

    mcp_saved, mcp_reason = _try_mcp_memory_save(
        tool_args=tool_args,
        effective_task_id=str(effective_task_id or ""),
        session_id=str(session_id or ""),
        turn_id=str(turn_id or ""),
    )
    if not mcp_saved:
        # No memory MCP reachable: the facade writes into the Obsidian vault.
        try:
            from agent.memory_facade import MODE_NONE, MemoryFacade

            facade = MemoryFacade.for_process()
            if facade.mode != MODE_NONE:
                vault_result = facade.save(
                    title=clean_title, content=clean_content,
                    type=str(memory_type or "notes"), tags=topic_tags, scope=str(scope or "project"),
                )
                if vault_result.ok:
                    mcp_saved, mcp_reason = True, f"saved_via_{vault_result.backend}"
        except Exception as exc:
            logger.debug("topic_capture: vault fallback failed: %s", exc)
    reason = "saved_local_and_mcp" if local_saved and mcp_saved else (
        "saved_local_only" if local_saved else mcp_reason
    )
    return TopicCaptureResult(
        decision="saved",
        local_saved=local_saved,
        mcp_saved=mcp_saved,
        reason=reason,
    )
