"""Bridge helpers for dual-writing memory across MCP + local Hermes memory."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from hermes_constants import get_hermes_home


MCP_MIRROR_STORE_FILENAME = "MCP_MIRROR_MEMORY.jsonl"


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


def _memory_mirror_store_path() -> Path:
    mem_dir = get_hermes_home() / "memories"
    mem_dir.mkdir(parents=True, exist_ok=True)
    return mem_dir / MCP_MIRROR_STORE_FILENAME


def _clean_tags(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(tag).strip() for tag in value if str(tag).strip()]


def _derive_scope_for_type(mem_type: str, fallback_scope: str) -> str:
    if mem_type in {"profile", "person"}:
        return "user"
    if mem_type in {"project", "tasks", "reference", "rule", "tool", "notes"}:
        return "project"
    scope = str(fallback_scope or "").strip().lower()
    if scope in {"global", "user", "project", "session"}:
        return scope
    return "project"


def build_structured_mirror_record(
    *,
    tool_args: Dict[str, Any],
    write_meta: Dict[str, Any],
    tool_name: str,
    effective_task_id: str,
    tool_call_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    args = tool_args or {}
    meta = write_meta or {}
    mem_type = str(args.get("type") or args.get("memory_type") or "").strip().lower()
    title = str(args.get("title") or "").strip()
    content = str(args.get("content") or args.get("summary") or args.get("fact") or "").strip()
    hints = args.get("hints")
    tags = _clean_tags(args.get("tags"))
    location = str(args.get("location") or "").strip()
    mcp_source = str(args.get("mcp_source") or "").strip()
    if not title and not content and not (isinstance(hints, dict) and hints):
        return None

    target = "user" if mem_type in {"profile", "person"} else "memory"
    scope = _derive_scope_for_type(mem_type, str(meta.get("scope") or ""))
    now = int(time.time())
    return {
        "id": str(uuid4()),
        "created_at": now,
        "updated_at": now,
        "kind": "mcp_memory_save_mirror",
        "type": mem_type or "notes",
        "title": title,
        "content": content,
        "hints": hints if isinstance(hints, dict) else {},
        "tags": tags,
        "location": location,
        "mcp_source": mcp_source,
        "scope": scope,
        "target": target,
        "write_origin": str(meta.get("write_origin") or "mcp_mirror"),
        "source_tool": tool_name,
        "session_id": str(meta.get("session_id") or ""),
        "task_id": effective_task_id or str(meta.get("task_id") or ""),
        "tool_call_id": tool_call_id or str(meta.get("tool_call_id") or ""),
    }


def append_structured_mirror_record(record: Dict[str, Any]) -> None:
    if not isinstance(record, dict) or not record:
        return
    path = _memory_mirror_store_path()
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_structured_mirror_records(
    *,
    limit: int = 20,
    memory_type: Optional[str] = None,
    query: Optional[str] = None,
) -> List[Dict[str, Any]]:
    path = _memory_mirror_store_path()
    if not path.exists():
        return []

    type_filter = str(memory_type or "").strip().lower()
    query_filter = str(query or "").strip().lower()
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
        if type_filter and str(row.get("type") or "").strip().lower() != type_filter:
            continue
        if query_filter:
            haystack = " ".join(
                [
                    str(row.get("title") or ""),
                    str(row.get("content") or ""),
                    " ".join(_clean_tags(row.get("tags"))),
                ]
            ).lower()
            if query_filter not in haystack:
                continue
        rows.append(row)

    rows.sort(key=lambda r: int(r.get("created_at") or 0), reverse=True)
    return rows[: max(1, int(limit or 20))]


def mirror_mcp_memory_save_to_local(
    agent: Any,
    tool_name: str,
    tool_args: Dict[str, Any],
    tool_result: Any,
    *,
    effective_task_id: str = "",
    tool_call_id: Optional[str] = None,
) -> bool:
    """Mirror successful MCP memory_save writes into local Hermes memory."""
    if not getattr(agent, "_memory_store", None):
        return False
    if not is_mcp_memory_save_tool(tool_name):
        return False
    if not tool_result_indicates_success(tool_result):
        return False

    target, content = build_local_mirror_payload(tool_args or {})
    if not content:
        return False

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

    structured_written = False
    try:
        structured = build_structured_mirror_record(
            tool_args=tool_args or {},
            write_meta=write_meta,
            tool_name=tool_name,
            effective_task_id=effective_task_id,
            tool_call_id=tool_call_id,
        )
        if structured:
            append_structured_mirror_record(structured)
            structured_written = True
    except Exception:
        pass

    flat_written = False
    try:
        from tools.memory_tool import memory_tool as _memory_tool

        flat_result = _memory_tool(
            action="add",
            target=target,
            content=content,
            store=agent._memory_store,
            metadata=write_meta,
        )
        try:
            parsed = json.loads(str(flat_result))
            flat_written = bool(parsed.get("success") is True)
        except Exception:
            # Non-JSON responses from the local memory tool are treated as success.
            flat_written = bool(str(flat_result).strip())
    except Exception:
        flat_written = False

    return structured_written or flat_written
