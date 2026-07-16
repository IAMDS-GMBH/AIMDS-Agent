"""Local audit stream for memory_context runtime decisions."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home

MEMORY_CONTEXT_AUDIT_FILENAME = "MCP_MEMORY_CONTEXT_AUDIT.jsonl"
MEMORY_CONTEXT_AUDIT_VERSION = 1


def _audit_path() -> Path:
    mem_dir = get_hermes_home() / "memories"
    mem_dir.mkdir(parents=True, exist_ok=True)
    return mem_dir / MEMORY_CONTEXT_AUDIT_FILENAME


def append_memory_context_audit_event(event: Dict[str, Any]) -> None:
    """Append one audit event to the local JSONL stream (best-effort)."""
    try:
        row = dict(event or {})
        row.setdefault("version", MEMORY_CONTEXT_AUDIT_VERSION)
        row.setdefault("ts", int(time.time()))
        with _audit_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def read_memory_context_audit_events(
    *,
    limit: int = 40,
    status: Optional[str] = None,
    reason: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Read memory_context audit events with optional filters."""
    path = _audit_path()
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
