"""MCP JSON Ingestor -- Automatically ingests structured JSON payloads from MCP tools
(Jira worklogs, Tempo entries, OpenProject tickets, support cases, API lists)
into local SQLite table 'mcp_records' in ~/.hermes/state.db.
"""

import json
import re
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


_DELIMITED_MIN_PAIRS = 2
_DELIMITED_MIN_HIT_RATIO = 0.6


def _parse_delimited_lines(text: str) -> List[Dict[str, Any]]:
    """Turn a `key: value | key: value` line dump into one dict per line.

    Several MCP servers answer with human-readable text rather than JSON —
    TempoMCP returns worklogs as
    ``TempoWorklogId: 43011 | IssueKey: IAMDS-595 | Date: 2026-01-02 | Hours: 1.00``,
    one per line. `json.loads` fails on that, so the whole payload used to land
    in a single `mcp_records` row as one opaque blob: 96k characters that SQL
    cannot aggregate. Agents then reached for throwaway Python to parse it,
    which is exactly what `mcp_records` exists to avoid.

    Only accepted when most non-empty lines actually look like this, so prose
    and stack traces are not shredded into nonsense rows.
    """
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return []

    parsed: List[Dict[str, Any]] = []
    for line in lines:
        segments = [seg.strip() for seg in line.split("|")]
        pairs: Dict[str, Any] = {}
        for segment in segments:
            key, sep, value = segment.partition(":")
            key = key.strip()
            if not sep or not key or " " in key:
                pairs = {}
                break
            pairs[key] = value.strip()
        if len(pairs) >= _DELIMITED_MIN_PAIRS:
            parsed.append(pairs)

    if len(parsed) / len(lines) < _DELIMITED_MIN_HIT_RATIO:
        return []

    return parsed


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
                # Not JSON — many servers answer in delimited plain text.
                delimited = _parse_delimited_lines(res)
                if delimited:
                    return delimited
        elif isinstance(res, (list, dict)):
            return _extract_items(res)

        # Fallback to single item dict if it has identifiable fields
        return [data]

    return []


