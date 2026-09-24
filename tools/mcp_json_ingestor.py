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
            "compression_locks", "todos", "inbox_entries", "brief_items", "brief_runs",
            "api_calls", "workday_calendar", "absences", "presence", "mcp_fetches"
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


def _ingest_write_through(
    data: Any,
    target_tool: str,
    tool_use_id: str,
    db_path: Optional[Path],
    tool_args: Optional[Dict[str, Any]],
) -> "IngestResult":
    """A confirmed booking lands in the listing tool's rows at once (AIS-409):
    a saved entry is upserted by id, a deleted one removed. The report that
    follows the booking sees it without a re-fetch. Only rows that look like
    bookings (date and duration) are written — an echo without them is not
    a record."""
    payload = data.get("result") if isinstance(data, dict) and isinstance(data.get("result"), dict) else data
    deleted = None
    if isinstance(payload, dict):
        deleted = next((payload.get(k) for k in _DELETED_ID_KEYS if payload.get(k) not in (None, "")), None)
    fallback_ref = _reference_key_from_args(tool_args)
    records = []
    if deleted is None:
        for item in _flatten_nested_worklogs(_extract_items(payload)):
            record = _extract_fields(item, target_tool, tool_use_id, fallback_ref)
            if record[4] and record[6] > 0:
                records.append(record)
    if deleted is None and not records:
        return IngestResult(0)
    try:
        conn = get_db_connection(db_path)
        with conn:
            if deleted is not None:
                cursor = conn.execute(
                    "DELETE FROM mcp_records WHERE id = ? AND tool_name = ?", (str(deleted), target_tool)
                )
                removed = cursor.rowcount or 0
            else:
                conn.executemany("""
                INSERT OR REPLACE INTO mcp_records (
                    id, tool_name, tool_use_id, reference_key, timestamp, user_id,
                    duration_seconds, category, comment, raw_data
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, records)
        conn.close()
    except Exception as exc:
        logger.warning("Failed to write booking through to mcp_records: %s", exc)
        return IngestResult(0)
    if deleted is not None:
        logger.info("Booking deleted: removed %d row(s) of %s (id %s)", removed, target_tool, deleted)
        return IngestResult(0, replaced=removed)
    logger.info("Booking written through: %d row(s) upserted into %s", len(records), target_tool)
    return IngestResult(len(records))


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
        # Fetch register (AIS-344): one row per fetched month (or window) —
        # which tool fetched which source for which range, how many rows,
        # and whether the fetch was complete. Consumers prove coverage
        # from it instead of assuming a range is "loaded".
        conn.execute("""
        CREATE TABLE IF NOT EXISTS mcp_fetches (
            tool_use_id TEXT NOT NULL,
            month TEXT NOT NULL,
            tool_name TEXT,
            reference_key TEXT,
            window_start TEXT,
            window_end TEXT,
            rows INTEGER DEFAULT 0,
            complete INTEGER DEFAULT 1,
            error TEXT,
            fetched_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (tool_use_id, month)
        )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mcp_fetches_tool ON mcp_fetches(tool_name, reference_key, month)")
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


def _parse_ndjson_documents(text: str) -> List[Any]:
    """One parsed JSON document per non-empty line, or ``[]`` unless every
    line parses. The month-splitting bridge joins per-month answers it could
    not merge with newlines; before AIS-354 that NDJSON failed ``json.loads``
    and a 100k-character calendar year landed as ONE blob row."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return []
    docs: List[Any] = []
    for line in lines:
        if line[:1] not in "{[":
            return []
        try:
            docs.append(json.loads(line))
        except Exception:
            return []
    return docs


# A single fallback row longer than this is reported as a blob (AIS-354).
_BLOB_ROW_CHARS = 4000


def _extract_items(data: Any) -> List[Dict[str, Any]]:
    """Extract a list of item dictionaries from arbitrary JSON input."""
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]

    if isinstance(data, dict):
        # Check common collection keys
        # ``value`` is Microsoft Graph's collection key: without it a chat or
        # drive listing was ingested as ONE blob row (AIS-289).
        # ``time_entries``/``work_packages``: the in-repo OpenProject server (AIS-408).
        for key in ("worklogs", "time_entries", "issues", "work_packages", "tickets", "entries", "records", "items", "data", "results", "values", "value", "cases", "matches"):
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
                # Several JSON documents, one per line (split-by-month join).
                ndjson_docs = _parse_ndjson_documents(res)
                if ndjson_docs:
                    merged: List[Dict[str, Any]] = []
                    for doc in ndjson_docs:
                        merged.extend(_extract_items(doc))
                    return merged
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


def _graph_datetime(value: Any) -> str:
    """``{"dateTime": "2026-08-04T10:00:00.0000000", "timeZone": …}`` → ISO text
    (fractional seconds dropped); plain strings pass through; else ``""``."""
    if isinstance(value, dict):
        value = value.get("dateTime") or value.get("datetime") or ""
    text = str(value or "").strip()
    if not text:
        return ""
    return re.sub(r"(\d{2}:\d{2}:\d{2})\.\d+", r"\1", text)


def _seconds_between(start: str, end: str) -> int:
    from datetime import datetime

    def _parse(text: str):
        text = str(text or "").strip().replace(" ", "T")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None

    a, b = _parse(start), _parse(end)
    if a is None or b is None:
        return 0
    if (a.tzinfo is None) != (b.tzinfo is None):
        a, b = a.replace(tzinfo=None), b.replace(tzinfo=None)
    seconds = int((b - a).total_seconds())
    return seconds if seconds > 0 else 0


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
    # OpenProject time entries (openproject-ce-mcp `list_time_entries`) carry
    # `work_package_id` + `spent_on` + `hours` as an ISO 8601 duration
    # (PT1H30M) — the Tempo-equivalent shape for AIS-327's time tracking parity.
    # A stable source identity (``source_key``/``calendar_key`` stamped by the
    # server: group id, mailbox, calendar id) beats display names — rows of one
    # calendar share one key however it was addressed (AIS-344).
    ref_key = (
        _pick(norm, "sourcekey", "calendarkey", "issuekey", "key", "ticketid", "caseid", "workpackageid", "calendarname")
        or (issue if isinstance(issue, str) else (issue or {}).get("key") if isinstance(issue, dict) else None)
        or fallback_ref
        or ""
    )

    # Timestamp — a bare date plus a start time is joined into one value.
    # Calendar events (Microsoft Graph) carry ``start: {dateTime, timeZone}``
    # plus the server's ``start_iso_local``; before AIS-339 they landed with
    # an empty timestamp, so no per-day JOIN was possible.
    timestamp = _pick(norm, "started", "startdate", "spenton", "startisolocal", "createdat", "created", "date", "updatedat") or ""
    event_start = _graph_datetime(norm.get("start"))
    event_end = _graph_datetime(norm.get("end")) or _pick(norm, "endisolocal")
    if event_start and not _pick(norm, "started", "startdate", "spenton", "startisolocal"):
        timestamp = event_start
    if isinstance(timestamp, dict):
        timestamp = _graph_datetime(timestamp) or ""
    start_time = _pick(norm, "starttime")
    if timestamp and start_time and isinstance(timestamp, str) and isinstance(start_time, str) \
            and re.fullmatch(r"\d{4}-\d{2}-\d{2}", timestamp.strip()) and re.fullmatch(r"\d{2}:\d{2}(:\d{2})?", start_time.strip()):
        timestamp = f"{timestamp.strip()}T{start_time.strip()}"

    # User / Author
    author = _pick(norm, "author", "user", "assignee", "worker", "authoraccountid", "username", "organizer")
    if isinstance(author, dict):
        email = author.get("emailAddress")
        if isinstance(email, dict):  # Graph organizer: {"emailAddress": {"name", "address"}}
            email = email.get("name") or email.get("address")
        user_id = author.get("displayName") or author.get("name") or email or ""
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
        if not duration and event_start and event_end:
            duration = _seconds_between(event_start, str(event_end))

    # Category / Type / Status
    category = _pick(norm, "category", "categories", "type", "status", "casestatus") or "default"
    if isinstance(category, dict):
        category = category.get("name") or category.get("value") or "default"
    elif isinstance(category, list):  # Graph ``categories: [...]``
        category = ", ".join(str(c) for c in category) or "default"
    if event_start and category == "default":
        category = "all_day" if norm.get("isallday") else "event"

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


# AIS-409: a booking tool's saved entry belongs to the rows of the tool that
# lists those entries — the workdays report and sql read that tool's rows.
# Suffix of the write tool -> suffix of the listing tool (same server prefix).
_WRITE_THROUGH_SUFFIXES = (
    ("log_time", "list_time_entries"),  # in-repo OpenProjectMCP (AIS-408)
    ("create_time_entry", "list_time_entries"),  # openproject-ce-mcp
    ("update_time_entry", "list_time_entries"),
    ("bulkCreateWorklogs", "retrieveWorklogs"),  # TempoMCP
    ("createWorklog", "retrieveWorklogs"),
    ("updateWorklog", "retrieveWorklogs"),
    ("jira_add_worklog", "jira_get_worklog"),  # AtlassianMCP
)
_DELETED_ID_KEYS = ("deleted_time_entry_id", "deleted_worklog_id")


def write_through_target(tool_name: str) -> Optional[str]:
    """The listing tool whose rows a booking tool's result updates, or None."""
    name = str(tool_name or "")
    for write_suffix, list_suffix in _WRITE_THROUGH_SUFFIXES:
        if name.endswith(write_suffix):
            return name[: -len(write_suffix)] + list_suffix
    return None


def _is_preview_payload(data: Any) -> bool:
    """A validate-only answer of a preview-then-confirm write tool: it
    describes a change that has not happened, so it is not a record."""
    if not isinstance(data, dict):
        return False
    if isinstance(data.get("result"), dict):
        return _is_preview_payload(data["result"])
    if str(data.get("state") or "").lower() in ("preview", "duplicate"):
        return True
    return data.get("requires_confirmation") is True or data.get("confirmed") is False


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
    for key in ("issue_key", "issueKey", "issue", "key", "ticket", "ticket_id", "case_id", "calendar"):
        val = tool_args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


_DATE_WINDOW_KEY_PAIRS = (
    ("startdate", "enddate"),
    ("datefrom", "dateto"),
    ("from", "to"),
    ("spentonfrom", "spentonto"),  # openproject-ce-mcp list_time_entries
    ("start", "end"),
    ("starttimeiso", "endtimeiso"),  # m365_get_events / calendarView (AIS-339)
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


def window_arg_keys(tool_args: Any) -> Optional[Tuple[str, str, str, str]]:
    """(start_key, end_key, start_value, end_value) with the ORIGINAL key
    names of the tool call, or None when the args carry no sane window."""
    if not isinstance(tool_args, dict):
        return None
    norm_to_orig = {str(k).lower().replace("_", "").replace("-", ""): k for k in tool_args}
    for start_key, end_key in _DATE_WINDOW_KEY_PAIRS:
        s_orig, e_orig = norm_to_orig.get(start_key), norm_to_orig.get(end_key)
        if s_orig is None or e_orig is None:
            continue
        raw_start, raw_end = tool_args.get(s_orig), tool_args.get(e_orig)
        if raw_start is None or raw_end is None:
            continue
        m_start = _ISO_DAY_RE.match(str(raw_start).strip())
        m_end = _ISO_DAY_RE.match(str(raw_end).strip())
        if m_start and m_end and m_start.group(1) <= m_end.group(1):
            return s_orig, e_orig, str(raw_start).strip(), str(raw_end).strip()
    return None


def month_bounds(day: str) -> Tuple[str, str]:
    """(first day, last day) of the month that contains ``YYYY-MM-DD``."""
    from calendar import monthrange

    y, m = int(day[:4]), int(day[5:7])
    return f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-{monthrange(y, m)[1]:02d}"


def _months_between(start_day: str, end_day: str) -> List[str]:
    out: List[str] = []
    y, m = int(start_day[:4]), int(start_day[5:7])
    ey, em = int(end_day[:4]), int(end_day[5:7])
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def split_window_args(tool_args: Any) -> List[Tuple[str, Dict[str, Any]]]:
    """Month-by-month copies of a windowed tool call: ``[(month, args)]``.

    Empty when the args carry no window or the window fits in one calendar
    month — then the call runs as-is. Values keep their shape: a bare date
    stays a date, a datetime keeps its ``T…`` form and ``Z``/offset suffix,
    so any server that accepted the original values accepts the chunks.
    """
    keys = window_arg_keys(tool_args)
    if not keys:
        return []
    s_key, e_key, s_val, e_val = keys
    s_day, e_day = s_val[:10], e_val[:10]
    months = _months_between(s_day, e_day)
    if len(months) <= 1:
        return []

    def _fmt(day: str, template: str, end: bool) -> str:
        if "T" not in template and " " not in template:
            return day
        suffix = ""
        tail = template[10:]
        for marker in ("Z", "+", "-"):
            idx = tail.find(marker, 1)
            if idx != -1:
                suffix = tail[idx:]
                break
        return f"{day}T{'23:59:59' if end else '00:00:00'}{suffix}"

    out: List[Tuple[str, Dict[str, Any]]] = []
    for month in months:
        first, last = month_bounds(f"{month}-01")
        c_start = s_day if month == months[0] else first
        c_end = e_day if month == months[-1] else last
        chunk = dict(tool_args)
        chunk[s_key] = s_val if (month == months[0] and c_start == s_day) else _fmt(c_start, s_val, end=False)
        chunk[e_key] = e_val if (month == months[-1] and c_end == e_day) else _fmt(c_end, e_val, end=True)
        out.append((month, chunk))
    return out


def _completeness_from_payload(data: Any) -> Tuple[Optional[bool], Optional[List[Dict[str, Any]]]]:
    """``complete`` and ``months[]`` as reported by the server or by the
    month-splitting bridge, looked up through the ``{"result": …}`` envelope."""
    node = data
    for _ in range(3):
        if isinstance(node, dict):
            months = node.get("months")
            complete = node.get("complete")
            if isinstance(months, list) or isinstance(complete, bool):
                return (complete if isinstance(complete, bool) else None,
                        [m for m in months if isinstance(m, dict)] if isinstance(months, list) else None)
            inner = node.get("result")
            if isinstance(inner, str) and inner.strip()[:1] in "{[":
                try:
                    inner = json.loads(inner)
                except Exception:
                    return None, None
            node = inner
            continue
        break
    return None, None


def record_fetches(
    conn: sqlite3.Connection,
    *,
    tool_use_id: str,
    tool_name: str,
    reference_key: str,
    window: Optional[tuple],
    rows: int,
    complete: Optional[bool],
    months: Optional[List[Dict[str, Any]]],
) -> None:
    """One register row per fetched month (from ``months[]``) or per window."""
    if not tool_use_id or not window:
        return
    entries: List[tuple] = []
    if months:
        for m in months:
            month = str(m.get("month") or "")
            if not month:
                continue
            first, last = month_bounds(f"{month}-01")
            entries.append((
                tool_use_id, month, tool_name, reference_key or "",
                max(first, window[0]), min(last, window[1]),
                int(m.get("count") or 0), 1 if m.get("complete", True) else 0, str(m.get("error") or "") or None,
            ))
    else:
        for month in _months_between(window[0], window[1]):
            first, last = month_bounds(f"{month}-01")
            entries.append((
                tool_use_id, month, tool_name, reference_key or "",
                max(first, window[0]), min(last, window[1]),
                rows, 0 if complete is False else 1, None,
            ))
    conn.executemany(
        "INSERT OR REPLACE INTO mcp_fetches (tool_use_id, month, tool_name, reference_key, window_start, window_end, "
        "rows, complete, error) VALUES (?,?,?,?,?,?,?,?,?)",
        entries,
    )


def fetch_coverage(
    conn: sqlite3.Connection,
    tool_like: str,
    start_day: str,
    end_day: str,
    reference_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Per-month coverage of a source in the fetch register: ``complete``,
    ``incomplete`` (last fetch of that month was cut or failed) or ``none``."""
    conditions = ["tool_name LIKE ?", "month BETWEEN ? AND ?"]
    params: List[Any] = [tool_like, start_day[:7], end_day[:7]]
    if reference_key:
        conditions.append("lower(reference_key) = lower(?)")
        params.append(reference_key)
    rows = conn.execute(
        "SELECT month, complete, rows, fetched_at, error FROM mcp_fetches WHERE " + " AND ".join(conditions)
        + " ORDER BY month, fetched_at",
        params,
    ).fetchall()
    latest: Dict[str, tuple] = {}
    for month, complete, n, fetched_at, error in rows:
        latest[month] = (complete, n, fetched_at, error)  # last fetch wins
    out: List[Dict[str, Any]] = []
    for month in _months_between(start_day, end_day):
        if month not in latest:
            out.append({"month": month, "status": "none"})
            continue
        complete, n, fetched_at, error = latest[month]
        entry: Dict[str, Any] = {"month": month, "status": "complete" if complete else "incomplete",
                                 "rows": n, "fetched_at": fetched_at}
        if error:
            entry["error"] = error
        out.append(entry)
    return out


class IngestResult(int):
    """int-compatible ingest outcome: value = rows ingested (the historic
    return), plus window-replacement and cap-eviction metadata (AIS-275) and
    completeness (AIS-344)."""

    replaced: int = 0
    evicted: int = 0
    window: Optional[tuple] = None
    complete: Optional[bool] = None
    months: Optional[List[Dict[str, Any]]] = None
    # True when the payload was not recognised as a record list and the whole
    # document went into ONE row (AIS-354) — SQL aggregates over it are noise.
    blob: bool = False

    def __new__(cls, ingested: int = 0, replaced: int = 0, evicted: int = 0,
                window: Optional[tuple] = None, complete: Optional[bool] = None,
                months: Optional[List[Dict[str, Any]]] = None, blob: bool = False):
        obj = super().__new__(cls, ingested)
        obj.replaced = replaced
        obj.evicted = evicted
        obj.window = window
        obj.complete = complete
        obj.months = months
        obj.blob = blob
        return obj

    @property
    def incomplete_months(self) -> List[str]:
        return [str(m.get("month")) for m in (self.months or []) if not m.get("complete", True)]


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

    if _is_error_payload(data) or _is_preview_payload(data):
        return IngestResult(0)

    target = write_through_target(tool_name)
    if target:
        return _ingest_write_through(data, target, tool_use_id, db_path, tool_args)

    items = _flatten_nested_worklogs(_extract_items(data))
    fallback_ref = _reference_key_from_args(tool_args)
    complete, months = _completeness_from_payload(data)
    window = _date_window_from_args(tool_args)
    if not items:
        # A complete-but-empty window is still a fact worth registering
        # (the month has no entries); an unrecognised payload is not.
        if window and complete is True and tool_use_id:
            try:
                conn = get_db_connection(db_path)
                with conn:
                    record_fetches(conn, tool_use_id=tool_use_id, tool_name=tool_name, reference_key=fallback_ref,
                                   window=window, rows=0, complete=True, months=months)
                conn.close()
            except Exception as exc:
                logger.debug("fetch register write failed: %s", exc)
        return IngestResult(0, window=window, complete=complete, months=months)

    records = [_extract_fields(item, tool_name, tool_use_id, fallback_ref) for item in items]
    # `_extract_items` falls back to the document itself when it finds no
    # item list: one large row without a timestamp is that blob, not a record.
    blob = len(records) == 1 and not records[0][4] and len(content_strip) > _BLOB_ROW_CHARS
    # The stable source key the rows carry (source_key/calendar_key) is the
    # reference the window replace and the register are scoped to.
    ref_keys = {r[3] for r in records if r[3]}
    scope_ref = fallback_ref
    if len(ref_keys) == 1:
        scope_ref = next(iter(ref_keys))

    try:
        conn = get_db_connection(db_path)
        replaced = 0
        with conn:
            if window is not None:
                # This fetch is authoritative for its requested window: drop
                # the same tool's rows in that range first so stale entries
                # (moved/deleted upstream) do not survive the re-fetch. A
                # request-scoped reference (issue key, calendar name) narrows
                # the replacement to that reference — fetching the
                # OFFICEZEITEN calendar must not wipe the main calendar's
                # events in the same window (AIS-339). Only months the fetch
                # reports as complete are replaced (AIS-344): an incomplete
                # month keeps its earlier rows rather than losing them.
                ranges: List[Tuple[str, str]] = []
                if months:
                    for m in months:
                        if m.get("complete", True) and m.get("month"):
                            first, last = month_bounds(f"{m['month']}-01")
                            ranges.append((max(first, window[0]), min(last, window[1])))
                elif complete is not False:
                    ranges.append(window)
                for r_start, r_end in ranges:
                    delete_sql = "DELETE FROM mcp_records WHERE tool_name = ? AND substr(timestamp, 1, 10) BETWEEN ? AND ?"
                    delete_params: List[Any] = [tool_name, r_start, r_end]
                    if scope_ref:
                        delete_sql += " AND lower(reference_key) = lower(?)"
                        delete_params.append(scope_ref)
                    cursor = conn.execute(delete_sql, delete_params)
                    replaced += cursor.rowcount or 0
            conn.executemany("""
            INSERT OR REPLACE INTO mcp_records (
                id, tool_name, tool_use_id, reference_key, timestamp, user_id,
                duration_seconds, category, comment, raw_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, records)
            if window is not None:
                record_fetches(conn, tool_use_id=tool_use_id, tool_name=tool_name, reference_key=scope_ref,
                               window=window, rows=len(records), complete=complete, months=months)
            prune_result = prune_mcp_records(conn)
        logger.info(
            "Auto-ingested %d MCP records into mcp_records (tool: %s%s%s)",
            len(records), tool_name,
            f", replaced {replaced} rows in window {window[0]}..{window[1]}" if window else "",
            "" if complete is not False else ", INCOMPLETE",
        )
        return IngestResult(
            len(records), replaced=replaced,
            evicted=getattr(prune_result, "cap_evicted", 0), window=window,
            complete=complete, months=months, blob=blob,
        )
    except Exception as exc:
        logger.warning("Failed to store MCP records in SQLite: %s", exc)
        return IngestResult(0)
