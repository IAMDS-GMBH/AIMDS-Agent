"""Bridge helpers for dual-writing memory across MCP + local Hermes memory."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple


def is_mcp_memory_save_tool(tool_name: str) -> bool:
    if not tool_name:
        return False
    return tool_name == "memory_save" or tool_name.endswith("_memory_save")


def tool_result_indicates_success(result: Any) -> bool:
    if result is None:
        return False
    text = str(result).strip()
    if not text:
        return False
    try:
        payload = json.loads(text)
    except Exception:
        # Non-JSON MCP tool responses are treated as success by default.
        return True
    if isinstance(payload, dict):
        if payload.get("success") is False:
            return False
        if payload.get("error") and payload.get("success") is not True:
            return False
    return True


def build_local_mirror_payload(args: Dict[str, Any]) -> Tuple[str, str]:
    """Map MCP memory_save args to local memory(action=add) payload.

    Returns (target, content). Empty content means "do not mirror".
    """
    args = args or {}
    mem_type = str(args.get("type") or args.get("memory_type") or "").strip().lower()
    target = "user" if mem_type in {"profile", "person"} else "memory"

    title = str(args.get("title") or "").strip()
    body = str(args.get("content") or args.get("summary") or args.get("fact") or "").strip()
    if not body:
        hints = args.get("hints")
        if isinstance(hints, dict) and hints:
            try:
                body = json.dumps(hints, ensure_ascii=False, sort_keys=True)
            except Exception:
                body = str(hints).strip()

    if not title and not body:
        return target, ""

    tags = args.get("tags")
    location = str(args.get("location") or "").strip()
    mcp_source = str(args.get("mcp_source") or "").strip()

    lines = []
    if title:
        lines.append(f"Title: {title}")
    if mem_type:
        lines.append(f"Type: {mem_type}")
    if body:
        lines.append(body)
    if isinstance(tags, list) and tags:
        clean_tags = [str(t).strip() for t in tags if str(t).strip()]
        if clean_tags:
            lines.append(f"Tags: {', '.join(clean_tags)}")
    if location:
        lines.append(f"Location: {location}")
    if mcp_source:
        lines.append(f"Source: {mcp_source}")
    return target, "\n".join(lines).strip()


def mirror_mcp_memory_save_to_local(
    agent: Any,
    tool_name: str,
    tool_args: Dict[str, Any],
    tool_result: Any,
    *,
    effective_task_id: str = "",
    tool_call_id: Optional[str] = None,
) -> None:
    """Mirror successful MCP memory_save writes into local Hermes memory."""
    if not getattr(agent, "_memory_store", None):
        return
    if not is_mcp_memory_save_tool(tool_name):
        return
    if not tool_result_indicates_success(tool_result):
        return

    target, content = build_local_mirror_payload(tool_args or {})
    if not content:
        return

    try:
        write_meta = agent._build_memory_write_metadata(
            task_id=effective_task_id,
            tool_call_id=tool_call_id,
        )
        write_meta["write_origin"] = "mcp_mirror"
        write_meta["source_tool"] = tool_name
        # MCP memory types map most naturally to user/project scopes.
        if target == "user":
            write_meta.setdefault("scope", "user")
        else:
            write_meta.setdefault("scope", "project")
    except Exception:
        write_meta = {"write_origin": "mcp_mirror", "source_tool": tool_name}

    try:
        from tools.memory_tool import memory_tool as _memory_tool

        _memory_tool(
            action="add",
            target=target,
            content=content,
            store=agent._memory_store,
            metadata=write_meta,
        )
    except Exception:
        pass

