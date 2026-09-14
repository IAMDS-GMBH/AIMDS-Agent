"""Ticket-system routing per project (AIS-327).

While Jira (AtlassianMCP / TempoMCP) and OpenProject (OpenProjectMCP) are both
connected, the agent must not guess which system a ticket, comment or time
booking belongs to. This tool is the deterministic memory for that decision:

* ``get(project)``      → the stored system for a project, or ``ask: true``
                          with the clarify choices to put to the user
* ``set(project, system)`` / ``unset(project)``
* ``set_default(system)`` / ``clear_default``  — "from now on always X"
* ``list``              → every mapping plus the default

Mappings live in ``~/.hermes/state.db`` (table ``ticket_routing``), one row
per project key plus a ``default`` row. A per-project mapping always beats the
default, so Jira can stay the system for an external customer project after
the default moved to OpenProject. Nothing here ever falls back silently: with
neither a mapping nor a default the answer is "ask".

The tool is registered only when at least two ticket-system families are
configured (``check_ticket_routing_requirements``), so single-system installs
pay no schema tokens.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TABLE = "ticket_routing"
DEFAULT_KEY = "default"
SYSTEMS = ("jira", "openproject")

# Config keys / catalog names that identify a ticket-system family. Tempo is
# time tracking on top of Jira, so it counts as the Jira family.
_FAMILY_MARKERS = {
    "jira": ("atlassian", "jira", "tempo"),
    "openproject": ("openproject",),
}

CLARIFY_CHOICES = [
    "Jira",
    "OpenProject",
    "Jira – from now on always",
    "OpenProject – from now on always",
]

_JIRA_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]+$")


def _default_db_path() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "state.db"


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = db_path or _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS {TABLE} (
            key TEXT PRIMARY KEY,
            system TEXT NOT NULL,
            label TEXT,
            note TEXT,
            updated_at TEXT
        )"""
    )
    return conn


def normalize_project(project: Any) -> str:
    """Stable key for a project reference.

    Jira keys (``AIS``, ``wsa``) are upper-cased; OpenProject identifiers and
    free-text names (``iamds-suite``, ``Kunde Müller``) are lower-cased and
    whitespace-collapsed. Both spellings of the same project land on one row.
    """
    text = re.sub(r"\s+", " ", str(project or "").strip())
    if not text:
        return ""
    if _JIRA_KEY_RE.match(text) and len(text) <= 12 and "-" not in text and text.upper() == text.upper():
        return text.upper()
    return text.lower()


def _normalize_system(system: Any) -> str:
    text = str(system or "").strip().lower()
    aliases = {
        "jira": "jira", "atlassian": "jira", "tempo": "jira", "atlassianmcp": "jira",
        "openproject": "openproject", "op": "openproject", "openprojectmcp": "openproject",
    }
    return aliases.get(text, text)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row(conn: sqlite3.Connection, key: str) -> Optional[Dict[str, Any]]:
    cur = conn.execute(f"SELECT key, system, label, note, updated_at FROM {TABLE} WHERE key = ?", (key,))
    hit = cur.fetchone()
    if not hit:
        return None
    return {"key": hit[0], "system": hit[1], "label": hit[2], "note": hit[3], "updated_at": hit[4]}


def _error(message: str, **extra: Any) -> str:
    payload = {"error": message}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def _act_get(args: Dict[str, Any], db_path: Optional[Path]) -> str:
    project = str(args.get("project") or "").strip()
    key = normalize_project(project)
    if not key:
        return _error("project is required", ask=True, clarify_choices=CLARIFY_CHOICES)
    conn = _connect(db_path)
    try:
        mapping = _row(conn, f"project:{key}")
        default = _row(conn, DEFAULT_KEY)
    finally:
        conn.close()
    payload: Dict[str, Any] = {"action": "get", "project": project, "project_key": key}
    if mapping:
        payload.update({"system": mapping["system"], "source": "project", "ask": False, "note": mapping.get("note") or ""})
    elif default:
        payload.update({"system": default["system"], "source": "default", "ask": False,
                        "note": "instance-wide default; a per-project mapping via action='set' overrides it"})
    else:
        payload.update({
            "system": None, "source": None, "ask": True,
            "clarify_choices": CLARIFY_CHOICES,
            "instruction": (
                "No ticket system is stored for this project and no default is set. Ask the user "
                "with `clarify` using clarify_choices, then store the answer via action='set' "
                "(or action='set_default' for a 'from now on always' answer). Do not guess."
            ),
        })
    return json.dumps(payload, ensure_ascii=False)


def _act_set(args: Dict[str, Any], db_path: Optional[Path]) -> str:
    project = str(args.get("project") or "").strip()
    key = normalize_project(project)
    system = _normalize_system(args.get("system"))
    if not key:
        return _error("project is required")
    if system not in SYSTEMS:
        return _error(f"system must be one of {list(SYSTEMS)}", got=str(args.get("system") or ""))
    note = str(args.get("note") or "").strip()
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute(
                f"INSERT INTO {TABLE} (key, system, label, note, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET system = excluded.system, label = excluded.label, "
                "note = excluded.note, updated_at = excluded.updated_at",
                (f"project:{key}", system, project, note, _now()),
            )
    finally:
        conn.close()
    return json.dumps({"action": "set", "project": project, "project_key": key, "system": system,
                       "note": note, "stored": True}, ensure_ascii=False)


