"""Sync chat transcripts to the Suite memory (AIS-469).

Every interactive chat is mirrored into the user's Suite memory through the
``memory_session`` MCP tool, so it can be found and continued from any device
(Windows, Mac, OpenWebUI, any MCP client). The server stores it per user and
encrypted, splits long messages and makes the transcript searchable.

How it works:

* SQLite triggers (``hermes_state.SYNC_TRIGGER_SQL``) mark a session dirty on
  every message change, whichever code path wrote it.
* The desktop backend ticker calls :func:`maybe_run` once a minute. Each run
  sends the new messages of a few dirty sessions in batches, oldest session
  first, and records how far the remote is.
* Message ids are ``<local session id>:<ordinal among active messages>``, so a
  ``replace_messages`` that rewrites rows with the same content stays a no-op
  on the server; an edit replaces that message in place; a rewind becomes a
  ``truncate`` of the remote tail.
* Compression segments of one conversation share one remote transcript (keyed
  by the conversation root); the synthetic compaction summary is not sent.
* User deletes are queued as tombstones and sent as ``delete``/``truncate``;
  local retention pruning keeps the remote copy on purpose.
* Offline, an MCP error or no ``memory_session`` tool: nothing is lost, the
  session stays dirty and is retried with backoff.

Tool output is redacted (secrets), stripped of inline binary data and capped —
the transcript is for recall, not for replaying raw tool results.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Per message sent: tool output is capped, other text is kept whole.
TOOL_OUTPUT_MAX_CHARS = 2000
#: One memory_session(append) call: at most this many messages / bytes.
BATCH_MAX_MESSAGES = 60
BATCH_MAX_BYTES = 512 * 1024
#: One run: at most this many sessions, so a backfill never blocks the ticker.
RUN_MAX_SESSIONS = 8
#: Minimum seconds between runs (the ticker fires every 60 s).
RUN_INTERVAL_SECONDS = 55.0

#: Slimming: messages of fully synced chats idle this long are replaced locally
#: by a marker + short hint; the full text stays in the Suite memory.
SLIM_AFTER_DAYS_DEFAULT = 30
SLIM_KEEP_LAST = 20
SLIM_MIN_CHARS = 800
SLIM_HINT_CHARS = 200
#: Never sent to the server (it would overwrite the stored text); recognised
#: by the trigger, the slimmer and the hydrator.
MARKER_PREFIX = "⟦synced:"
_MARKER = re.compile(r"^⟦synced:(?P<remote>[^|⟧]+)\|(?P<mid>[^⟧]+)⟧ ?")

_BASE64_DATA = re.compile(r"data:[\w.+-]+/[\w.+-]+;base64,[A-Za-z0-9+/=\s]{64,}")
_run_lock = threading.Lock()
#: remote id -> (fetched at, {message_id: text}); resume hydrates the agent
#: history and the display history from one read.
_hydrate_cache: Dict[str, Tuple[float, Dict[str, str]]] = {}
_HYDRATE_TTL = 120.0
_last_run = 0.0


# ── message → transcript text ────────────────────────────────────────────


def _is_compaction_summary(content: str) -> bool:
    try:
        from agent.context_compressor import LEGACY_SUMMARY_PREFIX, SUMMARY_PREFIX
    except Exception:
        return False
    return content.startswith(SUMMARY_PREFIX) or content.startswith(LEGACY_SUMMARY_PREFIX)


def _flatten(content: Any) -> str:
    """Text of a message content: plain string or multimodal parts (list)."""
    if content is None:
        return ""
    if isinstance(content, list):
        out = []
        for p in content:
            if isinstance(p, dict):
                kind = p.get("type")
                if kind in ("text", "input_text"):
                    out.append(str(p.get("text") or ""))
                elif kind in ("image_url", "image", "input_image"):
                    out.append("[image]")
                elif kind in ("file", "input_file", "document"):
                    out.append("[file]")
            elif isinstance(p, str):
                out.append(p)
        return "\n".join(o for o in out if o)
    return str(content)


def _redact(text: str) -> str:
    text = _BASE64_DATA.sub("[binary data]", text)
    try:
        from agent.redact import redact_sensitive_text

        return redact_sensitive_text(text, force=True)
    except Exception:
        return text


def transcript_text(msg: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """(role, text) to send for one stored message, or None to skip it."""
    role = str(msg.get("role") or "")
    if role not in ("user", "assistant", "tool"):
        return None
    text = _flatten(msg.get("content")).strip()
    if text.startswith(MARKER_PREFIX):
        return None  # slimmed locally — the server holds the full text
    if role == "user" and _is_compaction_summary(text):
        return None
    if role == "assistant" and _is_compaction_summary(text):
        return None
    if role == "tool":
        name = str(msg.get("tool_name") or "tool")
        if len(text) > TOOL_OUTPUT_MAX_CHARS:
            text = text[:TOOL_OUTPUT_MAX_CHARS] + f" … ({len(text) - TOOL_OUTPUT_MAX_CHARS} chars omitted)"
        text = f"[{name}] {text}".strip()
    elif role == "assistant" and msg.get("tool_calls"):
        try:
            calls = json.loads(msg["tool_calls"]) if isinstance(msg["tool_calls"], str) else msg["tool_calls"]
            names = [str((c.get("function") or {}).get("name") or c.get("name") or "") for c in calls or []]
            names = [n for n in names if n]
            if names:
                text = (text + "\n" if text else "") + "[tools: " + ", ".join(names) + "]"
        except (ValueError, TypeError, AttributeError):
            pass
    if not text:
        return None
    return role, _redact(text)


def _iso(ts: Any) -> Optional[str]:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(ts)))
    except (TypeError, ValueError):
        return None


def build_messages(session_id: str, rows: List[Dict[str, Any]], start: int) -> List[Dict[str, Any]]:
    """memory_session messages for rows[start:], ids by ordinal."""
    out = []
    for ordinal in range(start, len(rows)):
        rendered = transcript_text(rows[ordinal])
        if rendered is None:
            continue
        role, text = rendered
        m = {"message_id": f"{session_id}:{ordinal}", "role": role, "content": text}
        ts = _iso(rows[ordinal].get("timestamp"))
        if ts:
            m["ts"] = ts
        out.append(m)
    return out


def _batches(messages: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    batches: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    size = 0
    for m in messages:
        n = len(m["content"].encode("utf-8")) + 200
        if cur and (len(cur) >= BATCH_MAX_MESSAGES or size + n > BATCH_MAX_BYTES):
            batches.append(cur)
            cur, size = [], 0
        cur.append(m)
        size += n
    if cur:
        batches.append(cur)
    return batches


# ── remote calls ─────────────────────────────────────────────────────────


class SessionRemote:
    """memory_session through the process MCP registry (no agent needed)."""

    def __init__(self, facade: Any, tool: str):
        self.facade = facade
        self.tool = tool

    @classmethod
    def for_process(cls) -> Optional["SessionRemote"]:
        try:
            from agent.memory_facade import MODE_MCP, MemoryFacade

            facade = MemoryFacade.for_process()
        except Exception as exc:
            logger.debug("session sync: memory facade unavailable (%s)", exc)
            return None
        if facade.mode != MODE_MCP:
            return None
        tool = facade._tool("memory_session")
        return cls(facade, tool) if tool else None

    def call(self, args: Dict[str, Any]) -> Dict[str, Any]:
        from agent.memory_facade import _unwrap_mcp_result

        raw = self.facade._call_read(self.tool, args, timeout=60.0)
        payload = _unwrap_mcp_result(raw)
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                payload = {"error": payload}
        if not isinstance(payload, dict):
            raise RuntimeError(f"memory_session: unexpected result {str(raw)[:200]}")
        err = payload.get("error") or (payload.get("message") if payload.get("code") else None)
        if err:
            text = f"{payload.get('code') or ''} {err}"
            # The remote already lacks what we want to delete: done.
            if "NOT_FOUND" in text or "session not found" in text.lower():
                return {"not_found": True}
            raise RuntimeError(f"memory_session: {str(err)[:300]}")
        return payload


# ── one run ──────────────────────────────────────────────────────────────


def sync_session(db: Any, remote: SessionRemote, row: Dict[str, Any]) -> int:
    """Bring one session's remote transcript up to date. Returns messages sent."""
    sid = row["id"]
    gen = int(row["sync_gen"] or 0)
    remote_id = row.get("sync_remote") or db.sync_remote_id(sid)
    rows = db.sync_messages(sid)
    count = len(rows)

    if int(row["synced_gen"] or 0) != gen:
        # Edit, rewind or replace since the last sync: re-send everything (the
        # server skips unchanged messages) and drop the remote tail beyond the
        # local count.
        start = 0
        if int(row["synced_upto"] or 0) > count:
            remote.call({"action": "truncate", "session_id": remote_id, "from_message_id": f"{sid}:{count}"})
    else:
        start = min(int(row["synced_upto"] or 0), count)

    sent = 0
    title = (row.get("title") or "").strip()
    for batch in _batches(build_messages(sid, rows, start)):
        args: Dict[str, Any] = {"action": "append", "session_id": remote_id, "messages": batch,
                                "hints": {"client": "hermes", "local_session_id": sid}}
        if title:
            args["title"] = title
        remote.call(args)
        sent += len(batch)
    db.sync_record(sid, gen=gen, upto=count, remote=remote_id)
    db.sync_clear_dirty(sid, gen=gen, count=count)
    return sent