def _parse_duration(value: Any) -> int:
    """Seconds from an int, a numeric string, or a Jira-style "1h 30m"."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").strip().lower()
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        pass
    total = 0
    for amount, unit in re.findall(r"(\d+(?:[.,]\d+)?)\s*([wdhm])", text):
        amount = float(amount.replace(",", "."))
        total += amount * {"w": 5 * 8 * 3600, "d": 8 * 3600, "h": 3600, "m": 60}[unit]
    return int(total)


def _extract_fields(item: Dict[str, Any], tool_name: str, tool_use_id: str, fallback_ref: str = "") -> Tuple:
    """Extract structured fields from a single item dict."""
    record_id = str(item.get("id") or item.get("key") or item.get("case_id") or uuid.uuid4().hex)

    # Reference Key (issue key, ticket key, case ID, etc.) — for per-issue
    # tools (jira_get_worklog) the key is only in the request, not the reply.
    ref_key = (
        item.get("issueKey")
        or item.get("issue_key")
        or item.get("key")
        or item.get("ticket_id")
        or item.get("case_id")
        or (item.get("issue") if isinstance(item.get("issue"), str) else (item.get("issue") or {}).get("key"))
        or fallback_ref
        or ""
    )

    # Timestamp
    timestamp = (
        item.get("started")
        or item.get("startDate")
        or item.get("start_date")
        or item.get("created_at")
        or item.get("created")
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

    # Duration in seconds (camelCase, snake_case, or "1h 30m")
    duration = _parse_duration(
        item.get("timeSpentSeconds")
        or item.get("time_spent_seconds")
        or item.get("duration_seconds")
        or item.get("seconds")
        or item.get("duration")
        or item.get("timeSpent")
        or item.get("time_spent")
        or 0
    )

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


_NON_DATA_TOOL_MARKERS = ("memory", "_skill", "skill_", "kb_", "web_search", "web_fetch", "list_resources", "read_resource", "list_prompts", "get_prompt")
_BRIDGE_TOOLS = frozenset({"tool_search", "tool_describe", "tool_call"})


def should_ingest_tool(tool_name: str) -> bool:
    """Only results of *data* tools belong in mcp_records.

    The bridge tools (tool_search/describe/call), the core tools (sql, file
    and terminal tools, todo …), memory/skill/knowledge-base tools and the
    MCP resource/prompt utilities return JSON too; ingesting them produced one
    junk row per call ("Auto-ingested 1 records" on every tool_search, on every
    memory_context) and polluted the table the sql tool aggregates.
    """
    name = str(tool_name or "")
    if not name or name in _BRIDGE_TOOLS:
        return False
    try:
        from toolsets import _HERMES_CORE_TOOLS

        if name in _HERMES_CORE_TOOLS:
            return False
    except Exception:
        pass
    lowered = name.lower()
    return not any(marker in lowered for marker in _NON_DATA_TOOL_MARKERS)


def _is_error_payload(data: Any) -> bool:
    """`{"error": …}` (optionally wrapped in {"result": …}) is not a record."""
    if not isinstance(data, dict):
        return False
    if data.get("error") and not any(isinstance(data.get(k), list) for k in ("worklogs", "issues", "items", "results")):
        return True
    res = data.get("result")
    if isinstance(res, str):
        stripped = res.strip()
        if stripped.startswith("{"):
            try:
                return _is_error_payload(json.loads(stripped))
            except Exception:
                return False
        return False
    return _is_error_payload(res) if isinstance(res, dict) else False


_NESTED_WORKLOG_KEYS = ("worklog", "worklogs")


def _flatten_nested_worklogs(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Issues that carry their worklogs (jira_search with fields=worklog) become
    one record per worklog, tagged with the issue key — the query the user
    actually wants (`SUM(duration_seconds) GROUP BY reference_key`) needs the
    booking rows, not the issue container with 0 hours."""
    out: List[Dict[str, Any]] = []
    for item in items:
        out.append(item)
        issue_key = item.get("key") or item.get("issueKey") or ""
        containers = [item, item.get("fields") if isinstance(item.get("fields"), dict) else {}]
        for container in containers:
            wl = container.get("worklog")
            wl_list = wl.get("worklogs") if isinstance(wl, dict) else (wl if isinstance(wl, list) else None)
            if not wl_list and isinstance(container.get("worklogs"), list) and container is not item:
                wl_list = container.get("worklogs")
            if not isinstance(wl_list, list):
                continue
            for entry in wl_list:
                if not isinstance(entry, dict):
                    continue
                row = dict(entry)
                row.setdefault("issueKey", issue_key)
                row.setdefault("type", "worklog")
                out.append(row)
    return out


def _reference_key_from_args(tool_args: Any) -> str:
    if not isinstance(tool_args, dict):
        return ""
    for key in ("issue_key", "issueKey", "issue", "key", "ticket", "ticket_id", "case_id"):
        val = tool_args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def try_auto_ingest_json(
    content: str,
    tool_name: str = "mcp",
    tool_use_id: str = "",
    db_path: Optional[Path] = None,
    tool_args: Optional[Dict[str, Any]] = None,
) -> int:
    """Attempt to parse content as JSON and ingest into SQLite mcp_records table.

    Returns the number of records ingested (0 if content is not JSON or has no items).
    """
    if not content or not isinstance(content, str):
        return 0
    if not should_ingest_tool(tool_name):
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

    if _is_error_payload(data):
        return 0

    items = _flatten_nested_worklogs(_extract_items(data))
    fallback_ref = _reference_key_from_args(tool_args)
    if not items:
        return 0

    records = [_extract_fields(item, tool_name, tool_use_id, fallback_ref) for item in items]

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
