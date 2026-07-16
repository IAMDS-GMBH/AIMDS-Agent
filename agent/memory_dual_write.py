"""Bridge helpers for dual-writing memory across MCP + local Hermes memory."""

from __future__ import annotations

import json
import re
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


def _slugify(text: str) -> str:
    """Normalize text to a compact slug for deduplication keying."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text.strip("-")[:120]


def _record_slug(mem_type: str, title: str) -> str:
    """Stable dedup key combining type and title slugs."""
    return _slugify(f"{mem_type or 'notes'}-{title or 'untitled'}")


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
    slug = _record_slug(mem_type or "notes", title)
    return {
        "id": str(uuid4()),
        "slug": slug,
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


def upsert_structured_mirror_record(record: Dict[str, Any]) -> None:
    """Write a structured mirror record, updating an existing slug match in-place."""
    if not isinstance(record, dict) or not record:
        return
    slug = str(record.get("slug") or "")
    path = _memory_mirror_store_path()

    if slug and path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
        updated_lines: List[str] = []
        found = False
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                existing = json.loads(line)
            except Exception:
                updated_lines.append(line)
                continue
            if isinstance(existing, dict) and existing.get("slug") == slug:
                # Preserve original id and created_at on update
                merged = dict(existing)
                merged.update(record)
                merged["id"] = existing.get("id") or record.get("id") or str(uuid4())
                merged["created_at"] = existing.get("created_at") or record.get("created_at")
                merged["updated_at"] = int(time.time())
                updated_lines.append(json.dumps(merged, ensure_ascii=False))
                found = True
            else:
                updated_lines.append(line)
        if found:
            path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
            return

    # No existing slug match — append new record
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# Backward-compat alias (old callers used append_*)
append_structured_mirror_record = upsert_structured_mirror_record


def annotate_tool_result_with_local_mirror(tool_result: Any) -> Any:
    """Best-effort: annotate tool result payload to signal local mirror write."""
    if isinstance(tool_result, dict):
        merged = dict(tool_result)
        merged["local_mirror"] = True
        return merged
    text = str(tool_result or "").strip()
    if not text:
        return tool_result
    try:
        parsed = json.loads(text)
    except Exception:
        return tool_result
    if isinstance(parsed, dict):
        parsed["local_mirror"] = True
        try:
            return json.dumps(parsed, ensure_ascii=False)
        except Exception:
            return tool_result
    return tool_result


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


def format_structured_mirror_for_system_prompt(
    *,
    active_context: str = "",
) -> Optional[str]:
    """Format JSONL mirror records into a compact prompt block for the volatile tier.

    Returns None when the store is empty or unreadable (safe no-op).
    Groups by scope:
    - user-scope records: always included
    - project-scope records: included only when active_context is non-empty
    Deduplicates by slug (last-write-wins order from JSONL).

    Args:
        active_context: a project/cwd hint (e.g. cwd basename or project name).
                        When empty, project-scope records are still included
                        as a safe default so behavior is backward-compatible.
    """
    path = _memory_mirror_store_path()
    if not path.exists():
        return None

    seen_slugs: set = set()
    user_records: List[Dict[str, Any]] = []
    project_records: List[Dict[str, Any]] = []

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
        slug = str(row.get("slug") or row.get("id") or "")
        if slug and slug in seen_slugs:
            continue
        if slug:
            seen_slugs.add(slug)
        scope = str(row.get("scope") or "project").lower()
        if scope == "user":
            user_records.append(row)
        else:
            project_records.append(row)

    if not user_records and not project_records:
        return None

    def _format_record(r: Dict[str, Any]) -> str:
        title = str(r.get("title") or "").strip()
        content = str(r.get("content") or "").strip()
        mem_type = str(r.get("type") or "").strip()
        tags = _clean_tags(r.get("tags"))
        parts = []
        label = f"[{mem_type}] " if mem_type else ""
        if title and content:
            parts.append(f"- {label}{title}: {content}")
        elif title:
            parts.append(f"- {label}{title}")
        elif content:
            parts.append(f"- {label}{content}")
        else:
            return ""
        if tags:
            parts.append(f"  tags: {', '.join(tags)}")
        return "\n".join(parts)

    blocks: List[str] = []
    if user_records:
        lines = [_format_record(r) for r in user_records]
        lines = [l for l in lines if l]
        if lines:
            blocks.append("## User preferences & profile\n" + "\n".join(lines))
    # Project-scope records: inject when active_context is set, or always as
    # a safe backward-compatible default when active_context is empty.
    _include_project = bool(active_context) or True  # keep "always" default for now
    if project_records and _include_project:
        lines = [_format_record(r) for r in project_records]
        lines = [l for l in lines if l]
        if lines:
            header = f"## Saved project context" + (f" ({active_context})" if active_context else "")
            blocks.append(header + "\n" + "\n".join(lines))

    if not blocks:
        return None
    return "\n\n".join(blocks)


# ── Preference patterns for auto-capture ─────────────────────────────────────
# Each pattern captures a preference-like statement from assistant responses.
# Group 1 = optional subject/label, Group 2 = the preference value.
_PREFERENCE_PATTERNS: List[re.Pattern] = [
    # "you prefer X" / "you'd prefer X" / "you seem to prefer X"
    re.compile(r"\byou(?:'d| would| seem to)?\s+prefer\s+(.{3,80})", re.I),
    # "you like X" / "you tend to like X"
    re.compile(r"\byou(?:\s+tend\s+to)?\s+like\s+(.{3,80})", re.I),
    # "you want X" / "you'd want X"
    re.compile(r"\byou(?:'d| would)?\s+want\s+(.{3,80})", re.I),
    # "I'll remember that X" / "I'll keep in mind that X"
    re.compile(r"\bI(?:'ll| will)\s+(?:remember|keep in mind)\s+that\s+(.{3,120})", re.I),
    # "noted: X" / "noted — X"
    re.compile(r"\bnoted[:\s—-]+(.{3,120})", re.I),
    # "your X is Y" / "your X: Y"
    re.compile(r"\byour\s+([\w\s]{2,30}?)\s+(?:is|:)\s+(.{3,80})", re.I),
    # "you mentioned X" / "you said X"
    re.compile(r"\byou\s+(?:mentioned|said)\s+(?:that\s+)?(.{3,120})", re.I),
    # "I'll keep that in mind" — general but useful, captures surrounding sentence
    re.compile(r"\bI(?:'ll| will)\s+keep\s+that\s+in\s+mind\b", re.I),
]

# Short fillers that are not real preference content
_FILLER_RE = re.compile(
    r"^(?:it|that|this|so|yes|no|ok|okay|sure|absolutely|of course|got it|understood|great|noted)\s*[.!]?$",
    re.I,
)


def detect_preference_candidates(text: str) -> List[Dict[str, Any]]:
    """Scan assistant response text for preference-like statements.

    Returns a list of candidate dicts with keys: title, content, type, tags.
    Empty list when nothing worth saving is detected.

    This is intentionally conservative — false-negatives are better than
    false-positives for unsolicited memory writes.
    """
    if not text or len(text.strip()) < 10:
        return []

    candidates: List[Dict[str, Any]] = []
    seen_contents: set = set()

    for pattern in _PREFERENCE_PATTERNS:
        for match in pattern.finditer(text):
            groups = [g for g in match.groups() if g]
            if not groups:
                # Pattern matched but no capture group (e.g. "I'll keep that in mind")
                # Extract surrounding sentence as context
                start = max(0, match.start() - 40)
                end = min(len(text), match.end() + 80)
                snippet = text[start:end].strip()
                groups = [snippet]

            raw = " ".join(groups).strip().rstrip(".!,;")
            # Clean up common noise
            raw = re.sub(r"\s+", " ", raw).strip()

            if len(raw) < 4 or len(raw) > 200:
                continue
            if _FILLER_RE.match(raw):
                continue
            # Avoid near-duplicates
            key = raw.lower()[:60]
            if key in seen_contents:
                continue
            seen_contents.add(key)

            # Classify type: profile if personal preference, else notes
            mem_type = "profile"
            tags = ["auto-captured"]
            title = raw[:60].strip()
            if len(raw) > 60:
                title = raw[:57].strip() + "…"

            candidates.append({
                "title": title,
                "content": raw,
                "type": mem_type,
                "tags": tags,
            })

    return candidates[:5]  # Cap to avoid noisy saves in verbose responses


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
            upsert_structured_mirror_record(structured)
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