def run_once(db: Any, remote: SessionRemote, *, max_sessions: int = RUN_MAX_SESSIONS) -> Dict[str, int]:
    """Send pending deletes, then up to *max_sessions* dirty sessions.

    A timeout ends the run (AIS-524): the abandoned call keeps running for up
    to the MCP timeout and holds the server's request lock, so every further
    session in the same run only queued behind it — eight 60 s waits per tick
    while the Suite hung, delaying the user's own memory calls. The sessions
    not tried stay due and go out with the next tick.
    """
    stats = {"sessions": 0, "messages": 0, "deletes": 0, "errors": 0}
    for tomb in db.sync_tombstones_due():
        try:
            args = {"action": tomb["op"], "session_id": tomb["remote_session_id"]}
            if tomb["op"] == "truncate":
                args["from_message_id"] = tomb["from_message_id"]
            remote.call(args)
            db.sync_tombstone_done(tomb["id"])
            stats["deletes"] += 1
        except Exception as exc:
            stats["errors"] += 1
            db.sync_tombstone_fail(tomb["id"], str(exc), attempts=int(tomb["attempts"] or 0) + 1)
            logger.info("session sync: remote %s of %s failed: %s", tomb["op"], tomb["remote_session_id"], exc)
            if isinstance(exc, TimeoutError):
                stats["stopped"] = 1
                return stats

    for row in db.sync_due_sessions(limit=max_sessions):
        try:
            stats["messages"] += sync_session(db, remote, row)
            stats["sessions"] += 1
        except Exception as exc:
            stats["errors"] += 1
            db.sync_fail(row["id"], str(exc), attempts=int(row["sync_attempts"] or 0) + 1)
            logger.info("session sync: %s failed: %s", row["id"], exc)
            if isinstance(exc, TimeoutError):
                logger.info("session sync: memory_session timed out — stopping this run")
                stats["stopped"] = 1
                break
    return stats


