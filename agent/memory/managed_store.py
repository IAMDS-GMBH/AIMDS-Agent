from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from uuid import uuid4

from hermes_constants import get_hermes_home


def _tokenize(text: str) -> list[str]:
    return [p for p in "".join(ch.lower() if ch.isalnum() else " " for ch in text).split() if len(p) > 2]


@dataclass
class ManagedMemoryRecord:
    id: str
    created_at: int
    scope: str
    target: str
    action: str
    content: str
    old_text: str
    session_id: str
    confidence: float
    importance: float
    metadata: Dict[str, Any]


class ManagedMemoryStore:
    """Additive managed-memory store layered over MEMORY.md/USER.md.

    Phase-1 goals:
    - Keep existing memory behavior untouched.
    - Persist structured write history when enabled.
    - Support lightweight ranked recall for per-turn hybrid injection.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        capture_mode: str = "off",
        retrieval_enabled: bool = False,
        retrieval_top_k: int = 5,
        retrieval_max_chars: int = 1200,
        retrieval_scopes: Optional[Sequence[str]] = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.capture_mode = (capture_mode or "off").strip().lower()
        if self.capture_mode not in {"off", "suggest", "auto"}:
            self.capture_mode = "off"

        self.retrieval_enabled = bool(retrieval_enabled)
        self.retrieval_top_k = max(1, int(retrieval_top_k or 5))
        self.retrieval_max_chars = max(200, int(retrieval_max_chars or 1200))
        self.retrieval_scopes = set(retrieval_scopes or ["user", "project", "session"])

        memories_dir = get_hermes_home() / "memories"
        memories_dir.mkdir(parents=True, exist_ok=True)
        self._records_path = memories_dir / "MANAGED_MEMORY.jsonl"
        self._pending_path = memories_dir / "MANAGED_MEMORY.pending.jsonl"

    def _append_jsonl(self, path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _iter_jsonl(self, path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        except OSError:
            return []
        return rows

    def record_write(
        self,
        *,
        action: str,
        target: str,
        content: str,
        old_text: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Capture managed-memory write events in suggest/auto modes."""
        if not self.enabled or self.capture_mode == "off":
            return
        if action not in {"add", "replace", "remove"}:
            return

        meta = dict(metadata or {})
        scope = str(meta.get("scope") or "session").strip().lower()
        if scope not in {"global", "user", "project", "session"}:
            scope = "session"

        rec = ManagedMemoryRecord(
            id=str(uuid4()),
            created_at=int(time.time()),
            scope=scope,
            target=str(target or "memory"),
            action=action,
            content=str(content or ""),
            old_text=str(old_text or ""),
            session_id=str(meta.get("session_id") or ""),
            confidence=float(meta.get("confidence", 0.8)),
            importance=float(meta.get("importance", 0.6)),
            metadata=meta,
        )
        payload = asdict(rec)
        if self.capture_mode == "suggest":
            self._append_jsonl(self._pending_path, payload)
        else:
            self._append_jsonl(self._records_path, payload)

    def build_recall_context(self, query: str, *, top_k: Optional[int] = None) -> str:
        """Return a compact ranked managed-memory block for the turn."""
        if not self.enabled or not self.retrieval_enabled:
            return ""
        rows = self._iter_jsonl(self._records_path)
        if not rows:
            return ""
        terms = set(_tokenize(query or ""))
        if not terms:
            return ""

        now = int(time.time())
        k = max(1, int(top_k or self.retrieval_top_k))
        scored: list[tuple[float, dict[str, Any], str]] = []
        for row in rows:
            scope = str(row.get("scope") or "").strip().lower()
            if scope and scope not in self.retrieval_scopes:
                continue
            action = str(row.get("action") or "")
            if action == "remove":
                continue

            content = str(row.get("content") or "")
            if not content:
                continue
            c_terms = set(_tokenize(content))
            overlap = len(terms & c_terms)
            if overlap <= 0:
                continue
            age_hours = max(0.0, (now - int(row.get("created_at", now))) / 3600.0)
            recency = 1.0 / (1.0 + math.log1p(age_hours))
            confidence = float(row.get("confidence", 0.8))
            importance = float(row.get("importance", 0.6))
            score = overlap * 1.0 + recency * 0.6 + confidence * 0.5 + importance * 0.4
            scored.append((score, row, content))

        if not scored:
            return ""
        scored.sort(key=lambda t: t[0], reverse=True)
        picked = scored[:k]

        lines: list[str] = ["Managed memory recall (ranked):"]
        for _, row, content in picked:
            target = str(row.get("target") or "memory")
            scope = str(row.get("scope") or "session")
            lines.append(f"- [{scope}/{target}] {content.strip()}")
        block = "\n".join(lines).strip()
        if len(block) > self.retrieval_max_chars:
            block = block[: self.retrieval_max_chars - 1].rstrip() + "…"
        return block

