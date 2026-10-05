"""Deterministic handling of references in the user's message (AIS-485).

A message like "PRO-6 Ticket müssen wir uns als nächste ansehen" used to send
the model through file search, memory search, M365/GitHub search and a
non-existent Jira tool before it found the ticket tools (session
20261005_085337_68f41b, 4 minutes, 11 API calls). This module recognises
references with plain patterns — no LLM — and, per reference kind:

* loads the lookup tools of the systems that can resolve it into the session
  (deferred behind tool_search otherwise), preferred system first, and
* returns one compact, API-only hint line naming the reference and the tools,
  or saying that no such system is connected, so the model neither searches
  elsewhere first nor invents tools.

New reference kinds are a new ``ReferenceRule``, not new control flow.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Tokens shaped like a ticket key that are not one (standards, models,
# encodings). Kept short on purpose: a false positive only loads two tools.
_NOT_TICKET_PREFIXES = frozenset({
    "AES", "AVX", "CVE", "COVID", "CWE", "DIN", "ECMA", "EN", "ES", "GPT", "HL", "HTTP", "IEC", "IEEE",
    "IPV", "ISO", "MP", "PCI", "PEP", "RFC", "SHA", "SOC", "SSL", "TLS", "USB", "UTF", "WIN", "X",
})

_TICKET_KEY_RE = re.compile(r"(?<![A-Za-z0-9_./#-])([A-Z][A-Z0-9]{1,9})-(\d{1,7})(?![A-Za-z0-9_-])")
_OPENPROJECT_LINK_RE = re.compile(r"https?://\S+?/work_packages/(\d+)", re.IGNORECASE)
_JIRA_LINK_RE = re.compile(r"https?://\S+?/browse/([A-Z][A-Z0-9]+-\d+)")

_MAX_REFERENCES = 5


@dataclass(frozen=True)
class TicketSystem:
    """One way to resolve a ticket reference, in preference order."""

    label: str
    lookup: Callable[[Sequence[str]], List[str]]  # reachable names → lookup tool names to load
    accepts_key: Callable[[], bool] = lambda: True


@dataclass
class Reference:
    kind: str
    value: str
    systems: List[str] = field(default_factory=list)  # loaded tool names, preferred first


def find_ticket_references(text: str) -> List[str]:
    """Ticket keys and ticket links in ``text`` (deduplicated, in order)."""
    if not text or not isinstance(text, str):
        return []
    found: List[str] = []

    def add(value: str) -> None:
        if value not in found and len(found) < _MAX_REFERENCES:
            found.append(value)

    for match in _OPENPROJECT_LINK_RE.finditer(text):
        add(f"#{match.group(1)}")
    for match in _JIRA_LINK_RE.finditer(text):
        add(match.group(1))
    for match in _TICKET_KEY_RE.finditer(text):
        if match.group(1) not in _NOT_TICKET_PREFIXES:
            add(f"{match.group(1)}-{match.group(2)}")
    return found


def _ending(names: Sequence[str], suffixes: Tuple[str, ...]) -> List[str]:
    out: List[str] = []
    for suffix in suffixes:
        for name in names:
            if (name == suffix or name.endswith(f"_{suffix}")) and name not in out:
                out.append(name)
                break
    return out


def _openproject_lookup(kind: str) -> Callable[[Sequence[str]], List[str]]:
    def lookup(names: Sequence[str]) -> List[str]:
        try:
            from tools.openproject_names import is_hidden, openproject_kind, pm_suffix
        except Exception:
            return []
        out: List[str] = []
        for wanted in ("pm_get_work_package", "pm_search_work_packages"):
            for name in names:
                if openproject_kind(name) == kind and pm_suffix(name) == wanted and not is_hidden(name):
                    out.append(name)
                    break
        return out

    return lookup


def _suite_accepts_key() -> bool:
    try:
        from agent.openproject_suite import suite_accepts_display_ids

        return suite_accepts_display_ids()
    except Exception:
        return False


TICKET_SYSTEMS: Tuple[TicketSystem, ...] = (
    TicketSystem("OpenProject via the AIMDS Suite", _openproject_lookup("suite"), _suite_accepts_key),
    TicketSystem("OpenProject (local server)", _openproject_lookup("local")),
    TicketSystem("Jira", lambda names: _ending(names, ("jira_get_issue", "jira_search"))),
)


def _reachable_names(agent) -> List[str]:
    try:
        from agent.deferred_tools import guidance_tool_names

        return sorted(guidance_tool_names(agent))
    except Exception:
        return sorted(getattr(agent, "valid_tool_names", None) or [])


def _load(agent, names: Sequence[str]) -> List[str]:
    loaded: List[str] = []
    try:
        from agent.deferred_tools import load_deferred_tool, tool_search_active
    except Exception:
        return list(names)
    bridge = tool_search_active(agent)
    for name in names:
        if not bridge:
            loaded.append(name)  # everything is already in the schema
            continue
        try:
            resolved = load_deferred_tool(agent, name, reason="ticket_reference", enforce_cap=True)
        except Exception as exc:
            logger.debug("ticket reference autoload of %s failed: %s", name, exc)
            resolved = None
        if resolved:
            loaded.append(resolved)
    return loaded


def ticket_reference_hint(agent, text: str) -> str:
    """Load the ticket tools a ticket reference in ``text`` needs; return the
    API-only hint line ("" when the message holds no ticket reference)."""
    refs = find_ticket_references(text)
    if not refs:
        return ""
    names = _reachable_names(agent)
    parts: List[str] = []
    for system in TICKET_SYSTEMS:
        tools = _load(agent, system.lookup(names))
        if not tools:
            continue
        getter = tools[0]
        note = "the key works as id" if system.accepts_key() else "numeric id only; find a key via " + (
            f"`{tools[1]}`" if len(tools) > 1 else "search"
        )
        parts.append(f"{system.label}: `{getter}` ({note})")
    keys = ", ".join(refs)
    if not parts:
        return (
            f"[Ticket reference] {keys} looks like a ticket reference, but no ticket system (OpenProject, "
            "Jira) is connected in this session — say so instead of searching files, memory or the web."
        )
    logger.info("[AIS-485] ticket reference %s → %s", keys, "; ".join(parts))
    return (
        f"[Ticket reference] {keys}: look it up directly with the ticket tools before any file, memory or "
        f"web search — {'; '.join(parts)}. Try them in this order when the system is not named."
    )


__all__ = ["TICKET_SYSTEMS", "TicketSystem", "find_ticket_references", "ticket_reference_hint"]
