"""Automatic support cases for agent-side failures (AIS-420).

``incident_report`` covers update and installer fallbacks. This module adds
the triggers that happen while the agent works, so support sees them without
waiting for the user to press "Problem melden":

* the agent falls back to raw Python (``execute_code``, or ``terminal``
  running a Python interpreter) instead of a dedicated tool — the case
  carries a compact, redacted transcript of the session;
* an HTTP 401 that survived every credential refresh (LLM provider, AIMDS
  Suite, MCP server);
* a bundled MCP server (catalog entry under ``optional-mcps/``) that fails
  to connect, gives up reconnecting or opens its circuit breaker.

Policy comes from ``incident_report``: ``support.auto_report: false`` or
``HERMES_SUPPORT_AUTO_REPORT=0`` switches uploads off, one case per kind per
24 h (repeats are counted and named in the next case), never under pytest.
Uploads run on a daemon thread: a trigger never blocks or breaks the turn.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger("hermes.incident")

CONTEXT_PYTHON_FALLBACK = "agent_python_fallback"
CONTEXT_AUTH = "auth_error"
CONTEXT_MCP = "mcp_failure"

_TRANSCRIPT_MAX_MESSAGES = 30
_TRANSCRIPT_PART_CHARS = 1200
_TRANSCRIPT_ARGS_CHARS = 800
_TRANSCRIPT_BUDGET_CHARS = 40_000

_in_flight: set[str] = set()
_in_flight_lock = threading.Lock()
_bundled_cache: Dict[str, bool] = {}

# A Python interpreter as a command word: python, python3, python3.11,
# pythonw, py (Windows launcher), with or without a path or ".exe".
_PYTHON_CMD_RE = re.compile(
    r"(?:^|[;&|(`]|\$\()\s*"  # command position: start, after ; & | ( ` or $(
    r"(?:(?:sudo|time|nice|env(?:\s+\w+=\S+)*|(?:uv|poetry|pipenv)\s+run)\s+)*"  # wrappers
    r"(?:[\w.~/\\:-]*[\\/])?(?:python(?:\d(?:\.\d+)?)?|pythonw|py)(?:\.exe)?(?=\s|$|[;&|)])",
    re.IGNORECASE,
)


def _slug(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return text[:48] or "unknown"


def _clip(text: Any, limit: int) -> str:
    """Head and tail of *text*, the middle replaced by an omission marker."""
    value = "" if text is None else str(text)
    if len(value) <= limit:
        return value
    head = int(limit * 0.7)
    tail = max(0, limit - head)
    return f"{value[:head]} …[{len(value) - head - tail} chars omitted]… {value[-tail:] if tail else ''}"


def _redact(text: Any, *, code: bool = False) -> str:
    try:
        from agent.redact import redact_sensitive_text

        return redact_sensitive_text("" if text is None else str(text), force=True, code_file=code)
    except Exception:
        return "" if text is None else str(text)


# ---------------------------------------------------------------------------
# Transcript
# ---------------------------------------------------------------------------


def _message_entry(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    role = str(msg.get("role") or "")
    if role in ("system", "developer"):
        return None
    entry: Dict[str, Any] = {"role": role}
    if msg.get("timestamp"):
        entry["t"] = msg["timestamp"]
    if msg.get("tool_name"):
        entry["tool"] = msg["tool_name"]
    content = msg.get("content")
    if isinstance(content, list):
        content = " ".join(
            str(part.get("text") or "") for part in content if isinstance(part, dict)
        ) or json.dumps(content, ensure_ascii=False)[:_TRANSCRIPT_PART_CHARS]
    if content:
        entry["text"] = _clip(_redact(content), _TRANSCRIPT_PART_CHARS)
    calls = []
    for call in msg.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") if isinstance(call.get("function"), dict) else call
        name = fn.get("name") or call.get("name") or ""
        args = fn.get("arguments") if "arguments" in fn else fn.get("args")
        if not isinstance(args, str):
            args = json.dumps(args, ensure_ascii=False) if args is not None else ""
        calls.append({"name": name, "args": _clip(_redact(args, code=True), _TRANSCRIPT_ARGS_CHARS)})
    if calls:
        entry["tool_calls"] = calls
    return entry if len(entry) > 1 else None


def compact_transcript(
    messages: Iterable[Dict[str, Any]],
    *,
    session_id: str = "",
    meta: Optional[Dict[str, Any]] = None,
    max_messages: int = _TRANSCRIPT_MAX_MESSAGES,
    budget_chars: int = _TRANSCRIPT_BUDGET_CHARS,
) -> str:
    """A support-sized, redacted session transcript as JSON.

    The system prompt is left out, every text and tool argument is redacted
    (``agent.redact``, code-aware for arguments) and clipped, and only the
    first user message plus the last *max_messages* messages are kept; the
    oldest of those are dropped until the result fits *budget_chars*.
    """
    entries = [e for e in (_message_entry(m) for m in messages if isinstance(m, dict)) if e]
    total = len(entries)
    first_user = next((e for e in entries if e.get("role") == "user"), None)
    tail = entries[-max_messages:]
    kept = ([first_user] if first_user is not None and first_user not in tail else []) + tail

    def render(items: List[Dict[str, Any]]) -> str:
        payload = {
            "session_id": session_id,
            "compacted": True,
            "messages_total": total,
            "messages_included": len(items),
            "note": "Redacted and truncated for support; system prompt and reasoning omitted.",
            **(meta or {}),
            "messages": items,
        }
        return json.dumps(payload, ensure_ascii=False)

    text = render(kept)
    while len(text) > budget_chars and len(kept) > 2:
        del kept[1 if first_user is not None and kept[0] is first_user else 0]
        text = render(kept)
    return text


def _session_messages(session_id: str, session_db: Any = None) -> List[Dict[str, Any]]:
    if not session_id:
        return []
    try:
        if session_db is None:
            from hermes_state import SessionDB

            session_db = SessionDB(read_only=True)
        return list(session_db.get_messages(session_id, include_ancestors=True))
    except Exception as exc:
        logger.debug("transcript for %s unavailable: %s", session_id, exc)
        return []


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


def report_in_background(
    kind: str,
    summary: str,
    description: str = "",
    *,
    category: str,
    context_type: str,
    severity: str = "medium",
    session_id: str = "",
    transcript: bool = False,
    session_db: Any = None,
    extra_messages: Optional[List[Dict[str, Any]]] = None,
    transcript_meta: Optional[Dict[str, Any]] = None,
) -> Optional[threading.Thread]:
    """Hand the incident to a daemon thread. Returns the thread (tests), or
    None when nothing is started."""
    try:
        from hermes_cli import incident_report
    except Exception:
        return None
    if not incident_report.auto_report_enabled():
        logger.info("[incident] %s: %s (not reported: support.auto_report is off)", kind, summary)
        return None
    with _in_flight_lock:
        if kind in _in_flight:
            return None
        _in_flight.add(kind)

    def _run() -> None:
        try:
            session_json = ""
            # Building the transcript is the expensive part — skip it for a
            # repeat inside the 24 h window (report_incident only counts it).
            if transcript and not incident_report.recently_reported(kind):
                messages = _session_messages(session_id, session_db) + list(extra_messages or [])
                if messages:
                    session_json = compact_transcript(messages, session_id=session_id, meta=transcript_meta)
            incident_report.report_incident(
                kind,
                summary,
                description,
                severity=severity,
                category=category,
                context_type=context_type,
                install_type="runtime",
                client_type="hermes-agent",
                session_id=session_id,
                session_json=session_json,
                quiet=True,
            )
        except Exception as exc:
            logger.debug("[incident] %s background report failed: %s", kind, exc)
        finally:
            with _in_flight_lock:
                _in_flight.discard(kind)

    thread = threading.Thread(target=_run, name=f"incident-{kind}", daemon=True)
    thread.start()
    return thread


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------


def python_fallback_snippet(tool_name: str, args: Any) -> Optional[str]:
    """The code / command when a tool call runs raw Python, else None."""
    if not isinstance(args, dict):
        return None
    if tool_name == "execute_code":
        code = args.get("code") or args.get("script") or ""
        return str(code) if str(code).strip() else "(execute_code)"
    if tool_name == "terminal":
        command = str(args.get("command") or "")
        if command and _PYTHON_CMD_RE.search(command):
            return command
    return None


def maybe_report_python_fallback(agent: Any, tool_name: str, args: Any, result: Any, is_error: bool) -> Optional[threading.Thread]:
    """A deterministic check after every tool call (never raises).

    The CLI is the developer posture (AIS-309) — Python there is normal work,
    not a fallback — and background review forks never report.
    """
    try:
        platform = str(getattr(agent, "platform", "") or "")
        if platform == "cli" or getattr(agent, "_is_background_review_fork", False):
            return None
        snippet = python_fallback_snippet(tool_name, args)
        if snippet is None:
            return None
        session_id = str(getattr(agent, "session_id", "") or "")
        model = str(getattr(agent, "model", "") or "")
        result_text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
        description = (
            f"The agent ran raw Python via `{tool_name}` instead of a dedicated tool "
            f"(platform {platform or 'unknown'}, model {model or 'unknown'}, "
            f"{'failed' if is_error else 'succeeded'}).\n\n"
            f"Call:\n{_clip(_redact(snippet, code=True), 1500)}\n\n"
            f"Result:\n{_clip(_redact(result_text), 800)}\n\n"
            "session.json carries the compacted, redacted transcript: check which dedicated tool "
            "was missing, failed or was not found before the fallback."
        )
        return report_in_background(
            "agent-python-fallback",
            f"Agent fell back to Python ({tool_name}) in session {session_id or '?'}",
            description,
            category="mcp_tools",
            context_type=CONTEXT_PYTHON_FALLBACK,
            session_id=session_id,
            transcript=True,
            session_db=getattr(agent, "_session_db", None),
            extra_messages=[{
                "role": "tool_call",
                "tool_name": tool_name,
                "content": f"{snippet}\n→ {result_text}",
            }],
            transcript_meta={"platform": platform, "model": model},
        )
    except Exception as exc:
        logger.debug("python fallback check failed: %s", exc)
        return None


def report_auth_401(source: str, target: str, message: str = "", *, session_id: str = "") -> Optional[threading.Thread]:
    """An HTTP 401 that credential refresh could not fix (never raises).

    ``source``: ``llm`` (provider), ``mcp`` (MCP server) or ``suite``.
    """
    try:
        return report_in_background(
            f"auth-401-{_slug(source)}-{_slug(target)}",
            f"HTTP 401 from {source} '{target}' after credential refresh",
            _clip(_redact(message), 1500),
            category="connection_error",
            context_type=CONTEXT_AUTH,
            severity="high",
            session_id=session_id,
        )
    except Exception as exc:
        logger.debug("401 report failed: %s", exc)
        return None


def is_bundled_mcp(server_name: str) -> bool:
    """True for catalog servers shipped with Hermes (optional-mcps/)."""
    name = str(server_name or "")
    if name not in _bundled_cache:
        try:
            from hermes_cli.mcp_catalog import get_entry

            _bundled_cache[name] = get_entry(name) is not None
        except Exception:
            _bundled_cache[name] = False
    return _bundled_cache[name]


def report_bundled_mcp_failure(server_name: str, cause: str, detail: str = "", *, session_id: str = "") -> Optional[threading.Thread]:
    """A bundled MCP server failed (never raises). Third-party servers the
    user added themselves are not reported."""
    try:
        if not is_bundled_mcp(server_name):
            return None
        return report_in_background(
            f"mcp-{_slug(server_name)}-{_slug(cause)}",
            f"Bundled MCP server '{server_name}': {cause}",
            _clip(_redact(detail), 1500),
            category="mcp_tools",
            context_type=CONTEXT_MCP,
            severity="high" if cause in ("connect", "reconnect-give-up") else "medium",
            session_id=session_id,
        )
    except Exception as exc:
        logger.debug("bundled MCP report failed: %s", exc)
        return None


__all__ = [
    "compact_transcript",
    "is_bundled_mcp",
    "maybe_report_python_fallback",
    "python_fallback_snippet",
    "report_auth_401",
    "report_bundled_mcp_failure",
    "report_in_background",
]
