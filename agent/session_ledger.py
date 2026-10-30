"""Session ledger: what a session did, one entry per tool call worth keeping (AIS-797).

The executor calls :func:`record_tool_call` after every tool call. The
derivation is generic and deterministic — no per-tool code:

* **area** — the MCP server (or the upstream service a Suite tool proxies,
  ``mcp_<service>-<tool>``) or the toolset;
* **verb** — the first verb token of the tool name (``create``, ``update``,
  ``send``, ``list`` …), an ``action`` argument first;
* **kind** — ticket / mail / chat / file / note / web / artifact from name
  tokens and the area; clarify-shaped results become ``decision`` or ``open``;
* **ref** — identifiers from argument and result keys (key, id, subject,
  title, path, url, message_id), never bodies, descriptions or mail text;
* **status** — the result's (or the update's) status; a write that moves it
  records ``status_from`` (the last status the ledger saw for that ref, from a
  get, a list or an earlier write) and ``status_to``.

Calls without an identifiable item (terminal, sql, tool_search …) leave no
entry, failed calls and dry runs neither. Repeated reads of the same item
collapse. Entries live in ``state.db`` (``session_ledger``) under the
compression lineage root, so the ledger survives the session-id rotation of a
compression; the shape is the ``ledger_entry`` contract (agent/contracts).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

LEDGER_VERSION = 1
MAX_ENTRIES = 400  # per lineage; mirrors SessionDB.LEDGER_MAX_ENTRIES
BATCH_MAX = 20  # entries one batch write may add
SEEN_MAX = 50  # statuses one list result may teach the ledger
TITLE_MAX = 120
DETAIL_MAX = 120
STATUS_MAX = 80
_MEMO_MAX = 1000
_DELTAS_MAX = 32

KINDS = ("ticket", "mail", "chat", "file", "note", "web", "artifact", "decision", "open")
VERBS = ("create", "update", "delete", "send", "write", "read", "list", "search", "ask")
WRITE_VERBS = frozenset({"create", "update", "delete", "send", "write"})

_VERB_TOKENS = {
    "create": ("create", "add", "new", "insert", "upload", "generate", "book", "schedule"),
    "update": ("update", "edit", "modify", "patch", "set", "move", "rename", "change", "transition",
               "assign", "comment", "link", "replace", "mark", "close", "reopen", "respond"),
    "delete": ("delete", "remove", "trash", "archive", "destroy", "unlink"),
    "send": ("send", "reply", "forward", "post", "notify", "publish", "share"),
    "write": ("write", "save", "store", "append", "put", "upsert"),
    "read": ("read", "get", "fetch", "view", "show", "open", "describe", "extract", "navigate", "download"),
    "list": ("list", "browse", "recent"),
    "search": ("search", "find", "query", "lookup", "grep"),
}
_VERB_OF = {token: verb for verb, tokens in _VERB_TOKENS.items() for token in tokens}

# Name tokens (and adjacent-token pairs such as ``work_package``) per kind; the
# first kind with a hit wins, so the more specific kinds come first.
_KIND_TOKENS = (
    ("ticket", ("work_package", "workpackage", "issue", "issues", "ticket", "tickets", "epic", "jira")),
    ("mail", ("mail", "mails", "email", "emails", "inbox", "outlook")),
    ("chat", ("chat", "chats", "teams", "channel", "channels", "slack", "discord", "telegram")),
    ("note", ("memory", "note", "notes", "vault", "journal", "wiki", "confluence")),
    ("file", ("file", "files", "folder", "drive", "sharepoint", "onedrive", "directory", "document", "documents")),
    ("web", ("web", "browser", "url", "website")),
    ("artifact", ("artifact", "image", "diagram", "chart", "slides", "presentation", "pdf")),
)
# Areas that name their kind when the tool name does not (``pm_list_projects``).
_AREA_KINDS = (
    ("openproject", "ticket"), ("jira", "ticket"), ("linear", "ticket"),
    ("memory", "note"), ("websearch", "web"),
)

# Normalised (lowercase alphanumeric) keys per ref field, in preference order.
_REF_KEYS = {
    "key": ("key", "identifier", "issuekey", "ticketkey", "workpackagekey", "displayid"),
    "id": ("id", "itemid", "slug", "uid", "uuid", "chatid", "eventid", "pageid", "documentid", "fileid",
           "workpackageid", "issueid", "caseid", "threadid", "conversationid"),
    "title": ("title", "subject"),
    "url": ("url", "weburl", "weblink", "htmlurl", "browseurl", "webviewlink"),
    "path": ("path", "filepath"),
    "message_id": ("messageid", "internetmessageid"),
}
_REF_LIMITS = {"key": 120, "id": 200, "title": TITLE_MAX, "url": 500, "path": 500, "message_id": 300}
_STATUS_KEYS = ("status", "statusname")
_PLURAL_REF_KEYS = {"ids": "id", "keys": "key", "urls": "url", "paths": "path"}
# Collection keys beyond the shaper's ITEM_LIST_KEYS.
_EXTRA_LIST_KEYS = ("work_packages", "time_entries", "events", "emails", "mails", "chats", "notes",
                    "memories", "documents", "pages", "projects", "users", "rows")
_KEYLIKE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*-\d+$")
_SUITE_TOOL = re.compile(r"^mcp_([^-]+)-(.+)$")


# ----------------------------------------------------------------------
# derivation (pure)
# ----------------------------------------------------------------------
@dataclass
class Draft:
    """One entry before the ledger resolves status history and seq."""

    kind: str
    verb: str
    source: str
    area: str
    tool: str
    ref: Dict[str, str] = field(default_factory=dict)
    status: str = ""
    status_arg: bool = False  # the status came from the call's own arguments
    count: Optional[int] = None
    detail: str = ""
    seen: List[Tuple[Dict[str, str], str]] = field(default_factory=list)
    dedupe: str = ""  # identity of a ref-less list read


def _norm(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _clip(value: Any, n: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= n else text[: max(0, n - 1)] + "…"


def _scalar(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value.strip()
    return ""


def _status_value(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("value") or value.get("title")
    return _clip(_scalar(value), STATUS_MAX)


def _payload(result: Any) -> Any:
    """The JSON a tool returned, unwrapped from the untrusted-data preamble,
    trailing notes and ``{"result": …}`` envelopes; None for plain text."""
    if isinstance(result, (dict, list)):
        data = result
    else:
        text = str(result or "").strip()
        if not text:
            return None
        try:
            data = json.loads(text)
        except ValueError:
            from tools.mcp_json_ingestor import _isolate_json_document

            body = _isolate_json_document(text)
            if not body:
                return None
            try:
                data, _ = json.JSONDecoder().raw_decode(body)
            except ValueError:
                return None
    from cron.brief_sources.base import unwrap_result

    return unwrap_result(data)


def _origin(name: str) -> Tuple[str, str, str]:
    """``(source, area, tool part)`` of a registered tool name."""
    server = tool = None
    try:
        from tools.mcp_tool import get_mcp_tool_origin

        origin = get_mcp_tool_origin(name)
        if origin:
            server, tool = origin
    except Exception:
        pass
    if server is None and name.startswith("mcp_"):
        # Not registered in this process (or a test): mcp_<prefix>_<tool>.
        parts = name.split("_", 2)
        if len(parts) == 3:
            server, tool = parts[1], parts[2]
            if tool.startswith("mcp_") and tool.count("_") >= 2:
                # The Suite's sanitised mcp_<service>_<tool>.
                _, service, rest = tool.split("_", 2)
                return server, service, rest
    if server is not None:
        match = _SUITE_TOOL.match(tool or "")
        if match:
            return server, match.group(1), match.group(2)
        return server, server, tool or name
    toolset = ""
    try:
        from tools.registry import registry

        toolset = registry.get_toolset_for_tool(name) or ""
    except Exception:
        pass
    toolset = toolset or "agent"
    return toolset, toolset, name


def _tokens(text: str) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def _verb(tool_tokens: List[str], args: Dict[str, Any]) -> str:
    action = args.get("action") if isinstance(args.get("action"), str) else ""
    for token in _tokens(action) + tool_tokens:
        verb = _VERB_OF.get(token)
        if verb:
            return verb
    return "read"


def _kind(tool_tokens: List[str], area: str) -> Optional[str]:
    words = set(tool_tokens) | {a + "_" + b for a, b in zip(tool_tokens, tool_tokens[1:])}
    for kind, tokens in _KIND_TOKENS:
        if words.intersection(tokens):
            return kind
    area_l = area.lower()
    for needle, kind in _AREA_KINDS:
        if needle in area_l:
            return kind
    return None


def _ref(obj: Any) -> Dict[str, str]:
    if not isinstance(obj, dict):
        return {}
    norm: Dict[str, Any] = {}
    for key, value in obj.items():
        norm.setdefault(_norm(key), value)
    ref: Dict[str, str] = {}
    for field_name, keys in _REF_KEYS.items():
        for key in keys:
            value = _scalar(norm.get(key))
            if value:
                ref[field_name] = _clip(value, _REF_LIMITS[field_name])
                break
    # ``id: "DEMO-12"`` names the item by its key (OpenProject, Jira accept both).
    if "key" not in ref and _KEYLIKE.match(ref.get("id", "")):
        ref["key"] = ref.pop("id")
    url = ref.get("url", "")
    if url and not url.lower().startswith(("http://", "https://")):
        ref.pop("url")
    return ref


def _status(obj: Any) -> str:
    if not isinstance(obj, dict):
        return ""
    for key, value in obj.items():
        if _norm(key) in _STATUS_KEYS:
            return _status_value(value)
    return ""


def _has_identity(ref: Dict[str, str]) -> bool:
    return any(ref.get(k) for k in ("key", "id", "path", "url", "message_id"))


def _records(payload: Any) -> Tuple[List[Dict[str, Any]], bool]:
    """``(records, is_collection)`` of a result payload."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)], True
    if not isinstance(payload, dict):
        return [], False
    if _has_identity(_ref(payload)):
        return [payload], False
    from tools.mcp_result_shaper import ITEM_LIST_KEYS

    for key in (*ITEM_LIST_KEYS, *_EXTRA_LIST_KEYS):
        value = payload.get(key)
        if isinstance(value, list):
            return [r for r in value if isinstance(r, dict)], True
    return [payload], False


