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


def _default_db_path() -> Path:
    """~/.hermes/state.db resolved at call time — the import-time constant
    ignored a HERMES_HOME set later (every test), so tests wrote synthetic
    worklogs into the developer's real database."""
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "state.db"


def get_db_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Get a connection to SQLite database, ensuring tables exist."""
    path = db_path or _default_db_path()
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


# Capacity policy (AIS-275). The old 5000-row global cap saturated in
# production: every ingest silently evicted the oldest rows, and because a
# re-fetch bumps its own rows to "newest", it pushed UNRELATED worklogs out —
# aggregate SQL over old ranges went silently incomplete. Raised global cap
# plus a per-tool cap so one chatty tool cannot starve the others; both (and
# the TTL) are overridable via config `ingest.*`.
DEFAULT_MCP_RECORDS_TTL_DAYS = 14
DEFAULT_MCP_RECORDS_MAX_ROWS = 20000
DEFAULT_MCP_RECORDS_PER_TOOL_MAX_ROWS = 6000


def _ingest_limits() -> tuple:
    ttl = DEFAULT_MCP_RECORDS_TTL_DAYS
    max_rows = DEFAULT_MCP_RECORDS_MAX_ROWS
    per_tool = DEFAULT_MCP_RECORDS_PER_TOOL_MAX_ROWS
    try:
        from hermes_cli.config import load_config

        cfg = (load_config() or {}).get("ingest") or {}
        ttl = int(cfg.get("mcp_records_ttl_days") or ttl)
        max_rows = int(cfg.get("mcp_records_max_rows") or max_rows)
        per_tool = int(cfg.get("mcp_records_per_tool_max_rows") or per_tool)
    except Exception:
        pass
    return ttl, max_rows, per_tool


class PruneResult(int):
    """int-compatible prune outcome: value = TTL-deleted rows (the historic
    return), plus the cap-eviction count that used to happen silently."""

    cap_evicted: int = 0

    def __new__(cls, ttl_deleted: int = 0, cap_evicted: int = 0):
        obj = super().__new__(cls, ttl_deleted)
        obj.cap_evicted = cap_evicted
        return obj


def prune_mcp_records(
    conn: Optional[sqlite3.Connection] = None,
    older_than_days: Optional[int] = None,
    max_records: Optional[int] = None,
    per_tool_max_records: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> PruneResult:
    """Prune old auto-ingested records to prevent unbounded growth of state.db.

    ``None`` limits resolve from config (``ingest.mcp_records_*``) with the
    module defaults as fallback. Cap evictions are counted and logged — they
    remove rows that were NOT re-fetched, so downstream sums can silently
    lose data (AIS-275).
    """
    cfg_ttl, cfg_max, cfg_per_tool = _ingest_limits()
    older_than_days = cfg_ttl if older_than_days is None else older_than_days
    max_records = cfg_max if max_records is None else max_records
    per_tool_max_records = cfg_per_tool if per_tool_max_records is None else per_tool_max_records
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
            evicted = 0
            cursor = conn.execute(
                """
                DELETE FROM mcp_records WHERE id IN (
                    SELECT id FROM (
                        SELECT id, ROW_NUMBER() OVER (
                            PARTITION BY tool_name ORDER BY created_at DESC, id DESC
                        ) AS rn FROM mcp_records
                    ) WHERE rn > ?
                )
                """,
                (per_tool_max_records,),
            )
            evicted += cursor.rowcount or 0
            cursor = conn.execute(
                """
                DELETE FROM mcp_records WHERE id NOT IN (
                    SELECT id FROM mcp_records ORDER BY created_at DESC LIMIT ?
                )
                """,
                (max_records,),
            )
            evicted += cursor.rowcount or 0
            if evicted:
                logger.warning(
                    "mcp_records at capacity: evicted %d rows beyond the caps "
                    "(per-tool %d / global %d) — aggregate sums over old ranges "
                    "may be incomplete until those ranges are re-fetched",
                    evicted, per_tool_max_records, max_records,
                )
            cleanup_scratch_tables(conn)
            return PruneResult(deleted, evicted)
    except Exception as exc:
        logger.debug("Prune mcp_records error: %s", exc)
        return PruneResult(0, 0)
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


def _normalize_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _pick(norm: Dict[str, Any], *names: str) -> Any:
    """First present, non-empty value among the normalized key names."""
    for name in names:
        value = norm.get(name)
        if value is None or value == "" or value == {} or value == []:
            continue
        return value
    return None


def _extract_fields(item: Dict[str, Any], tool_name: str, tool_use_id: str, fallback_ref: str = "") -> Tuple:
    """Extract structured fields from a single item dict.

    Keys are matched case- and separator-insensitively: JSON servers send
    ``issueKey``/``timeSpentSeconds``, Jira REST ``started``, and the
    delimited-text servers (TempoMCP) ``IssueKey``/``Date``/``Hours``/
    ``TempoWorklogId``. Before this, the Tempo rows landed with no key, no
    date, no duration and a fresh UUID per call — SQL had nothing to sum.
    """
    norm: Dict[str, Any] = {}
    for key, value in item.items():
        norm.setdefault(_normalize_key(key), value)

    record_id = _pick(norm, "id", "tempoworklogid", "worklogid", "key", "caseid") or uuid.uuid4().hex

    # Reference Key (issue key, ticket key, case ID, etc.) — for per-issue
    # tools (jira_get_worklog) the key is only in the request, not the reply.
    issue = norm.get("issue")
    ref_key = (
        _pick(norm, "issuekey", "key", "ticketid", "caseid")
        or (issue if isinstance(issue, str) else (issue or {}).get("key") if isinstance(issue, dict) else None)
        or fallback_ref
        or ""
    )

    # Timestamp — a bare date plus a start time is joined into one value.
    timestamp = _pick(norm, "started", "startdate", "createdat", "created", "date", "updatedat") or ""
    start_time = _pick(norm, "starttime")
    if timestamp and start_time and isinstance(timestamp, str) and isinstance(start_time, str) \
            and re.fullmatch(r"\d{4}-\d{2}-\d{2}", timestamp.strip()) and re.fullmatch(r"\d{2}:\d{2}(:\d{2})?", start_time.strip()):
        timestamp = f"{timestamp.strip()}T{start_time.strip()}"

    # User / Author
    author = _pick(norm, "author", "user", "assignee", "worker", "authoraccountid", "username")
    if isinstance(author, dict):
        user_id = author.get("displayName") or author.get("name") or author.get("emailAddress") or ""
    else:
        user_id = str(author or "")

    # Duration in seconds (camelCase, snake_case, "1h 30m", or decimal hours)
    duration_value = _pick(norm, "timespentseconds", "durationseconds", "seconds", "billableseconds", "duration", "timespent")
    if duration_value is not None:
        duration = _parse_duration(duration_value)
    else:
        hours = _pick(norm, "hours", "timespenthours")
        try:
            duration = int(round(float(str(hours).replace(",", ".")) * 3600)) if hours is not None else 0
        except ValueError:
            duration = _parse_duration(hours)

    # Category / Type / Status
    category = _pick(norm, "category", "type", "status", "casestatus") or "default"
    if isinstance(category, dict):
        category = category.get("name") or category.get("value") or "default"

    # Comment / Description / Summary
    comment = _pick(norm, "comment", "summary", "description") or ""

    raw_data = json.dumps(item, ensure_ascii=False)

    return (
        str(record_id),
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


_NON_DATA_TOOL_MARKERS = ("memory", "_skill", "skill_", "kb_", "web_search", "web_fetch", "list_resources", "read_resource", "list_prompts", "get_prompt", "workdays")
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


_DATE_WINDOW_KEY_PAIRS = (
    ("startdate", "enddate"),
    ("datefrom", "dateto"),
    ("from", "to"),
    ("start", "end"),
)
_ISO_DAY_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def _date_window_from_args(tool_args: Any) -> Optional[tuple]:
    """The requested date window (start_day, end_day) from the tool call args.

    Recognizes startDate/endDate, dateFrom/dateTo, from/to, start/end in any
    casing/underscore style; values must begin with an ISO day (datetimes are
    truncated). Returns None unless both ends parse and start <= end — a
    fetch is only treated as window-authoritative when the window is explicit
    and sane (AIS-275).
    """
    if not isinstance(tool_args, dict):
        return None
    norm = {
        str(k).lower().replace("_", "").replace("-", ""): v
        for k, v in tool_args.items()
    }
    for start_key, end_key in _DATE_WINDOW_KEY_PAIRS:
        raw_start, raw_end = norm.get(start_key), norm.get(end_key)
        if raw_start is None or raw_end is None:
            continue
        m_start = _ISO_DAY_RE.match(str(raw_start).strip())
        m_end = _ISO_DAY_RE.match(str(raw_end).strip())
        if not (m_start and m_end):
            continue
        start, end = m_start.group(1), m_end.group(1)
        if start <= end:
            return (start, end)
    return None


class IngestResult(int):
    """int-compatible ingest outcome: value = rows ingested (the historic
    return), plus window-replacement and cap-eviction metadata (AIS-275)."""

    replaced: int = 0
    evicted: int = 0
    window: Optional[tuple] = None

    def __new__(cls, ingested: int = 0, replaced: int = 0, evicted: int = 0,
                window: Optional[tuple] = None):
        obj = super().__new__(cls, ingested)
        obj.replaced = replaced
        obj.evicted = evicted
        obj.window = window
        return obj


def _isolate_json_document(text: str) -> str:
    """The JSON document inside a tool result that may carry a preamble
    ("The following content was retrieved from an external source…") and a
    trailing note ("[Auto-ingested N records …]") — as stored transcripts do.
    Returns "" when no JSON document starts at a line boundary."""
    if text.startswith("{") or text.startswith("["):
        body = text
    else:
        match = re.search(r"(?m)^[\[{]", text)
        if not match:
            return ""
        body = text[match.start():]
    closer = "}" if body[0] == "{" else "]"
    end = body.rfind(closer)
    return body[: end + 1] if end != -1 else body


def try_auto_ingest_json(
    content: str,
    tool_name: str = "mcp",
    tool_use_id: str = "",
    db_path: Optional[Path] = None,
    tool_args: Optional[Dict[str, Any]] = None,
) -> IngestResult:
    """Attempt to parse content as JSON and ingest into SQLite mcp_records table.

    Returns an int-compatible :class:`IngestResult` — its value is the number
    of records ingested (0 if content is not JSON or has no items).

    Window-authoritative ingest (AIS-275): when the tool args carry an
    explicit date window AND the payload parsed into >0 records, the rows of
    the SAME tool inside that window are deleted first — the fetch replaces
    its window, so upstream deletions/moves (a Tempo "move" is delete + new
    id) no longer leave stale rows behind. A parsed-but-empty response
    deliberately deletes NOTHING: 0 extracted items is indistinguishable from
    an unrecognized payload shape, and wiping on ambiguity is the worse
    failure (repair path: DELETE via the sql tool, then re-fetch).
    """
    if not content or not isinstance(content, str):
        return IngestResult(0)
    if not should_ingest_tool(tool_name):
        return IngestResult(0)

    content_strip = content.strip()

    # Unwrap untrusted tool result XML wrappers if present
    if "<untrusted_tool_result" in content_strip:
        start_idx = content_strip.find(">")
        end_idx = content_strip.rfind("</untrusted_tool_result>")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            content_strip = content_strip[start_idx + 1 : end_idx].strip()

    content_strip = _isolate_json_document(content_strip)
    if not content_strip:
        return IngestResult(0)

    try:
        data = json.loads(content_strip)
    except Exception:
        return IngestResult(0)

    if _is_error_payload(data):
        return IngestResult(0)

    items = _flatten_nested_worklogs(_extract_items(data))
    fallback_ref = _reference_key_from_args(tool_args)
    if not items:
        return IngestResult(0)

    records = [_extract_fields(item, tool_name, tool_use_id, fallback_ref) for item in items]
    window = _date_window_from_args(tool_args)

    try:
        conn = get_db_connection(db_path)
        replaced = 0
        with conn:
            if window is not None:
                # This fetch is authoritative for its requested window: drop
                # the same tool's rows in that range first so stale entries
                # (moved/deleted upstream) do not survive the re-fetch.
                cursor = conn.execute(
                    "DELETE FROM mcp_records WHERE tool_name = ? "
                    "AND substr(timestamp, 1, 10) BETWEEN ? AND ?",
                    (tool_name, window[0], window[1]),
                )
                replaced = cursor.rowcount or 0
            conn.executemany("""
            INSERT OR REPLACE INTO mcp_records (
                id, tool_name, tool_use_id, reference_key, timestamp, user_id,
                duration_seconds, category, comment, raw_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, records)
            prune_result = prune_mcp_records(conn)
        logger.info(
            "Auto-ingested %d MCP records into mcp_records (tool: %s%s)",
            len(records), tool_name,
            f", replaced {replaced} rows in window {window[0]}..{window[1]}" if window else "",
        )
        return IngestResult(
            len(records), replaced=replaced,
            evicted=getattr(prune_result, "cap_evicted", 0), window=window,
        )
    except Exception as exc:
        logger.warning("Failed to store MCP records in SQLite: %s", exc)
        return IngestResult(0)
