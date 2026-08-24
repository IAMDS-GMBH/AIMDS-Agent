"""MCP JSON Ingestor -- Automatically ingests structured JSON payloads from MCP tools
(Jira worklogs, Tempo entries, OpenProject tickets, support cases, API lists)
into local SQLite table 'mcp_records' in ~/.hermes/state.db.
"""

import json
import logging
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from hermes_state import DEFAULT_DB_PATH

logger = logging.getLogger(__name__)


def get_db_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Get a connection to SQLite database, ensuring tables exist."""
    path = db_path or DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    init_mcp_tables(conn)
    return conn


def cleanup_scratch_tables(conn: sqlite3.Connection) -> int:
    """Drop lingering temporary or scratch tables created in state.db."""
    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE 'temp_%' OR name LIKE 'tmp_%' OR name LIKE 'scratch_%')"
        )
        system_tables = {
            "sessions", "messages", "schema_version", "state_meta", "mcp_records",
            "compression_locks", "todos", "inbox_entries"
        }
        tables = [row[0] for row in cursor.fetchall() if row[0].lower() not in system_tables]
        dropped = 0
        for table in tables:
            conn.execute(f"DROP TABLE IF EXISTS [{table}]")
            dropped += 1
        return dropped
    except Exception as exc:
        logger.debug("Cleanup scratch tables error: %s", exc)
        return 0


def prune_mcp_records(
    conn: Optional[sqlite3.Connection] = None,
    older_than_days: int = 14,
    max_records: int = 5000,
    db_path: Optional[Path] = None,
) -> int:
    """Prune old auto-ingested records to prevent unbounded growth of state.db."""
    should_close = False
    if conn is None:
        conn = get_db_connection(db_path)
        should_close = True
    try:
        with conn:
            cursor = conn.execute(
                "DELETE FROM mcp_records WHERE created_at < datetime('now', ?)",
                (f"-{older_than_days} days",),
            )
            deleted = cursor.rowcount or 0
            conn.execute(
                """
                DELETE FROM mcp_records WHERE id NOT IN (
                    SELECT id FROM mcp_records ORDER BY created_at DESC LIMIT ?
                )
                """,
                (max_records,),
            )
            cleanup_scratch_tables(conn)
            return deleted
    except Exception as exc:
        logger.debug("Prune mcp_records error: %s", exc)
        return 0
    finally:
        if should_close:
            conn.close()


def init_mcp_tables(conn: sqlite3.Connection) -> None:
    """Initialize the mcp_records schema in SQLite."""
    with conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS mcp_records (
            id TEXT PRIMARY KEY,
            tool_name TEXT,
            tool_use_id TEXT,
            reference_key TEXT,
            timestamp TEXT,
            user_id TEXT,
            duration_seconds INTEGER DEFAULT 0,
            category TEXT,
            comment TEXT,
            raw_data TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mcp_records_ref ON mcp_records(reference_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mcp_records_tool ON mcp_records(tool_name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mcp_records_ts ON mcp_records(timestamp)")
    try:
        prune_mcp_records(conn)
    except Exception:
        pass


def _extract_items(data: Any) -> List[Dict[str, Any]]:
    """Extract a list of item dictionaries from arbitrary JSON input."""
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]

    if isinstance(data, dict):
        # Check common collection keys
        for key in ("worklogs", "issues", "tickets", "entries", "records", "items", "data", "results", "values", "cases"):
            val = data.get(key)
            if isinstance(val, list):
                return [item for item in val if isinstance(item, dict)]

        # Check nested result key (e.g. {"result": "{...}"} or {"result": [...]})
        res = data.get("result")
        if isinstance(res, str):
            try:
                parsed_res = json.loads(res)
                return _extract_items(parsed_res)
            except Exception:
                pass
        elif isinstance(res, (list, dict)):
            return _extract_items(res)

        # Fallback to single item dict if it has identifiable fields
        return [data]

    return []


def _extract_fields(item: Dict[str, Any], tool_name: str, tool_use_id: str) -> Tuple:
    """Extract structured fields from a single item dict."""
    record_id = str(item.get("id") or item.get("key") or item.get("case_id") or uuid.uuid4().hex)

    # Reference Key (issue key, ticket key, case ID, etc.)
    ref_key = (
        item.get("issueKey")
        or item.get("key")
        or item.get("ticket_id")
        or item.get("case_id")
        or (item.get("issue") if isinstance(item.get("issue"), str) else (item.get("issue") or {}).get("key"))
        or ""
    )

    # Timestamp
    timestamp = (
        item.get("started")
        or item.get("created_at")
        or item.get("date")
        or item.get("createdAt")
        or item.get("updated_at")
        or ""
    )

    # User / Author
    author = item.get("author") or item.get("user") or item.get("assignee")
    if isinstance(author, dict):
        user_id = author.get("displayName") or author.get("name") or author.get("emailAddress") or ""
    else:
        user_id = str(author or "")

    # Duration in seconds
    duration = item.get("timeSpentSeconds") or item.get("duration_seconds") or item.get("seconds") or item.get("duration") or 0
    if not isinstance(duration, (int, float)):
        try:
            duration = int(duration)
        except (ValueError, TypeError):
            duration = 0

    # Category / Type / Status
    category = item.get("category") or item.get("type") or item.get("status") or item.get("case_status") or "default"
    if isinstance(category, dict):
        category = category.get("name") or category.get("value") or "default"

    # Comment / Description / Summary
    comment = item.get("comment") or item.get("summary") or item.get("description") or ""

    raw_data = json.dumps(item, ensure_ascii=False)

    return (
        record_id,
        tool_name,
        tool_use_id,
        str(ref_key),
        str(timestamp),
        str(user_id),
        int(duration),
        str(category),
        str(comment),
        raw_data,
    )


def try_auto_ingest_json(
    content: str,
    tool_name: str = "mcp",
    tool_use_id: str = "",
    db_path: Optional[Path] = None,
) -> int:
    """Attempt to parse content as JSON and ingest into SQLite mcp_records table.

    Returns the number of records ingested (0 if content is not JSON or has no items).
    """
    if not content or not isinstance(content, str):
        return 0

    content_strip = content.strip()

    # Unwrap untrusted tool result XML wrappers if present
    if "<untrusted_tool_result" in content_strip:
        start_idx = content_strip.find(">")
        end_idx = content_strip.rfind("</untrusted_tool_result>")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            content_strip = content_strip[start_idx + 1 : end_idx].strip()

    if not (content_strip.startswith("{") or content_strip.startswith("[")):
        return 0

    try:
        data = json.loads(content_strip)
    except Exception:
        return 0

    items = _extract_items(data)
    if not items:
        return 0

    records = [_extract_fields(item, tool_name, tool_use_id) for item in items]

    try:
        conn = get_db_connection(db_path)
        with conn:
            conn.executemany("""
            INSERT OR REPLACE INTO mcp_records (
                id, tool_name, tool_use_id, reference_key, timestamp, user_id,
                duration_seconds, category, comment, raw_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, records)
            prune_mcp_records(conn)
        logger.info("Auto-ingested %d MCP records into mcp_records (tool: %s)", len(records), tool_name)
        return len(records)
    except Exception as exc:
        logger.warning("Failed to store MCP records in SQLite: %s", exc)
        return 0
