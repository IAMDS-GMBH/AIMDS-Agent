"""Persistent profile-scoped TODO storage."""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home

VALID_STATUSES = {"pending", "in_progress", "completed", "cancelled"}
DEFAULT_TODO_DAY_HOUR = 18


class PersistentTodoStore:
    """SQLite-backed todo store scoped to the active Hermes profile."""

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = Path(db_path) if db_path else (get_hermes_home() / "todos.db")
        self._lock = threading.RLock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS todos (
                        id TEXT PRIMARY KEY,
                        content TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        completed_at REAL
                    )
                    """
                )

    @staticmethod
    def _default_todo_timestamp(now_ts: Optional[float] = None) -> float:
        """
        Default todo timestamp when no explicit date is provided.

        Uses local same-day end-of-day at 18:00.
        """
        now = datetime.fromtimestamp(now_ts if now_ts is not None else time.time())
        return now.replace(hour=DEFAULT_TODO_DAY_HOUR, minute=0, second=0, microsecond=0).timestamp()

    @staticmethod
    def _normalize(item: Dict[str, Any]) -> Dict[str, str]:
        item_id = str(item.get("id", "")).strip() or "?"
        content = str(item.get("content", "")).strip() or "(no description)"
        status = str(item.get("status", "pending")).strip().lower()
        if status not in VALID_STATUSES:
            status = "pending"
        return {"id": item_id, "content": content, "status": status}

    @staticmethod
    def _dedupe_by_id(todos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        last_index: Dict[str, int] = {}
        for i, item in enumerate(todos):
            item_id = str(item.get("id", "")).strip() or "?"
            last_index[item_id] = i
        return [todos[i] for i in sorted(last_index.values())]

    def _rows(self) -> List[sqlite3.Row]:
        with self._lock:
            with self._connect() as conn:
                return list(
                    conn.execute(
                        "SELECT id, content, status, created_at, updated_at, completed_at "
                        "FROM todos ORDER BY created_at ASC, updated_at ASC, id ASC"
                    )
                )

    def read(self) -> List[Dict[str, str]]:
        return [
            {"id": str(row["id"]), "content": str(row["content"]), "status": str(row["status"])}
            for row in self._rows()
        ]

    def read_with_meta(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": str(row["id"]),
                "content": str(row["content"]),
                "status": str(row["status"]),
                "created_at": float(row["created_at"]),
                "updated_at": float(row["updated_at"]),
                "completed_at": float(row["completed_at"]) if row["completed_at"] is not None else None,
            }
            for row in self._rows()
        ]

    def has_items(self) -> bool:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute("SELECT COUNT(*) AS n FROM todos").fetchone()
                return bool(row and int(row["n"]) > 0)

    def write(self, todos: List[Dict[str, Any]], merge: bool = False) -> List[Dict[str, str]]:
        normalized = [self._normalize(t) for t in self._dedupe_by_id(todos)]
        now = self._default_todo_timestamp()
        with self._lock:
            with self._connect() as conn:
                if not merge:
                    existing_created = {
                        str(row["id"]): float(row["created_at"])
                        for row in conn.execute("SELECT id, created_at FROM todos")
                    }
                    conn.execute("DELETE FROM todos")
                    for idx, item in enumerate(normalized):
                        created = existing_created.get(item["id"], now + (idx * 1e-6))
                        completed_at = now if item["status"] == "completed" else None
                        conn.execute(
                            "INSERT INTO todos (id, content, status, created_at, updated_at, completed_at) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (item["id"], item["content"], item["status"], created, now, completed_at),
                        )
                else:
                    for idx, item in enumerate(normalized):
                        existing = conn.execute("SELECT created_at FROM todos WHERE id = ?", (item["id"],)).fetchone()
                        completed_at = now if item["status"] == "completed" else None
                        if existing:
                            conn.execute(
                                "UPDATE todos SET content = ?, status = ?, updated_at = ?, completed_at = ? WHERE id = ?",
                                (item["content"], item["status"], now, completed_at, item["id"]),
                            )
                        else:
                            conn.execute(
                                "INSERT INTO todos (id, content, status, created_at, updated_at, completed_at) "
                                "VALUES (?, ?, ?, ?, ?, ?)",
                                (item["id"], item["content"], item["status"], now + (idx * 1e-6), now, completed_at),
                            )
        return self.read()

    def set_status(self, todo_id: str, status: str) -> Optional[Dict[str, Any]]:
        status_norm = str(status).strip().lower()
        if status_norm not in VALID_STATUSES:
            return None
        now = self._default_todo_timestamp()
        completed_at = now if status_norm == "completed" else None
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE todos SET status = ?, updated_at = ?, completed_at = ? WHERE id = ?",
                    (status_norm, now, completed_at, todo_id),
                )
                row = conn.execute(
                    "SELECT id, content, status, created_at, updated_at, completed_at FROM todos WHERE id = ?",
                    (todo_id,),
                ).fetchone()
        if not row:
            return None
        return {
            "id": str(row["id"]),
            "content": str(row["content"]),
            "status": str(row["status"]),
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
            "completed_at": float(row["completed_at"]) if row["completed_at"] is not None else None,
        }

    def create(self, content: str, status: str = "pending") -> Dict[str, Any]:
        now = self._default_todo_timestamp()
        status_norm = status.strip().lower()
        if status_norm not in VALID_STATUSES:
            status_norm = "pending"
        with self._lock:
            with self._connect() as conn:
                row = conn.execute("SELECT MAX(created_at) AS max_created FROM todos").fetchone()
                max_created = float(row["max_created"]) if row and row["max_created"] is not None else None
                created_at = max(now, (max_created + 1e-6) if max_created is not None else now)
                todo_id = f"todo-{int(created_at * 1_000_000)}"
                completed_at = created_at if status_norm == "completed" else None
                conn.execute(
                    "INSERT INTO todos (id, content, status, created_at, updated_at, completed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (todo_id, content.strip() or "(no description)", status_norm, created_at, created_at, completed_at),
                )
        return self.set_status(todo_id, status_norm) or {
            "id": todo_id,
            "content": content.strip() or "(no description)",
            "status": status_norm,
            "created_at": created_at,
            "updated_at": created_at,
            "completed_at": completed_at,
        }

    def delete(self, todo_id: str) -> bool:
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
                return int(cur.rowcount or 0) > 0