# ── slimming (local history shrinks to markers + hints) ─────────────────


_COPY = {
    "en": {"more": " … [{n} more characters in the Suite memory]",
           "offline": " [archived in the Suite memory — unavailable right now]"},
    "de": {"more": " … [{n} weitere Zeichen im Suite-Memory]",
           "offline": " [im Suite-Memory archiviert — gerade nicht erreichbar]"},
}


def _copy(key: str) -> str:
    """User-facing text in the display language (display.language)."""
    lang = "en"
    try:
        from hermes_cli.config import load_config

        lang = str(((load_config() or {}).get("display") or {}).get("language") or "en")[:2].lower()
    except Exception:
        pass
    return _COPY.get(lang, _COPY["en"])[key]


def marker(remote_id: str, message_id: str, text: str) -> str:
    hint = " ".join(text.split())[:SLIM_HINT_CHARS]
    more = len(text) - len(hint)
    tail = _copy("more").format(n=more) if more > 0 else ""
    return f"{MARKER_PREFIX}{remote_id}|{message_id}⟧ {hint}{tail}"


def slim_once(db: Any, *, after_days: int, limit: int = 10) -> int:
    """Shrink long assistant/tool messages of fully synced, idle chats.

    Kept whole: user messages, the last SLIM_KEEP_LAST messages, anything
    shorter than SLIM_MIN_CHARS, multimodal content. Runs only on sessions
    that are clean (every message acknowledged by the server); a message
    arriving meanwhile aborts that session's slimming.
    """
    if after_days <= 0:
        return 0
    slimmed = 0
    for sid in db.sync_slim_candidates(time.time() - after_days * 86400, limit=limit):
        rows = db.sync_messages(sid)
        remote_id = db.sync_remote_id(sid)
        replacements = []
        for ordinal, row in enumerate(rows[: max(0, len(rows) - SLIM_KEEP_LAST)]):
            content = row.get("content")
            if row.get("role") not in ("assistant", "tool") or not isinstance(content, str):
                continue
            if content.startswith(MARKER_PREFIX) or len(content) < SLIM_MIN_CHARS:
                continue
            if transcript_text(row) is None:
                continue  # never sent (e.g. a compaction summary) — keep it
            replacements.append((row["id"], marker(remote_id, f"{sid}:{ordinal}", content)))
        if replacements:
            slimmed += db.sync_slim_apply(sid, replacements)
    return slimmed


#: The server repeats this many characters of a part at the start of the next
#: (go-mcp-memory vector.DefaultPartOverlap); whitespace trimming shifts it a little.
PART_OVERLAP = 200