def _is_preview(args: Dict[str, Any], payload: Any) -> bool:
    if str(args.get("dry_run", "")).lower() in ("true", "1"):
        return True
    from tools.mcp_json_ingestor import _is_preview_payload

    return _is_preview_payload(payload)


def _fingerprint(tool: str, args: Dict[str, Any]) -> str:
    try:
        raw = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        raw = str(args)
    return tool + "|" + hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:12]


def derive(name: str, args: Any, result: Any) -> List[Draft]:
    """Ledger drafts for one successful tool call; empty when nothing is worth keeping."""
    args = args if isinstance(args, dict) else {}
    payload = _payload(result)
    if _is_preview(args, payload):
        return []
    from tools.mcp_json_ingestor import _is_error_payload

    if _is_error_payload(payload):
        return []
    source, area, tool_part = _origin(name)

    # A clarify-shaped answer: a decision when answered, an open question otherwise.
    if isinstance(payload, dict) and "response_state" in payload and "question" in payload:
        question = _clip(payload.get("question") or args.get("question"), TITLE_MAX)
        if not question:
            return []
        answered = payload.get("resolved") is True
        return [Draft(
            kind="decision" if answered else "open", verb="ask", source=source, area=area, tool=name,
            ref={"title": question}, status=_status_value(payload.get("response_state")),
            detail=_clip(payload.get("user_response"), DETAIL_MAX) if answered else "",
        )]

    tool_tokens = _tokens(tool_part)
    verb = _verb(tool_tokens, args)
    kind = _kind(tool_tokens, area)
    arg_ref = _ref(args)
    if kind is None:
        if "path" in arg_ref:
            kind = "file"
        elif "message_id" in arg_ref:
            kind = "mail"
        elif "url" in arg_ref:
            kind = "web"
    # A saved note typed as a decision is one (memory_save type=decision).
    if kind == "note" and _norm(args.get("type")) == "decision":
        kind = "decision"
    records, is_collection = _records(payload)
    # ``ids: [...]`` / ``keys: [...]``: a batch even when the result is plain text.
    named: List[Dict[str, Any]] = []
    for key, field_name in _PLURAL_REF_KEYS.items():
        values = args.get(key)
        if isinstance(values, list):
            named.extend({field_name: v} for v in values)
    if named and not records:
        is_collection = True
    write = verb in WRITE_VERBS

    def draft(ref: Dict[str, str], status: str, status_arg: bool = False, **extra: Any) -> Draft:
        return Draft(kind=kind or "artifact", verb=verb, source=source, area=area, tool=name,
                     ref=ref, status=status, status_arg=status_arg, **extra)

    if is_collection and write:
        # A batch write: one entry per item it names.
        out = []
        asked = _status(args)
        for record in (records or named)[:BATCH_MAX]:
            ref = _ref(record)
            if _has_identity(ref):
                own = _status(record)
                out.append(draft(ref, own or asked, bool(asked)))
        return out
    if kind is None:
        # Unknown service: only a write that names its item is worth an entry.
        ref = {**arg_ref, **_ref(records[0] if records else None)}
        if not (write and _has_identity(ref)):
            return []
    if is_collection:
        seen = []
        for record in records:
            ref, status = _ref(record), _status(record)
            if status and _has_identity(ref):
                seen.append((ref, status))
                if len(seen) >= SEEN_MAX:
                    break
        ref = {k: v for k, v in arg_ref.items() if k in ("path", "url")}
        return [draft(ref, "", count=len(records), seen=seen,
                      dedupe="" if _has_identity(ref) else _fingerprint(name, args))]

    record = records[0] if records else {}
    ref = dict(arg_ref)
    for field_name, value in _ref(record).items():
        if field_name != "path" or "path" not in ref:
            ref[field_name] = value
    if not _has_identity(ref) and not (write and ref.get("title")):
        return []
    asked = _status(args) if write else ""
    return [draft(ref, _status(record) or asked, bool(asked))]


