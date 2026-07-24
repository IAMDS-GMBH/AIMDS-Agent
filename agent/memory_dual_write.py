"""Bridge helpers for dual-writing memory across MCP + local Hermes memory."""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from hermes_constants import get_hermes_home


MCP_MIRROR_STORE_FILENAME = "MCP_MIRROR_MEMORY.jsonl"
FILESYSTEM_INDEX_FILENAME = "index.json"
FILESYSTEM_USER_DIR = "user"
FILESYSTEM_PROJECT_DIR = "project"


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


def _filesystem_memory_root() -> Path:
    root = get_hermes_home() / "memories"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _filesystem_index_path() -> Path:
    return _filesystem_memory_root() / FILESYSTEM_INDEX_FILENAME


def _ensure_filesystem_memory_layout() -> None:
    root = _filesystem_memory_root()
    (root / FILESYSTEM_USER_DIR).mkdir(parents=True, exist_ok=True)
    (root / FILESYSTEM_PROJECT_DIR).mkdir(parents=True, exist_ok=True)
    idx = _filesystem_index_path()
    if not idx.exists():
        idx.write_text("{}", encoding="utf-8")


def _load_filesystem_index() -> Dict[str, Any]:
    _ensure_filesystem_memory_layout()
    idx = _filesystem_index_path()
    try:
        data = json.loads(idx.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _save_filesystem_index(index: Dict[str, Any]) -> None:
    _ensure_filesystem_memory_layout()
    tmp = _filesystem_index_path().with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(index or {}, f, ensure_ascii=False, indent=2, sort_keys=True)
    tmp.replace(_filesystem_index_path())


def _frontmatter_from_record(record: Dict[str, Any]) -> str:
    meta = {
        "slug": str(record.get("slug") or ""),
        "title": str(record.get("title") or ""),
        "type": str(record.get("type") or "notes"),
        "scope": str(record.get("scope") or "project"),
        "tags": _clean_tags(record.get("tags")),
        "updated_at": int(record.get("updated_at") or int(time.time())),
        "confidence": (
            record.get("hints", {}).get("extraction_confidence")
            if isinstance(record.get("hints"), dict)
            else None
        ),
    }
    return "---\n" + json.dumps(meta, ensure_ascii=False, indent=2) + "\n---\n"


def _record_filesystem_path(record: Dict[str, Any]) -> Path:
    _ensure_filesystem_memory_layout()
    scope = str(record.get("scope") or "project").lower()
    subdir = FILESYSTEM_USER_DIR if scope == "user" else FILESYSTEM_PROJECT_DIR
    slug = str(record.get("slug") or record.get("id") or "")
    if not slug:
        slug = _record_slug(str(record.get("type") or "notes"), str(record.get("title") or "untitled"))
    return _filesystem_memory_root() / subdir / f"{slug}.md"


def _write_record_filesystem(record: Dict[str, Any]) -> None:
    if not isinstance(record, dict):
        return
    path = _record_filesystem_path(record)
    body = str(record.get("content") or "").strip()
    if not body:
        body = str(record.get("title") or "").strip()
    path.write_text(_frontmatter_from_record(record) + "\n" + body + "\n", encoding="utf-8")

    index = _load_filesystem_index()
    slug = str(record.get("slug") or "")
    if slug:
        index[slug] = {
            "path": str(path.relative_to(_filesystem_memory_root())),
            "scope": str(record.get("scope") or "project"),
            "type": str(record.get("type") or "notes"),
            "title": str(record.get("title") or ""),
            "updated_at": int(record.get("updated_at") or int(time.time())),
        }
        _save_filesystem_index(index)


def _delete_record_filesystem(slug: str) -> None:
    if not slug:
        return
    index = _load_filesystem_index()
    entry = index.get(slug)
    if isinstance(entry, dict):
        rel = str(entry.get("path") or "").strip()
        if rel:
            p = _filesystem_memory_root() / rel
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass
        index.pop(slug, None)
        _save_filesystem_index(index)
        return
    for sub in (FILESYSTEM_USER_DIR, FILESYSTEM_PROJECT_DIR):
        p = _filesystem_memory_root() / sub / f"{slug}.md"
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass


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
            # Keep editable filesystem mirror in sync.
            try:
                _write_record_filesystem(merged)
            except Exception:
                pass
            return

    # No existing slug match — append new record
    if "updated_at" not in record:
        record["updated_at"] = int(time.time())
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    try:
        _write_record_filesystem(record)
    except Exception:
        pass


# Backward-compat alias (old callers used append_*)
append_structured_mirror_record = upsert_structured_mirror_record


_COMPACT_CURSOR_FILENAME = ".mirror_compact_cursor"


def _compact_cursor_path() -> Path:
    return get_hermes_home() / "memories" / _COMPACT_CURSOR_FILENAME


def _load_compact_cursor() -> int:
    """Return the record count at last compaction (0 if never compacted)."""
    try:
        p = _compact_cursor_path()
        return int(p.read_text(encoding="utf-8").strip()) if p.exists() else 0
    except Exception:
        return 0


def _save_compact_cursor(count: int) -> None:
    try:
        _compact_cursor_path().write_text(str(count), encoding="utf-8")
    except Exception:
        pass


def compact_mirror_store(
    *,
    max_age_days: int = 90,
    max_records: int = 200,
    force: bool = False,
) -> int:
    """Compact the JSONL mirror store: merge near-duplicates and drop stale records.

    Runs only when the store has grown by at least ``max_records // 4`` records
    since the last compaction (unless ``force=True``).

    Compaction steps:
    1. Drop records older than ``max_age_days``.
    2. Within each ``(type, scope)`` bucket, merge records whose slug token sets
       overlap ≥ 70% — keep the most recently updated record, merge tags.
    3. Rewrite JSONL atomically.

    Returns the number of records removed/merged.
    """
    path = _memory_mirror_store_path()
    if not path.exists():
        return 0

    lines = [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    current_count = len(lines)

    if not force:
        last_count = _load_compact_cursor()
        threshold = max(10, max_records // 4)
        if current_count - last_count < threshold:
            return 0

    now = int(time.time())
    cutoff = now - max_age_days * 86400

    rows: List[Dict[str, Any]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        # Drop stale records
        ts = int(row.get("updated_at") or row.get("created_at") or now)
        if ts < cutoff:
            continue
        rows.append(row)

    # Merge near-duplicates within (type, scope) buckets
    def _slug_tokens(r: Dict[str, Any]) -> set:
        slug = str(r.get("slug") or "")
        return set(_tokenize_for_recall(slug))

    merged: List[Dict[str, Any]] = []
    used: List[bool] = [False] * len(rows)

    for i, row_i in enumerate(rows):
        if used[i]:
            continue
        used[i] = True
        bucket_type = str(row_i.get("type") or "")
        bucket_scope = str(row_i.get("scope") or "")
        tokens_i = _slug_tokens(row_i)
        merged_tags = list(row_i.get("tags") or [])
        representative = dict(row_i)

        for j in range(i + 1, len(rows)):
            if used[j]:
                continue
            row_j = rows[j]
            if str(row_j.get("type") or "") != bucket_type:
                continue
            if str(row_j.get("scope") or "") != bucket_scope:
                continue
            tokens_j = _slug_tokens(row_j)
            if not tokens_i or not tokens_j:
                continue
            overlap = len(tokens_i & tokens_j) / max(len(tokens_i), len(tokens_j))
            if overlap >= 0.70:
                used[j] = True
                # Keep most recently updated
                ts_i = int(representative.get("updated_at") or representative.get("created_at") or 0)
                ts_j = int(row_j.get("updated_at") or row_j.get("created_at") or 0)
                if ts_j > ts_i:
                    representative = dict(row_j)
                # Merge tags
                for tag in _clean_tags(row_j.get("tags")):
                    if tag not in merged_tags:
                        merged_tags.append(tag)

        representative["tags"] = merged_tags
        merged.append(representative)

    removed = current_count - len(merged)
    if removed <= 0 and current_count == len(merged):
        # Nothing to do; just update cursor
        _save_compact_cursor(current_count)
        return 0

    # Atomic rewrite via temp file
    tmp_path = path.with_suffix(".jsonl.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            for rec in merged:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        tmp_path.replace(path)
        _save_compact_cursor(len(merged))
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        return 0

    return max(0, removed)


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
    scope: Optional[str] = None,
) -> List[Dict[str, Any]]:
    path = _memory_mirror_store_path()
    if not path.exists():
        return []

    type_filter = str(memory_type or "").strip().lower()
    query_filter = str(query or "").strip().lower()
    scope_filter_r = str(scope or "").strip().lower()
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
        if scope_filter_r and str(row.get("scope") or "").strip().lower() != scope_filter_r:
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


def delete_structured_mirror_record(slug: str) -> bool:
    """Remove the record with the given slug from the JSONL mirror store.

    Returns True if a record was found and removed, False if not found.
    """
    path = _memory_mirror_store_path()
    if not path.exists():
        return False

    lines = path.read_text(encoding="utf-8").splitlines()
    kept: List[str] = []
    removed = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            row = json.loads(stripped)
            if str(row.get("slug") or row.get("id") or "") == slug:
                removed += 1
                continue
        except Exception:
            pass
        kept.append(stripped)

    if removed == 0:
        return False

    tmp_path = path.with_suffix(".jsonl.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            for line in kept:
                f.write(line + "\n")
        tmp_path.replace(path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        return False

    try:
        _delete_record_filesystem(slug)
    except Exception:
        pass
    return True


def list_filesystem_memory_records(
    *,
    limit: int = 40,
    scope: Optional[str] = None,
    memory_type: Optional[str] = None,
    query: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List editable filesystem memory records from index.json."""
    idx = _load_filesystem_index()
    scope_f = str(scope or "").strip().lower()
    type_f = str(memory_type or "").strip().lower()
    query_f = str(query or "").strip().lower()
    rows: List[Dict[str, Any]] = []
    for slug, meta in idx.items():
        if not isinstance(meta, dict):
            continue
        row = {
            "slug": str(slug),
            "path": str(meta.get("path") or ""),
            "scope": str(meta.get("scope") or "project"),
            "type": str(meta.get("type") or "notes"),
            "title": str(meta.get("title") or ""),
            "updated_at": int(meta.get("updated_at") or 0),
        }
        if scope_f and row["scope"].lower() != scope_f:
            continue
        if type_f and row["type"].lower() != type_f:
            continue
        if query_f:
            hay = f'{row["slug"]} {row["title"]} {row["path"]}'.lower()
            if query_f not in hay:
                continue
        rows.append(row)
    rows.sort(key=lambda r: int(r.get("updated_at") or 0), reverse=True)
    return rows[: max(1, int(limit or 40))]


def resolve_filesystem_memory_path(slug: str) -> Optional[Path]:
    """Resolve a memory slug to its editable filesystem path."""
    idx = _load_filesystem_index()
    entry = idx.get(str(slug or ""))
    if isinstance(entry, dict):
        rel = str(entry.get("path") or "").strip()
        if rel:
            p = _filesystem_memory_root() / rel
            return p if p.exists() else None
    for sub in (FILESYSTEM_USER_DIR, FILESYSTEM_PROJECT_DIR):
        p = _filesystem_memory_root() / sub / f"{slug}.md"
        if p.exists():
            return p
    return None


def _parse_frontmatter_and_body(text: str) -> Tuple[Dict[str, Any], str]:
    raw = str(text or "")
    if not raw.startswith("---"):
        return {}, raw.strip()
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}, raw.strip()
    fm_raw = parts[1].strip()
    body = parts[2].strip()
    try:
        fm = json.loads(fm_raw)
        if isinstance(fm, dict):
            return fm, body
    except Exception:
        pass
    return {}, body


def reconcile_filesystem_memory_to_structured() -> Dict[str, int]:
    """Apply filesystem edits (HermesMemory) back into the structured mirror."""
    _ensure_filesystem_memory_layout()
    root = _filesystem_memory_root()
    updated = 0
    skipped = 0
    files = list((root / FILESYSTEM_USER_DIR).glob("*.md")) + list((root / FILESYSTEM_PROJECT_DIR).glob("*.md"))
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            skipped += 1
            continue
        fm, body = _parse_frontmatter_and_body(text)
        slug = str(fm.get("slug") or path.stem).strip()
        scope = str(fm.get("scope") or (FILESYSTEM_USER_DIR if path.parent.name == FILESYSTEM_USER_DIR else "project"))
        mem_type = str(fm.get("type") or ("profile" if scope == "user" else "project"))
        title = str(fm.get("title") or path.stem.replace("-", " ").strip()).strip()
        tags = _clean_tags(fm.get("tags"))
        hints = {}
        conf = fm.get("confidence")
        try:
            conf = float(conf) if conf is not None else None
        except Exception:
            conf = None
        if conf is not None:
            hints["extraction_confidence"] = max(0.0, min(1.0, conf))
        rec = {
            "id": str(uuid4()),
            "slug": slug,
            "created_at": int(time.time()),
            "updated_at": int(time.time()),
            "kind": "mcp_memory_save_mirror",
            "type": mem_type or "notes",
            "title": title,
            "content": body,
            "hints": hints,
            "tags": tags,
            "location": "",
            "mcp_source": "filesystem",
            "scope": "user" if scope == "user" else "project",
            "target": "user" if scope == "user" else "memory",
            "write_origin": "filesystem_reconcile",
            "source_tool": "filesystem",
            "session_id": "",
            "task_id": "",
            "tool_call_id": "",
        }
        try:
            upsert_structured_mirror_record(rec)
            updated += 1
        except Exception:
            skipped += 1
    return {"updated": updated, "skipped": skipped}


def _tokenize_for_recall(text: str) -> List[str]:
    """Tokenize text into lowercase alphanumeric terms ≥3 chars for TF-IDF overlap scoring."""
    return [p for p in re.sub(r"[^\w\s]", " ", str(text or "").lower()).split() if len(p) >= 3]


def build_mirror_recall_context(
    query: str,
    *,
    top_k: int = 5,
    max_chars: int = 1200,
    scope_filter: Optional[str] = None,
) -> str:
    """Score JSONL mirror records against the query and return a compact ranked block.

    Scoring: token overlap (primary) + recency decay + user-scope boost.
    Returns empty string when the store is empty, unreadable, or nothing is relevant.

    Args:
        query: The current user message text used as the retrieval query.
        top_k: Maximum number of records to include in the block.
        max_chars: Hard character cap on the returned block.
        scope_filter: When set, only records with this scope are considered.
    """
    if not str(query or "").strip():
        return ""

    path = _memory_mirror_store_path()
    if not path.exists():
        return ""

    query_terms = set(_tokenize_for_recall(query))
    if not query_terms:
        return ""

    now = int(time.time())
    scored: List[tuple] = []

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

        scope = str(row.get("scope") or "project").lower()
        if scope_filter and scope != scope_filter.lower():
            continue

        title = str(row.get("title") or "")
        content = str(row.get("content") or "")
        tags = " ".join(_clean_tags(row.get("tags")))
        record_text = f"{title} {content} {tags}"
        record_terms = set(_tokenize_for_recall(record_text))

        overlap = len(query_terms & record_terms)
        if overlap == 0:
            continue

        age_hours = max(0.0, (now - int(row.get("updated_at") or row.get("created_at") or now)) / 3600.0)
        recency = 1.0 / (1.0 + math.log1p(age_hours))
        scope_boost = 0.3 if scope == "user" else 0.0
        score = overlap * 1.0 + recency * 0.5 + scope_boost

        scored.append((score, row, title, content))

    if not scored:
        return ""

    scored.sort(key=lambda t: t[0], reverse=True)
    picked = scored[: max(1, int(top_k or 5))]

    lines: List[str] = ["Relevant saved memories:"]
    for _, row, title, content in picked:
        scope = str(row.get("scope") or "project")
        mem_type = str(row.get("type") or "")
        label = f"[{mem_type}] " if mem_type else ""
        if title and content:
            lines.append(f"- {label}{title}: {content}")
        elif title:
            lines.append(f"- {label}{title}")
        elif content:
            lines.append(f"- {label}{content}")

    block = "\n".join(lines).strip()
    if len(block) > max_chars:
        block = block[:max_chars - 1].rstrip() + "…"
    return block


def format_structured_mirror_for_system_prompt(
    *,
    active_context: str = "",
    scope_filter: Optional[str] = None,
) -> Optional[str]:
    """Format JSONL mirror records into a compact prompt block for the volatile tier.

    Returns None when the store is empty or unreadable (safe no-op).
    Groups by scope:
    - user-scope records: always included (unless scope_filter restricts)
    - project-scope records: included only when active_context is non-empty
    Deduplicates by slug (last-write-wins order from JSONL).

    Args:
        active_context: a project/cwd hint (e.g. cwd basename or project name).
                        When empty, project-scope records are still included
                        as a safe default so behavior is backward-compatible.
        scope_filter: when set (e.g. "user"), only records with that scope
                      are included. None means include all scopes.
    """
    path = _memory_mirror_store_path()
    if not path.exists():
        return None

    seen_slugs: set = set()
    user_records: List[Dict[str, Any]] = []
    project_records: List[Dict[str, Any]] = []
    _scope_filter = str(scope_filter or "").strip().lower() if scope_filter else None

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
        if _scope_filter and scope != _scope_filter:
            continue
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


# ---------------------------------------------------------------------------
# memory_context → USER.md snapshot sync
# ---------------------------------------------------------------------------

_MCP_SNAPSHOT_MARKER = "<!-- mcp_context_snapshot -->"
# Profile-like top-level keys to extract from a memory_context JSON result.
_PROFILE_EXTRACT_KEYS = (
    "profile", "user", "user_profile", "personal_info", "summary",
    "name", "role", "preferences", "work_style", "language",
)
# Keys that are purely onboarding-control flags, not profile content.
_ONBOARDING_CONTROL_KEYS = frozenset({
    "onboarding_init_auto_started", "onboarding_question_flow_required",
    "onboarding_first_question", "onboarding_questions",
    "init_auto_started", "question_flow_required",
})


def _extract_profile_content_from_memory_context(result: Any) -> str:
    """Extract a human-readable profile snapshot from a memory_context result.

    Returns an empty string when no meaningful profile data is found.
    """
    if result is None:
        return ""
    text = str(result).strip()
    if not text:
        return ""

    try:
        parsed = json.loads(text)
    except Exception:
        # Plain-text result: use it directly if it looks like profile data.
        if len(text) >= 40 and not text.startswith("{") and not text.startswith("["):
            return text[:2000]
        return ""

    if not isinstance(parsed, dict):
        return ""

    # Check if the result is mostly just onboarding control flags (no real profile).
    content_keys = [k for k in parsed if k not in _ONBOARDING_CONTROL_KEYS]
    if not content_keys:
        return ""

    # Try to extract named profile section keys first.
    lines: list[str] = []
    for key in _PROFILE_EXTRACT_KEYS:
        val = parsed.get(key)
        if val is None:
            continue
        if isinstance(val, dict):
            sub = "; ".join(f"{k}: {v}" for k, v in val.items() if v is not None)
            if sub:
                lines.append(f"**{key}**: {sub}")
        elif isinstance(val, (list, tuple)):
            items = [str(i) for i in val if i]
            if items:
                lines.append(f"**{key}**: {', '.join(items)}")
        else:
            s = str(val).strip()
            if s:
                lines.append(f"**{key}**: {s}")

    # Fall back: any remaining non-control, non-empty string values.
    if not lines:
        for key in content_keys:
            val = parsed.get(key)
            if val is None:
                continue
            s = str(val).strip() if not isinstance(val, (dict, list)) else json.dumps(val)
            if s and len(s) >= 4:
                lines.append(f"**{key}**: {s[:400]}")
            if len(lines) >= 15:
                break

    return "\n".join(lines)[:2000]


def mirror_mcp_memory_context_to_user_md(
    agent: Any,
    function_name: str,
    result: Any,
) -> bool:
    """After a successful memory_context call, snapshot the profile into USER.md.

    Writes a clearly-marked snapshot section so that if MCP becomes unavailable
    in a future session, USER.md acts as a meaningful local fallback.

    Returns True when a write was performed.
    """
    from model_tools import _is_memory_context_tool_name  # avoid circular at module level
    if not _is_memory_context_tool_name(function_name):
        return False
    if not getattr(agent, "_memory_store", None):
        return False
    if not getattr(agent, "_user_profile_enabled", False):
        return False

    profile_content = _extract_profile_content_from_memory_context(result)
    if not profile_content or len(profile_content) < 40:
        return False

    try:
        path = agent._memory_store._path_for("user")
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
    except Exception:
        return False

    # Build the replacement/new snapshot section.
    snapshot_section = (
        f"\n{_MCP_SNAPSHOT_MARKER}\n"
        "## Remote Profile Snapshot\n"
        f"*Last synced from MCP memory_context.*\n\n"
        f"{profile_content}\n"
        f"{_MCP_SNAPSHOT_MARKER}\n"
    )

    # If section already exists and content is identical, skip.
    if _MCP_SNAPSHOT_MARKER in existing:
        start = existing.index(_MCP_SNAPSHOT_MARKER)
        end = existing.index(_MCP_SNAPSHOT_MARKER, start + len(_MCP_SNAPSHOT_MARKER)) + len(_MCP_SNAPSHOT_MARKER)
        old_section = existing[start : end + 1]
        if profile_content in old_section:
            return False  # already up to date
        new_text = existing[:start] + snapshot_section + existing[end + 1 :].lstrip("\n")
    else:
        new_text = existing.rstrip("\n") + "\n" + snapshot_section

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_text, encoding="utf-8")
        # Reload the in-memory store to reflect the new content.
        try:
            agent._memory_store.load_from_disk()
        except Exception:
            pass
        import logging as _logging
        _logging.getLogger(__name__).debug(
            "mirror_mcp_memory_context_to_user_md: wrote snapshot (%d chars) via %s",
            len(profile_content),
            function_name,
        )
        return True
    except Exception:
        return False


def mirror_local_memory_to_mcp(
    agent: Any,
    tool_name: str,
    tool_args: Dict[str, Any],
    tool_result: Any,
    *,
    effective_task_id: str = "",
    tool_call_id: Optional[str] = None,
) -> bool:
    """Mirror successful local Hermes memory writes to remote MCP memory_save if available."""
    if tool_name != "memory":
        return False
    if not tool_result_indicates_success(tool_result):
        return False

    args = tool_args or {}
    if args.get("__mcp_mirror"):
        return False  # Prevent re-mirror loop

    action = str(args.get("action") or "").strip().lower()
    if action not in {"add", "replace", "update_structured"}:
        return False

    content = str(args.get("content") or "").strip()
    if not content:
        return False

    valid_tools = set(getattr(agent, "valid_tool_names", []) or [])
    from agent.prompt_builder import _resolve_memory_save_tool_name

    mcp_save_tool = _resolve_memory_save_tool_name(valid_tools)
    if not mcp_save_tool:
        return False

    target = str(args.get("target") or "memory").strip().lower()
    mem_type = "profile" if target == "user" else "notes"

    # Extract first line or short snippet for title
    first_line = content.splitlines()[0].strip() if content else "Local memory update"
    title = first_line[:60].lstrip("#").strip() or "Local memory update"

    save_args = {
        "title": title,
        "content": content,
        "type": mem_type,
        "tags": ["local-sync", "hermes-memory"],
        "__mcp_mirror": True,
    }

    try:
        import run_agent as _ra

        _ra.handle_function_call(
            mcp_save_tool,
            save_args,
            effective_task_id,
            tool_call_id=f"mcp-mirror-{uuid4().hex[:8]}",
            session_id=agent.session_id or "",
            turn_id=getattr(agent, "_current_turn_id", "") or "",
            api_request_id=getattr(agent, "_current_api_request_id", "") or "",
            enabled_tools=list(valid_tools),
            skip_pre_tool_call_hook=True,
            skip_tool_request_middleware=True,
        )
        import logging as _logging

        _logging.getLogger(__name__).info(
            "mirror_local_memory_to_mcp: mirrored local memory write to %s", mcp_save_tool
        )
        return True
    except Exception as exc:
        import logging as _logging

        _logging.getLogger(__name__).debug(
            "mirror_local_memory_to_mcp failed for %s: %s", mcp_save_tool, exc
        )
        return False


