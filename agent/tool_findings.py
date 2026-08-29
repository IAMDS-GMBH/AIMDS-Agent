"""Tool findings: what the agent learns about a tool flows back to the vault.

Nothing used to keep "what I learned about this tool" — the required date
format Tempo insists on, that ``total: -1`` from Jira is an error signal,
that one retrieval with a date range beats sixteen per-issue calls. The
skill review fork even forbade capturing tool failures outright, which
suppressed the useful half (parameter formats, required combinations,
pagination) along with the useless one ("X is broken").

Two halves:

* **Capture.** Every tool outcome of a turn is tallied here. At turn end a
  background review is spawned (the same fork memory/skill reviews use)
  when the turn shows a learning moment: a tool error followed by a
  successful retry with changed arguments, the first successful use of a
  tool that tool_search loaded into the session, or the same tool called
  three or more times. The prompt asks for durable, positive, parameter-
  level facts and saves them through the memory vault as one note per tool
  (``Tool: <name>``, type ``reference``, tag ``tool-finding``) — upsert by
  title, so the note grows instead of multiplying.
* **Surface.** When ``tool_describe`` (or a tool_search autoload) puts a tool
  into the session, the vault is asked for that note and its first 400
  characters ride along as ``notes``. No local copy, no shipped file: the
  memory server — or the Obsidian vault in fallback mode — is the store.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agent.tool_findings")

_LOG_ATTR = "_turn_tool_log"
_SEEN_OK_ATTR = "_tools_seen_ok"
TOOL_NOTE_PREFIX = "Tool: "
NOTES_MAX_CHARS = 400
REPEAT_THRESHOLD = 3


def _args_fingerprint(args: Any) -> str:
    try:
        raw = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        raw = str(args)
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:12]


def _is_mcp_tool(name: str) -> bool:
    return name.startswith("mcp_")


def reset_turn(agent) -> None:
    try:
        setattr(agent, _LOG_ATTR, [])
    except Exception:
        pass


def record_tool_outcome(agent, name: str, args: Any, result: Any, is_error: bool) -> None:
    """Called by the executor for every tool call that actually ran."""
    if not name or name in {"tool_search", "tool_describe", "tool_call"}:
        return
    log = getattr(agent, _LOG_ATTR, None)
    if not isinstance(log, list):
        log = []
        try:
            setattr(agent, _LOG_ATTR, log)
        except Exception:
            return
    log.append({"name": name, "error": bool(is_error), "args": _args_fingerprint(args)})


def turn_triggers(agent) -> Dict[str, List[str]]:
    """Which tools of this turn earned a findings review, and why."""
    log = getattr(agent, _LOG_ATTR, None) or []
    if not log:
        return {}
    seen_ok = getattr(agent, _SEEN_OK_ATTR, None)
    if not isinstance(seen_ok, set):
        seen_ok = set()
        try:
            setattr(agent, _SEEN_OK_ATTR, seen_ok)
        except Exception:
            pass
    try:
        from agent.deferred_tools import is_loaded
    except Exception:  # pragma: no cover
        def is_loaded(_agent, _name):  # type: ignore
            return False

    triggers: Dict[str, List[str]] = {}
    by_tool: Dict[str, List[Dict[str, Any]]] = {}
    for entry in log:
        by_tool.setdefault(entry["name"], []).append(entry)

    for name, entries in by_tool.items():
        reasons: List[str] = []
        # (a) an error followed by a success with different arguments
        failed_args = [e["args"] for e in entries if e["error"]]
        if failed_args and any(
            not e["error"] and e["args"] not in failed_args for e in entries
        ) and _is_mcp_tool(name):
            reasons.append("recovered after an error with changed arguments")
        # (b) first successful use of a tool that the catalog loaded this session
        if any(not e["error"] for e in entries) and name not in seen_ok and is_loaded(agent, name):
            reasons.append("first successful use after loading it via tool_search")
        # (c) repeated calls of the same tool
        if len(entries) >= REPEAT_THRESHOLD and _is_mcp_tool(name):
            reasons.append(f"called {len(entries)} times in one turn")
        if reasons:
            triggers[name] = reasons
        if any(not e["error"] for e in entries):
            seen_ok.add(name)
    return triggers


def build_review_prompt(triggers: Dict[str, List[str]], save_tool_hint: str) -> str:
    lines = [
        "Review how the tools below were used in the conversation above and record what a future "
        "session should know before calling them. Save each finding to the memory vault with "
        f"{save_tool_hint} as ONE note per tool: title exactly `{TOOL_NOTE_PREFIX}<tool name>`, "
        "type `reference`, tags [`tool-finding`, `<server>`]. If a note with that title exists, "
        "write the merged note (keep what still holds, add what is new) — never a second note.\n",
        "Tools that earned a look:",
    ]
    for name, reasons in triggers.items():
        lines.append(f"  • {name}: " + "; ".join(reasons))
    lines += [
        "",
        "Record only durable, positive, parameter-level facts: exact argument formats (dates, ids, "
        "enums), required combinations, pagination and limits, what an error payload means "
        "(e.g. `total: -1` is an error, not an empty result), and which tool to prefer for which "
        "job (\"for a date range use X, not one call per issue\"). Keep each note under 1,200 characters.",
        "Do NOT record: environment-dependent failures, transient errors that a retry fixed, "
        "'this tool is broken' claims, credentials, or one-off task narratives.",
        "If nothing durable emerged, say 'Nothing to save.' and stop.",
    ]
    return "\n".join(lines)


def finding_notes_for(agent, tool_name: str) -> str:
    """The vault's ``Tool: <name>`` note, trimmed for a tool_describe result."""
    if not tool_name:
        return ""
    try:
        from agent.memory_facade import MODE_NONE, MemoryFacade

        facade = MemoryFacade.for_agent(agent)
        if facade.mode == MODE_NONE:
            return ""
        title = f"{TOOL_NOTE_PREFIX}{tool_name}"
        for hit in facade.search(title, limit=3):
            if str(hit.get("title") or "").strip().lower() != title.lower():
                continue
            content = str(hit.get("content") or hit.get("preview") or "").strip()
            if not content:
                return ""
            return content[:NOTES_MAX_CHARS].rstrip() + ("…" if len(content) > NOTES_MAX_CHARS else "")
    except Exception as exc:
        logger.debug("tool findings lookup failed for %s: %s", tool_name, exc)
    return ""


__all__ = [
    "NOTES_MAX_CHARS",
    "REPEAT_THRESHOLD",
    "TOOL_NOTE_PREFIX",
    "build_review_prompt",
    "finding_notes_for",
    "record_tool_outcome",
    "reset_turn",
    "turn_triggers",
]