def identity(area: str, ref: Dict[str, Any]) -> str:
    """Stable identity of a ref within an area (status lookups, dedupe)."""
    for key in ("key", "id", "path", "url", "message_id", "title"):
        value = str((ref or {}).get(key) or "").strip().lower()
        if value:
            return f"{str(area or '').lower()}:{value}"
    return ""


# ----------------------------------------------------------------------
# ledger (per agent)
# ----------------------------------------------------------------------
def _session_db(agent: Any) -> Any:
    db = getattr(agent, "_session_db", None)
    return db if callable(getattr(type(db), "append_ledger_entries", None)) else None


class SessionLedger:
    """Per-agent ledger state: lineage root, last known status per ref, deltas."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._roots: Dict[str, str] = {}
        self._root = ""
        self._status: "OrderedDict[str, str]" = OrderedDict()
        self._last: "OrderedDict[str, Tuple[str, str]]" = OrderedDict()
        self._entries: List[Dict[str, Any]] = []  # kept when there is no state.db
        self._deltas: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
        self._total = 0
        self._seq = 0

    @property
    def lineage_root(self) -> str:
        return self._root

    @property
    def total(self) -> int:
        return self._total

    def entries(self) -> List[Dict[str, Any]]:
        """In-memory entries (only kept when the agent has no state.db)."""
        return list(self._entries)

    def _remember(self, memo: "OrderedDict[str, Any]", key: str, value: Any) -> None:
        memo[key] = value
        memo.move_to_end(key)
        while len(memo) > _MEMO_MAX:
            memo.popitem(last=False)

    def _resolve_root(self, db: Any, session_id: str) -> str:
        root = self._roots.get(session_id)
        if root is None:
            root = session_id
            if db is not None:
                try:
                    root = db.get_lineage_root(session_id) or session_id
                except Exception as exc:
                    logger.debug("ledger lineage lookup failed: %s", exc)
            self._roots[session_id] = root
        if root != self._root:
            # Another conversation (or a restart): learn what it already holds.
            self._root = root
            self._status.clear()
            self._last.clear()
            self._entries = []
            self._total = 0
            if db is not None:
                try:
                    for entry in db.get_ledger(root):
                        self._learn(entry)
                    self._total = db.count_ledger(root)
                except Exception as exc:
                    logger.debug("ledger warm start failed: %s", exc)
        return root

    def _learn(self, entry: Dict[str, Any]) -> None:
        ident = identity(entry.get("area", ""), entry.get("ref") or {})
        if not ident:
            return
        if entry.get("status"):
            self._remember(self._status, ident, entry["status"])
        self._remember(self._last, ident, (entry.get("verb", ""), entry.get("status", "")))

    def add(self, agent: Any, drafts: Iterable[Draft], *, tool_call_id: str = "") -> List[Dict[str, Any]]:
        session_id = getattr(agent, "session_id", "")
        if not isinstance(session_id, str) or not session_id:
            return []
        db = _session_db(agent)
        turn_id = getattr(agent, "_current_turn_id", "")
        turn_id = turn_id if isinstance(turn_id, str) else ""
        tool_call_id = tool_call_id if isinstance(tool_call_id, str) else ""
        now = time.time()
        with self._lock:
            root = self._resolve_root(db, session_id)
            rows: List[Tuple[str, Dict[str, Any]]] = []
            for d in drafts:
                ident = identity(d.area, d.ref)
                memo_key = ident or d.dedupe
                if d.verb not in WRITE_VERBS and memo_key and self._last.get(memo_key) == (d.verb, d.status):
                    continue  # the same read again
                entry: Dict[str, Any] = {
                    "v": LEDGER_VERSION, "ts": now, "kind": d.kind, "verb": d.verb, "source": d.source,
                    "area": d.area, "tool": d.tool, "ref": dict(d.ref),
                    "lineage_root": root, "session_id": session_id,
                }
                if d.status:
                    entry["status"] = d.status
                    known = self._status.get(ident)
                    if d.verb in WRITE_VERBS and d.verb != "create":
                        if known and known != d.status:
                            entry["status_from"], entry["status_to"] = known, d.status
                        elif not known and d.status_arg:
                            entry["status_to"] = d.status
                if d.count is not None:
                    entry["count"] = int(d.count)
                if d.detail:
                    entry["detail"] = d.detail
                if turn_id:
                    entry["turn_id"] = turn_id
                if tool_call_id:
                    entry["tool_call_id"] = tool_call_id
                if ident and d.status:
                    self._remember(self._status, ident, d.status)
                if memo_key:
                    self._remember(self._last, memo_key, (d.verb, d.status))
                for ref, status in d.seen:
                    seen_ident = identity(d.area, ref)
                    if seen_ident:
                        self._remember(self._status, seen_ident, status)
                rows.append((ident, entry))
            if not rows:
                return []
            seqs: List[int] = []
            if db is not None:
                try:
                    seqs = db.append_ledger_entries(root, session_id, rows)
                except Exception as exc:
                    logger.debug("ledger persist failed: %s", exc)
            if len(seqs) != len(rows):
                seqs = [self._seq + n + 1 for n in range(len(rows))]
            self._seq = max(self._seq, seqs[-1])
            out = []
            for seq, (_ident, entry) in zip(seqs, rows):
                entry["seq"] = seq
                out.append(entry)
            if db is None:
                self._entries = (self._entries + out)[-MAX_ENTRIES:]
            self._total = min(self._total + len(out), MAX_ENTRIES)
            if tool_call_id:
                self._deltas[tool_call_id] = out
                while len(self._deltas) > _DELTAS_MAX:
                    self._deltas.popitem(last=False)
            return out

    def pop_delta(self, tool_call_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            return self._deltas.pop(tool_call_id, None) or []


def ledger_for(agent: Any) -> Optional[SessionLedger]:
    """The agent's ledger, created on first use (None when it cannot hold one)."""
    ledger = getattr(agent, "_session_ledger", None)
    if isinstance(ledger, SessionLedger):
        return ledger
    ledger = SessionLedger()
    try:
        setattr(agent, "_session_ledger", ledger)
    except Exception:
        return None
    return ledger


