"""SQLite persistence for the LLM-free brief collector (AIS-305).

Normalised ``BriefItem`` rows live in ``~/.hermes/state.db`` next to
``mcp_records`` / ``api_calls`` / ``workday_calendar`` so the brief, the
mail/teams watermark and the interactive ``sql`` tool all read one table.

Tables
------
``brief_items``  one row per item (``id = source:kind:key``); ``prev_status``
                 / ``status_changed_at`` record the last status flip so a
                 brief can list "changed since the last brief" without
                 re-reading the source.
``brief_runs``   one row per collector run (sources line, item counts,
                 session id → tokens via ``api_calls``).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from cron.brief_sources.base import BriefItem

logger = logging.getLogger(__name__)

ITEM_RETENTION_DAYS = 30
RUN_RETENTION_DAYS = 90

_SCHEMA = """
CREATE TABLE IF NOT EXISTS brief_items (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    title TEXT,
    status TEXT,
    priority TEXT,
    starts_at TEXT,
    ends_at TEXT,
    due_at TEXT,
    updated_at TEXT,
    url TEXT,
    mine INTEGER DEFAULT 1,
    extra TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    prev_status TEXT,
    status_changed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_brief_items_kind_due ON brief_items(kind, due_at);
CREATE INDEX IF NOT EXISTS idx_brief_items_source_seen ON brief_items(source, last_seen_at);
CREATE TABLE IF NOT EXISTS brief_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    run_at TEXT NOT NULL,
    kind TEXT,
    sources TEXT,
    item_counts TEXT,
    session_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_brief_runs_job ON brief_runs(job_id, run_at);
"""


def _default_db_path() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "state.db"


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


class BriefStore:
    """Thin sqlite wrapper; every method is safe to call from a worker thread."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else _default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(_SCHEMA)
        try:
            self.prune()
        except Exception as exc:  # pragma: no cover - best effort
            logger.debug("brief_store prune failed: %s", exc)

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # items
    # ------------------------------------------------------------------
    def existing_ids(self, ids: Iterable[str]) -> set:
        ids = list(dict.fromkeys(ids))
        found: set = set()
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            marks = ",".join("?" for _ in chunk)
            rows = self._conn.execute(f"SELECT id FROM brief_items WHERE id IN ({marks})", chunk).fetchall()
            found.update(r["id"] for r in rows)
        return found

    def upsert_items(self, items: Sequence[BriefItem], seen_at: datetime) -> Dict[str, Any]:
        """Insert or update ``items``; returns ``{"new": [...], "changed": [(item, prev_status)]}``."""
        now = _iso(seen_at)
        new: List[BriefItem] = []
        changed: List[tuple] = []
        with self._conn:
            for item in items:
                row = self._conn.execute(
                    "SELECT status, first_seen_at, prev_status, status_changed_at FROM brief_items WHERE id = ?",
                    (item.id,),
                ).fetchone()
                prev_status = row["prev_status"] if row else None
                status_changed_at = row["status_changed_at"] if row else None
                if row is None:
                    new.append(item)
                    first_seen = now
                else:
                    first_seen = row["first_seen_at"]
                    if (row["status"] or "") != (item.status or ""):
                        prev_status = row["status"]
                        status_changed_at = now
                        changed.append((item, row["status"]))
                self._conn.execute(
                    """INSERT INTO brief_items (id, source, kind, key, title, status, priority, starts_at, ends_at,
                       due_at, updated_at, url, mine, extra, first_seen_at, last_seen_at, prev_status, status_changed_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET
                         title=excluded.title, status=excluded.status, priority=excluded.priority,
                         starts_at=excluded.starts_at, ends_at=excluded.ends_at, due_at=excluded.due_at,
                         updated_at=excluded.updated_at, url=excluded.url, mine=excluded.mine, extra=excluded.extra,
                         last_seen_at=excluded.last_seen_at, prev_status=excluded.prev_status,
                         status_changed_at=excluded.status_changed_at""",
                    (
                        item.id, item.source, item.kind, item.key, item.title, item.status, item.priority,
                        item.starts_at, item.ends_at, item.due_at, item.updated_at, item.url, 1 if item.mine else 0,
                        json.dumps(item.extra or {}, ensure_ascii=False, default=str), first_seen, now,
                        prev_status, status_changed_at,
                    ),
                )
        return {"new": new, "changed": changed}

    def items(self, *, source: Optional[str] = None, kind: Optional[str] = None,
              seen_since: Optional[datetime] = None, limit: int = 500) -> List[BriefItem]:
        sql = "SELECT * FROM brief_items WHERE 1=1"
        params: List[Any] = []
        if source:
            sql += " AND source = ?"
            params.append(source)
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        if seen_since:
            sql += " AND last_seen_at >= ?"
            params.append(_iso(seen_since))
        sql += " ORDER BY COALESCE(due_at, starts_at, updated_at) LIMIT ?"
        params.append(int(limit))
        return [self._row_to_item(r) for r in self._conn.execute(sql, params).fetchall()]

    def changed_since(self, since: datetime, *, limit: int = 20) -> List[BriefItem]:
        rows = self._conn.execute(
            "SELECT * FROM brief_items WHERE status_changed_at IS NOT NULL AND status_changed_at >= ? "
            "ORDER BY status_changed_at DESC LIMIT ?",
            (_iso(since), int(limit)),
        ).fetchall()
        return [self._row_to_item(r) for r in rows]

    def open_ticket_keys(self, source: str, *, limit: int = 10) -> List[str]:
        """Keys of tickets last seen as not-done — the bounded status-refresh set."""
        rows = self._conn.execute(
            "SELECT key, extra FROM brief_items WHERE source = ? AND kind = 'ticket' "
            "ORDER BY last_seen_at DESC LIMIT 200",
            (source,),
        ).fetchall()
        keys: List[str] = []
        for r in rows:
            try:
                extra = json.loads(r["extra"] or "{}")
            except Exception:
                extra = {}
            if str(extra.get("category", "")).lower() == "done":
                continue
            keys.append(r["key"])
            if len(keys) >= limit:
                break
        return keys

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> BriefItem:
        try:
            extra = json.loads(row["extra"] or "{}")
        except Exception:
            extra = {}
        item = BriefItem(
            source=row["source"], kind=row["kind"], key=row["key"], title=row["title"] or "",
            status=row["status"] or "", priority=row["priority"] or "", starts_at=row["starts_at"] or "",
            ends_at=row["ends_at"] or "", due_at=row["due_at"] or "", updated_at=row["updated_at"] or "",
            url=row["url"] or "", mine=bool(row["mine"]), extra=extra,
        )
        item.prev_status = row["prev_status"]
        item.status_changed_at = row["status_changed_at"]
        item.first_seen_at = row["first_seen_at"]
        return item

    # ------------------------------------------------------------------
    # runs / calendar / prune
    # ------------------------------------------------------------------
    def record_run(self, job_id: str, kind: str, sources_line: str, counts: Dict[str, int],
                   run_at: datetime, session_id: str = "") -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO brief_runs (job_id, run_at, kind, sources, item_counts, session_id) VALUES (?,?,?,?,?,?)",
                (job_id, _iso(run_at), kind, sources_line, json.dumps(counts), session_id),
            )

    def last_run_at(self, job_id: str) -> Optional[datetime]:
        row = self._conn.execute(
            "SELECT run_at FROM brief_runs WHERE job_id = ? ORDER BY run_at DESC LIMIT 1", (job_id,)
        ).fetchone()
        if not row:
            return None
        try:
            return datetime.fromisoformat(row["run_at"])
        except Exception:
            return None

    def target_hours(self, day: str) -> Optional[float]:
        """Target hours for ``YYYY-MM-DD`` from the materialised workday calendar, if present."""
        try:
            row = self._conn.execute(
                "SELECT target_hours FROM workday_calendar WHERE day = ?", (day,)
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        return float(row["target_hours"]) if row and row["target_hours"] is not None else None

    def prune(self, now: Optional[datetime] = None) -> int:
        now = now or datetime.now(timezone.utc)
        with self._conn:
            a = self._conn.execute(
                "DELETE FROM brief_items WHERE last_seen_at < ?",
                (_iso(now - timedelta(days=ITEM_RETENTION_DAYS)),),
            ).rowcount
            b = self._conn.execute(
                "DELETE FROM brief_runs WHERE run_at < ?",
                (_iso(now - timedelta(days=RUN_RETENTION_DAYS)),),
            ).rowcount
        return int(a or 0) + int(b or 0)