def _act_unset(args: Dict[str, Any], db_path: Optional[Path]) -> str:
    key = normalize_project(args.get("project"))
    if not key:
        return _error("project is required")
    conn = _connect(db_path)
    try:
        with conn:
            cur = conn.execute(f"DELETE FROM {TABLE} WHERE key = ?", (f"project:{key}",))
            removed = cur.rowcount
    finally:
        conn.close()
    return json.dumps({"action": "unset", "project_key": key, "removed": bool(removed)}, ensure_ascii=False)


def _act_set_default(args: Dict[str, Any], db_path: Optional[Path]) -> str:
    system = _normalize_system(args.get("system"))
    if system not in SYSTEMS:
        return _error(f"system must be one of {list(SYSTEMS)}", got=str(args.get("system") or ""))
    note = str(args.get("note") or "").strip()
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute(
                f"INSERT INTO {TABLE} (key, system, label, note, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET system = excluded.system, note = excluded.note, "
                "updated_at = excluded.updated_at",
                (DEFAULT_KEY, system, "default", note, _now()),
            )
    finally:
        conn.close()
    return json.dumps({
        "action": "set_default", "system": system, "stored": True,
        "note": "Per-project mappings still override this default (e.g. Jira for an external customer project).",
    }, ensure_ascii=False)


def _act_clear_default(db_path: Optional[Path]) -> str:
    conn = _connect(db_path)
    try:
        with conn:
            cur = conn.execute(f"DELETE FROM {TABLE} WHERE key = ?", (DEFAULT_KEY,))
            removed = cur.rowcount
    finally:
        conn.close()
    return json.dumps({"action": "clear_default", "removed": bool(removed)}, ensure_ascii=False)


def _act_list(db_path: Optional[Path]) -> str:
    conn = _connect(db_path)
    try:
        cur = conn.execute(f"SELECT key, system, label, note, updated_at FROM {TABLE} ORDER BY key")
        rows = cur.fetchall()
    finally:
        conn.close()
    default: Optional[str] = None
    projects: List[Dict[str, Any]] = []
    for key, system, label, note, updated_at in rows:
        if key == DEFAULT_KEY:
            default = system
            continue
        projects.append({"project_key": key.removeprefix("project:"), "label": label, "system": system,
                         "note": note or "", "updated_at": updated_at})
    return json.dumps({"action": "list", "default": default, "projects": projects,
                       "systems": list(SYSTEMS)}, ensure_ascii=False)


def execute_ticket_routing(args: Dict[str, Any], db_path: Optional[Path] = None) -> str:
    action = str(args.get("action") or "get").strip().lower()
    try:
        if action == "get":
            return _act_get(args, db_path)
        if action == "set":
            return _act_set(args, db_path)
        if action == "unset":
            return _act_unset(args, db_path)
        if action == "set_default":
            return _act_set_default(args, db_path)
        if action == "clear_default":
            return _act_clear_default(db_path)
        if action == "list":
            return _act_list(db_path)
        return _error(f"unknown action {action!r}",
                      actions=["get", "set", "unset", "set_default", "clear_default", "list"])
    except sqlite3.Error as exc:
        logger.warning("ticket_routing: database error: %s", exc)
        return _error(f"database error: {exc}")


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


def configured_ticket_families(config: Optional[Dict[str, Any]] = None) -> List[str]:
    """Ticket-system families with at least one enabled ``mcp_servers`` entry."""
    if config is None:
        try:
            from hermes_cli.config import load_config

            config = load_config() or {}
        except Exception:
            return []
    servers = (config or {}).get("mcp_servers") or {}
    if not isinstance(servers, dict):
        return []
    found: List[str] = []
    for key, entry in servers.items():
        if not isinstance(entry, dict) or entry.get("enabled", True) is False:
            continue
        haystack = " ".join(
            str(part or "").lower()
            for part in (key, entry.get("catalog_name"), entry.get("command"), " ".join(map(str, entry.get("args") or [])), entry.get("url"))
        )
        for family, markers in _FAMILY_MARKERS.items():
            if family not in found and any(marker in haystack for marker in markers):
                found.append(family)
    return found


def check_ticket_routing_requirements() -> bool:
    """Only offer the tool when two ticket-system families are configured."""
    try:
        return len(configured_ticket_families()) >= 2
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Schema + registration
# ---------------------------------------------------------------------------

TICKET_ROUTING_SCHEMA = {
    "name": "ticket_routing",
    "description": (
        "Which ticket system (Jira or OpenProject) handles a project while both are connected. "
        "Call action='get' with the project (Jira key, OpenProject identifier/name or the customer/"
        "project the user named) BEFORE creating, updating, commenting, transitioning or booking "
        "time on a ticket. `ask: true` means: ask the user with `clarify` (choices provided), then "
        "store the answer via action='set' — or action='set_default' when the user says 'from now "
        "on always X'. A per-project mapping always overrides the default (Jira may stay in use for "
        "external customer projects). Never guess a system."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["get", "set", "unset", "set_default", "clear_default", "list"],
                "description": "get (default) | set | unset | set_default | clear_default | list",
            },
            "project": {
                "type": "string",
                "description": "Project reference: Jira key (AIS), OpenProject identifier (wsa) or the name the user used.",
            },
            "system": {
                "type": "string",
                "enum": ["jira", "openproject"],
                "description": "Target system for set / set_default.",
            },
            "note": {
                "type": "string",
                "description": "Optional reason (e.g. 'external customer, stays in Jira').",
            },
        },
        "required": [],
    },
}


from tools.registry import registry  # noqa: E402

registry.register(
    name="ticket_routing",
    toolset="ticket_routing",
    schema=TICKET_ROUTING_SCHEMA,
    handler=lambda args, **kw: execute_ticket_routing(args),
    check_fn=check_ticket_routing_requirements,
    emoji="🎫",
)