def record_tool_call(
    agent: Any, name: str, args: Any, result: Any, *, is_error: bool = False, tool_call_id: str = "",
) -> List[Dict[str, Any]]:
    """Executor hook: add the entries one finished tool call earns (never raises)."""
    if is_error or not name:
        return []
    try:
        drafts = derive(name, args, result)
        if not drafts:
            return []
        ledger = ledger_for(agent)
        return ledger.add(agent, drafts, tool_call_id=tool_call_id) if ledger else []
    except Exception as exc:
        logger.debug("session ledger record failed for %s: %s", name, exc)
        return []


def pop_delta(agent: Any, tool_call_id: str) -> Optional[Dict[str, Any]]:
    """The ``session.ledger`` event payload for one tool call, once (None if it added nothing)."""
    ledger = getattr(agent, "_session_ledger", None)
    if not isinstance(ledger, SessionLedger) or not tool_call_id:
        return None
    entries = ledger.pop_delta(tool_call_id)
    if not entries:
        return None
    return {
        "v": LEDGER_VERSION, "session_id": entries[-1]["session_id"], "lineage_root": ledger.lineage_root,
        "entries": entries, "total": ledger.total,
    }


def read_page(db: Any, session_id: str, *, after: int = 0) -> Dict[str, Any]:
    """``LedgerPage`` for any segment of a conversation (endpoint, /notes)."""
    root = db.get_lineage_root(session_id) or session_id
    return {
        "v": LEDGER_VERSION, "session_id": session_id, "lineage_root": root,
        "entries": db.get_ledger(root, after=after), "total": db.count_ledger(root),
    }


def format_entries(entries: Iterable[Dict[str, Any]]) -> List[str]:
    """Plain-text lines for the CLI ``/notes`` probe."""
    lines = []
    for e in entries:
        ref = e.get("ref") or {}
        label = ref.get("key") or ref.get("path") or ref.get("url") or ref.get("id") or ref.get("message_id") or ""
        title = ref.get("title") or ""
        what = " ".join(p for p in (label, f"“{title}”" if title and title != label else "") if p)
        if e.get("count") is not None:
            what = (what + " " if what else "") + f"({e['count']} records)"
        status = ""
        if e.get("status_to"):
            status = f" [{e.get('status_from') or '?'} → {e['status_to']}]"
        elif e.get("status"):
            status = f" [{e['status']}]"
        detail = f" = {e['detail']}" if e.get("detail") else ""
        stamp = time.strftime("%H:%M", time.localtime(float(e.get("ts") or 0)))
        lines.append(f"{stamp}  {e.get('verb', ''):<6} {e.get('kind', ''):<8} {e.get('area', '')}: {what}{status}{detail}")
    return lines