def _merge_parts(parts: List[str], overlap: int = PART_OVERLAP) -> str:
    """Join a message's stored parts, dropping the overlap the server added.

    Of all prefix/suffix matches the one closest to the known overlap wins —
    in repetitive text a longer match is a coincidence, not the overlap.
    """
    out = parts[0] if parts else ""
    for nxt in parts[1:]:
        best, best_dist = 0, None
        for k in range(min(len(out), len(nxt), overlap * 2), 0, -1):
            if out.endswith(nxt[:k]):
                dist = abs(k - overlap)
                if best_dist is None or dist < best_dist:
                    best, best_dist = k, dist
        out = out + ("" if best else " ") + nxt[best:]
    return out


def has_markers(messages: List[Dict[str, Any]]) -> bool:
    return any(isinstance(m.get("content"), str) and m["content"].startswith(MARKER_PREFIX) for m in messages or [])


def hydrate_messages(messages: List[Dict[str, Any]], remote: Optional["SessionRemote"] = None,
                     max_pages: int = 25) -> List[Dict[str, Any]]:
    """Replace slimmed markers by the full text from the Suite memory, in place.

    Offline or without the memory tool the hint stays, marked as archived.
    """
    wanted: Dict[str, set] = {}
    for m in messages or []:
        c = m.get("content")
        if isinstance(c, str):
            hit = _MARKER.match(c)
            if hit:
                wanted.setdefault(hit["remote"], set()).add(hit["mid"])
    if not wanted:
        return messages
    texts: Dict[str, str] = {}
    now = time.monotonic()
    for remote_id in list(wanted):
        cached = _hydrate_cache.get(remote_id)
        if cached and now - cached[0] < _HYDRATE_TTL and wanted[remote_id] <= set(cached[1]):
            texts.update(cached[1])
            del wanted[remote_id]
    if wanted and remote is None:
        remote = SessionRemote.for_process()
    if remote is not None:
        for remote_id, ids in wanted.items():
            try:
                parts: Dict[str, List[Tuple[int, str]]] = {}
                from_seq = 0
                for _ in range(max_pages):
                    page = remote.call({"action": "read", "session_id": remote_id, "from_seq": from_seq, "limit": 200})
                    for ch in page.get("chunks") or []:
                        if ch.get("message_id") in ids:
                            parts.setdefault(ch["message_id"], []).append((int(ch.get("part") or 0), str(ch.get("content") or "")))
                    if page.get("next_seq") is None:
                        break
                    from_seq = int(page["next_seq"])
                fetched = {mid: _merge_parts([t for _, t in sorted(ps)]) for mid, ps in parts.items()}
                _hydrate_cache[remote_id] = (now, fetched)
                texts.update(fetched)
            except Exception as exc:
                logger.info("session sync: hydrating %s failed: %s", remote_id, exc)
    for m in messages or []:
        c = m.get("content")
        if not isinstance(c, str):
            continue
        hit = _MARKER.match(c)
        if not hit:
            continue
        full = texts.get(hit["mid"])
        if full is not None:
            if m.get("role") == "tool" and full.startswith("["):
                full = full.split("] ", 1)[-1]  # drop the "[tool_name] " label added for the transcript
            elif m.get("role") == "assistant":
                full = re.sub(r"\n?\[tools: [^\]]*\]$", "", full)
            m["content"] = full
        else:
            m["content"] = c[hit.end():] + _copy("offline")
    return messages


def enabled() -> bool:
    try:
        from hermes_cli.config import load_config

        mem = (load_config() or {}).get("memory") or {}
        return bool(mem.get("session_sync", True))
    except Exception:
        return True


def slim_after_days() -> int:
    try:
        from hermes_cli.config import load_config

        mem = (load_config() or {}).get("memory") or {}
        return int(mem.get("session_slim_after_days", SLIM_AFTER_DAYS_DEFAULT))
    except Exception:
        return SLIM_AFTER_DAYS_DEFAULT


def maybe_run(db: Any = None) -> Optional[Dict[str, int]]:
    """Ticker entry point: throttled, single-flight, never raises."""
    global _last_run
    if not enabled():
        return None
    now = time.monotonic()
    if now - _last_run < RUN_INTERVAL_SECONDS or not _run_lock.acquire(blocking=False):
        return None
    try:
        _last_run = now
        remote = SessionRemote.for_process()
        if remote is None:
            return None
        if db is None:
            from hermes_state import SessionDB

            db = SessionDB()
        db.sync_mark_backfill()
        stats = run_once(db, remote)
        stats["slimmed"] = slim_once(db, after_days=slim_after_days())
        if stats["messages"] or stats["deletes"] or stats["errors"] or stats["slimmed"]:
            logger.info("session sync: %s", stats)
        return stats
    except Exception as exc:
        logger.debug("session sync run failed: %s", exc)
        return None
    finally:
        _run_lock.release()


__all__ = [
    "MARKER_PREFIX", "SessionRemote", "build_messages", "enabled", "has_markers", "hydrate_messages",
    "marker", "maybe_run", "run_once", "slim_once", "sync_session", "transcript_text",
]
