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

    path = db_path or DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        conn = sqlite3.connect(str(path), timeout=10.0)
        init_mcp_tables(conn)

        query_clean = query.strip()
        is_select = query_clean.upper().startswith("SELECT") or query_clean.upper().startswith("PRAGMA") or query_clean.upper().startswith("EXPLAIN") or query_clean.upper().startswith("WITH")

        cursor = conn.cursor()
        if is_select:
            cursor.execute(query_clean)
            col_names = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = cursor.fetchmany(MAX_SQL_ROWS)
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
        else:
            with conn:
                cursor.execute(query_clean)
                affected = cursor.rowcount
            return json.dumps({"status": "success", "rows_affected": affected}, ensure_ascii=False)

    except Exception as exc:
        logger.debug("SQL execution failed: %s", exc)
        return tool_error(f"SQLite error: {exc}")


def check_sql_requirements() -> bool:
    """SQL tool uses Python's built-in sqlite3 -- always available."""
    return True


SQL_SCHEMA = {
    "name": "sql",
    "description": (
        "Execute SQL queries directly against the local Hermes SQLite state database (~/.hermes/state.db).\n"
        "Use this for fast, 100% accurate queries, aggregations, and reports on 'mcp_records' "
        "(auto-ingested Jira/Tempo worklogs, project tickets, support cases) and local session history.\n\n"
        "Available tables:\n"
        "- mcp_records: (id, tool_name, reference_key, timestamp, user_id, duration_seconds, category, comment, raw_data)\n"
        "- sessions: (id, source, user_id, model, started_at, title, message_count)\n"
        "- messages: (id, session_id, role, content, tool_name, created_at)\n"
        "- todos: (id, content, status, created_at)\n"
        "- inbox_entries: (id, source, title, content, created_at)\n\n"
        "Supports all SQLite syntax: SELECT, INSERT, UPDATE, DELETE, WITH, CREATE TABLE, etc."
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


registry.register(
    name="sql",
    toolset="hermes_core",
    schema=SQL_SCHEMA,
    handler=lambda args, **kw: execute_sql(args.get("query", "")),
    check_fn=check_sql_requirements,
    emoji="📊",
)
