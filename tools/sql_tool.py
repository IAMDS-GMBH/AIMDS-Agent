#!/usr/bin/env python3
"""
SQL Tool Module -- Native SQLite Query Execution

Executes SQL queries against the local Hermes SQLite database (~/.hermes/state.db).
Mainly used for querying 'mcp_records' (auto-ingested Jira/Tempo worklogs, support cases,
project tickets) and other state tables cleanly without executing terminal/bash commands.
"""

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_state import DEFAULT_DB_PATH
from tools.mcp_json_ingestor import init_mcp_tables
from tools.registry import registry, tool_error

logger = logging.getLogger(__name__)

MAX_SQL_ROWS = 200

READ_ONLY_TABLES = {
    "sessions",
    "messages",
    "schema_version",
    "state_meta",
    "compression_locks",
    "telegram_dm_topic_mode",
    "telegram_dm_topic_bindings",
}

MUTATION_OPS = {
    getattr(sqlite3, "SQLITE_INSERT", 18),
    getattr(sqlite3, "SQLITE_UPDATE", 23),
    getattr(sqlite3, "SQLITE_DELETE", 9),
    getattr(sqlite3, "SQLITE_DROP_TABLE", 11),
    getattr(sqlite3, "SQLITE_ALTER_TABLE", 26),
    getattr(sqlite3, "SQLITE_DROP_INDEX", 10),
}


def _sql_authorizer(
    action: int,
    arg1: Optional[str],
    arg2: Optional[str],
    db_name: Optional[str],
    trigger_name: Optional[str],
) -> int:
    if action in MUTATION_OPS and arg1 and arg1.lower() in READ_ONLY_TABLES:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _default_db_path() -> Path:
    """~/.hermes/state.db resolved at call time (HERMES_HOME may change after import)."""
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "state.db"


def execute_sql(
    query: str,
    db_path: Optional[Path] = None,
) -> str:
    """Execute a SQL query against the local SQLite database.

    Args:
        query: SQL string to execute (SELECT, INSERT, UPDATE, DELETE, CREATE TABLE, etc.)
        db_path: Optional override path for the database file.

    Returns:
        Formatted markdown table or JSON result.
    """
    if not query or not query.strip():
        return tool_error("Query string cannot be empty")

    path = db_path or _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        conn = sqlite3.connect(str(path), timeout=10.0)
        conn.set_authorizer(_sql_authorizer)
        init_mcp_tables(conn)

        query_clean = query.strip()

        # Whether a statement returns rows is decided by SQLite, not by the
        # first keyword: agents prefix queries with `-- comments`, wrap them
        # in CTEs, or add PRAGMA/EXPLAIN. A leading comment used to make a
        # SELECT look like a write and its rows were silently dropped
        # ({"status": "success", "rows_affected": -1}).
        cursor = conn.cursor()
        with conn:
            cursor.execute(query_clean)
            returns_rows = cursor.description is not None
            if returns_rows:
                col_names = [desc[0] for desc in cursor.description]
                rows = cursor.fetchmany(MAX_SQL_ROWS)
            else:
                affected = cursor.rowcount

        if returns_rows:
            total_fetched = len(rows)

            if not rows:
                return json.dumps({"columns": col_names, "rows": [], "count": 0}, ensure_ascii=False)

            # Format as clean markdown table
            header = "| " + " | ".join(col_names) + " |"
            separator = "| " + " | ".join(["---"] * len(col_names)) + " |"
            row_lines = []
            for row in rows:
                cells = [str(val).replace("\n", " ").replace("|", "\\|") if val is not None else "" for val in row]
                row_lines.append("| " + " | ".join(cells) + " |")

            table_md = "\n".join([header, separator] + row_lines)
            summary = f"({total_fetched} rows returned"
            if total_fetched == MAX_SQL_ROWS:
                summary += f", capped at {MAX_SQL_ROWS}"
            summary += ")"

            return f"{table_md}\n\n_{summary}_"
        return json.dumps({"status": "success", "rows_affected": affected}, ensure_ascii=False)

    except Exception as exc:
        logger.debug("SQL execution failed: %s", exc)
        if "not authorized" in str(exc).lower():
            return tool_error(f"Modification denied: System tables ({', '.join(sorted(READ_ONLY_TABLES))}) are read-only.")
        return tool_error(f"SQLite error: {exc}")


def check_sql_requirements() -> bool:
    """SQL tool uses Python's built-in sqlite3 -- always available."""
    return True


SQL_SCHEMA = {
    "name": "sql",
    "description": (
        "Execute deterministic SQL queries and mathematical calculations directly against the local SQLite database (~/.hermes/state.db).\n"
        "MANDATORY FOR ALL CALCULATIONS & AGGREGATIONS: Never perform mental arithmetic or manual calculations on budgets, logs, or numbers in text. "
        "Always use SQL queries (SUM, COUNT, AVG, ROUND, GROUP BY, math expressions, CTEs/temp tables) for 100% mathematical precision.\n\n"
        "Common Use Cases:\n"
        "1. Worklog & Time Tracking: SELECT substr(timestamp, 1, 7) AS month, reference_key, ROUND(SUM(duration_seconds)/3600.0, 2) AS hours FROM mcp_records WHERE timestamp LIKE '2026-%' GROUP BY month, reference_key;\n"
        "2. Budget & Financial Calculations: SELECT 50000 - SUM(amount) AS remaining_budget, ROUND(SUM(amount)/50000.0 * 100, 2) AS pct_used FROM ...;\n"
        "3. Arbitrary Arithmetic & Formulas: SELECT ROUND((174.5 / 160.0 - 1.0) * 100, 2) AS deviation_pct;\n\n"
        "Available tables:\n"
        "- mcp_records: (id, tool_name, reference_key, timestamp, user_id, duration_seconds, category, comment, raw_data)\n"
        "- workday_calendar: (day, month, iso_week, weekday, is_weekend, is_holiday, holiday_name, holiday_kind, factor, target_hours, reason, region, days_per_week, weekly_hours, generated_at) — written by workdays(action='materialize'/'report')\n"
        "- absences: (day, portion, kind, source, note, created_at) — vacation/sick days, filled via workdays(action='absences')\n"
        "- sessions: (id, source, user_id, model, started_at, title, message_count)\n"
        "- messages: (id, session_id, role, content, tool_name, created_at)\n"
        "- todos: (id, content, status, created_at)\n"
        "- inbox_entries: (id, source, title, content, created_at)\n\n"
        "Supports all SQLite syntax: SELECT, INSERT, UPDATE, DELETE, WITH, CREATE TABLE, TEMP tables, math functions, etc."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "SQL query string to execute against SQLite (~/.hermes/state.db)."
            }
        },
        "required": ["query"]
    }
}


def _handle_sql(args: dict, **kw) -> str:
    query = (
        args.get("query")
        or args.get("statement")
        or args.get("sql")
        or args.get("command")
        or ""
    )
    return execute_sql(query)


registry.register(
    name="sql",
    toolset="sql",
    schema=SQL_SCHEMA,
    handler=_handle_sql,
    check_fn=check_sql_requirements,
    emoji="📊",
)
