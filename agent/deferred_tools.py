"""Per-session loading of deferred tools (progressive disclosure, step two).

``tools/tool_search.py`` keeps MCP and plugin tools out of the model-facing
tools array and offers three bridge tools instead. Until this module existed
that was the whole story: a tool the model found via ``tool_search`` was
*not* callable by name — only through the ``tool_call`` wrapper — while the
system prompt promised the opposite ("tools returned by tool_search are
IMMEDIATELY callable"). Sessions in ``~/.hermes/state.db`` show 69 rejected
direct calls in one month, most of them right after a successful search.

This module makes the promise true. A deferred tool enters the *session's*
tool array when the model

* gets it back from ``tool_search`` (top ``autoload_top_n`` hits),
* loads it explicitly with ``tool_describe``,
* invokes it through ``tool_call`` (so the next call can be direct), or
* simply calls it by its real name (the conversation loop asks here before
  rejecting an unknown tool name).

Design constraints
------------------
* State lives on the agent instance only (``agent._loaded_deferred_tools``).
  ``model_tools.get_tool_definitions`` is memoised process-wide and must
  never see a session's loaded tools.
* The catalog stays stateless (see the OpenClaw cron regression in
  ``tools/tool_search.py``). The loaded set is re-validated against the live
  registry whenever ``registry._generation`` moves: a tool that vanished or
  left the session's scope is dropped, a changed schema is replaced.
* Scope is the same gate ``tool_call`` uses: the deferrable subset of the
  session's own enabled/disabled toolsets. A restricted session can not
  gain a tool here that the bridge would refuse to dispatch.
* Loaded schemas are appended at the *end* of ``agent.tools`` and never
  reordered. Anthropic's prompt cache prefixes tools → system → messages,
  so every load invalidates the prefix once; stable ordering keeps that at
  one cache write per load event rather than one per turn.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("agent.deferred_tools")

# Soft cap for tools loaded implicitly by tool_search hits. Explicit loads
# (tool_describe, tool_call, a direct call by name) always go through.
MAX_AUTOLOADED_TOOLS = 40

_LOADED_ATTR = "_loaded_deferred_tools"
_LOADED_GEN_ATTR = "_loaded_deferred_gen"
_SCOPE_CACHE_ATTR = "_deferred_scope_cache"
# Skills the primary memory server advertises in its memory_context reply
# (``suggested_skills``). They are served by the server, read with its
# ``skill`` tool, and never shipped locally — the catalog lists them next to
# local skills so one search covers both.
_MCP_SKILLS_ATTR = "_mcp_skill_catalog"
MCP_SKILL_HIT_CAP = 2


# ---------------------------------------------------------------------------
# Registry / scope helpers
# ---------------------------------------------------------------------------


def _registry_generation() -> int:
    try:
        from tools.registry import registry

        return int(getattr(registry, "_generation", 0) or 0)
    except Exception:
        return 0


def _loaded(agent) -> Dict[str, Dict[str, Any]]:
    loaded = getattr(agent, _LOADED_ATTR, None)
    if not isinstance(loaded, dict):
        loaded = {}
        try:
            setattr(agent, _LOADED_ATTR, loaded)
        except Exception:
            pass
    return loaded


def _valid_names(agent) -> set:
    names = getattr(agent, "valid_tool_names", None)
    if not isinstance(names, set):
        names = set(names or [])
        try:
            agent.valid_tool_names = names
        except Exception:
            pass
    return names


def _tools_list(agent) -> List[Dict[str, Any]]:
    tools = getattr(agent, "tools", None)
    if not isinstance(tools, list):
        tools = list(tools or [])
        try:
            agent.tools = tools
        except Exception:
            pass
    return tools


def tool_search_active(agent) -> bool:
    """Deferred loading only makes sense while the bridge is in the session."""
    try:
        from tools.tool_search import TOOL_SEARCH_NAME
    except Exception:
        return False
    return TOOL_SEARCH_NAME in _valid_names(agent)


def scoped_defs_map(agent) -> Dict[str, Dict[str, Any]]:
    """``{name: tool_def}`` for every tool the session's toolsets grant.

    Built from the *pre-assembly* definitions (``skip_tool_search_assembly``),
    i.e. exactly what ``model_tools`` would have emitted for this session
    before the bridge collapsed the deferrable ones — check_fn filtering,
    dynamic schema rewrites and toolset scope included. Cached on the agent
    against the registry generation and the toolset selection.
    """
    try:
        import model_tools
    except Exception:
        return {}

    enabled = getattr(agent, "enabled_toolsets", None)
    disabled = getattr(agent, "disabled_toolsets", None)
    key = (
        _registry_generation(),
        frozenset(enabled) if enabled is not None else None,
        frozenset(disabled) if disabled is not None else None,
    )
    cached = getattr(agent, _SCOPE_CACHE_ATTR, None)
    if cached is not None and cached[0] == key:
        return cached[1]

    defs_map: Dict[str, Dict[str, Any]] = {}
    try:
        defs = model_tools.get_tool_definitions(
            enabled_toolsets=enabled,
            disabled_toolsets=disabled,
            quiet_mode=True,
            skip_tool_search_assembly=True,
        ) or []
        for td in defs:
            name = (td.get("function") or {}).get("name", "")
            if name:
                defs_map[name] = td
    except Exception as exc:
        logger.debug("scoped_defs_map failed: %s", exc)
        defs_map = {}

    try:
        setattr(agent, _SCOPE_CACHE_ATTR, (key, defs_map))
    except Exception:
        pass
    return defs_map


def scoped_deferrable_names(agent) -> frozenset:
    """Deferrable tool names the session may reach (the ``tool_call`` gate)."""
    try:
        from tools.tool_search import BRIDGE_TOOL_NAMES, is_deferrable_tool_name
    except Exception:
        return frozenset()
    return frozenset(
        name
        for name in scoped_defs_map(agent)
        if name not in BRIDGE_TOOL_NAMES and is_deferrable_tool_name(name)
    )


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def is_loaded(agent, name: str) -> bool:
    return name in _loaded(agent)


def load_deferred_tool(
    agent,
    name: str,
    *,
    reason: str = "direct",
    enforce_cap: bool = False,
) -> Optional[str]:
    """Add one deferred tool to the session's tools array.

    Returns the canonical tool name when the tool is now (or already was)
    callable by name, ``None`` when it may not be loaded: the bridge is not
    active, the name does not resolve, the tool is a core tool (already
    visible), it is outside the session's toolset scope, or the autoload
    cap is hit.
    """
    if not name or not tool_search_active(agent):
        return None
    try:
        from tools.tool_search import (
            BRIDGE_TOOL_NAMES,
            _resolve_tool_entry,
            is_deferrable_tool_name,
        )
    except Exception:
        return None

    if name in BRIDGE_TOOL_NAMES:
        return None

    resolved, entry, err = _resolve_tool_entry(name)
    if err or entry is None or not resolved:
        return None
    if not is_deferrable_tool_name(resolved):
        return None

    valid = _valid_names(agent)
    if resolved in valid:
        return resolved

    td = scoped_defs_map(agent).get(resolved)
    if td is None:
        logger.debug("deferred tool %s is outside the session scope", resolved)
        return None

    loaded = _loaded(agent)
    if enforce_cap and len(loaded) >= MAX_AUTOLOADED_TOOLS:
        logger.debug("autoload cap reached (%d); not loading %s", MAX_AUTOLOADED_TOOLS, resolved)
        return None

    schema = dict(td)
    _tools_list(agent).append(schema)
    valid.add(resolved)
    loaded[resolved] = schema
    try:
        setattr(agent, _LOADED_GEN_ATTR, _registry_generation())
    except Exception:
        pass
    logger.info("[AIS-161] loaded deferred tool %s into session (%s)", resolved, reason)
    return resolved


def _remove_from_tools(agent, name: str) -> None:
    tools = _tools_list(agent)
    tools[:] = [td for td in tools if (td.get("function") or {}).get("name") != name]
    _valid_names(agent).discard(name)
    _loaded(agent).pop(name, None)


def ensure_loaded_tools_current(agent) -> bool:
    """Re-validate loaded tools against the live registry.

    Cheap when nothing changed (one int compare). When the registry
    generation moved — an MCP server reconnected, a plugin loaded — every
    loaded tool is looked up again in the session scope: gone or out of
    scope → removed, schema changed → replaced in place. Returns True when
    ``agent.tools`` was modified.
    """
    loaded = _loaded(agent)
    if not loaded:
        return False
    generation = _registry_generation()
    if getattr(agent, _LOADED_GEN_ATTR, None) == generation:
        return False

    defs_map = scoped_defs_map(agent)
    changed = False
    tools = _tools_list(agent)
    for name in list(loaded):
        live = defs_map.get(name)
        if live is None:
            _remove_from_tools(agent, name)
            logger.info("[AIS-161] dropped loaded tool %s: no longer registered/in scope", name)
            changed = True
            continue
        if live != loaded[name]:
            replacement = dict(live)
            for idx, td in enumerate(tools):
                if (td.get("function") or {}).get("name") == name:
                    tools[idx] = replacement
            loaded[name] = replacement
            changed = True
    try:
        setattr(agent, _LOADED_GEN_ATTR, generation)
    except Exception:
        pass
    return changed


def apply_tool_definitions(agent, new_defs: Optional[List[Dict[str, Any]]]) -> None:
    """Install a freshly assembled tool list and re-append still-valid loads.

    Every caller that used to write ``agent.tools``/``agent.valid_tool_names``
    directly (toolset change, MCP reload, ACP reconfigure) goes through here,
    otherwise a reload silently forgets what the session had loaded.
    """
    defs = list(new_defs or [])
    agent.tools = defs
    agent.valid_tool_names = {
        (td.get("function") or {}).get("name", "") for td in defs
    } - {""}

    loaded = _loaded(agent)
    if not loaded:
        return
    try:
        setattr(agent, _SCOPE_CACHE_ATTR, None)
    except Exception:
        pass
    defs_map = scoped_defs_map(agent) if tool_search_active(agent) else {}
    for name in list(loaded):
        live = defs_map.get(name)
        if live is None:
            loaded.pop(name, None)
            continue
        if name in agent.valid_tool_names:
            loaded[name] = next(
                td for td in agent.tools if (td.get("function") or {}).get("name") == name
            )
            continue
        schema = dict(live)
        agent.tools.append(schema)
        agent.valid_tool_names.add(name)
        loaded[name] = schema
    try:
        setattr(agent, _LOADED_GEN_ATTR, _registry_generation())
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Prompt guidance: which tools does this session *reach*, loaded or deferred?
# ---------------------------------------------------------------------------


def guidance_tool_names(agent) -> set:
    """Tool names the system-prompt guidance builders should consider.

    ``agent.valid_tool_names`` holds only the tools currently in the schema —
    with the bridge active that is the ~20 core tools, every MCP tool is
    deferred. Guidance gated on that set (Teams send, Outlook memory and
    signature, Jira, the skill index's ``requires_tools``) silently vanished
    from every tool_search session (AIS-289, session 20260904_090002_142f89:
    a 37k-char prompt without a single ``m365_`` name). Returns the visible
    names plus every deferrable tool in the session's toolset scope; falls
    back to the visible names when the bridge is off or the scope lookup
    fails.
    """
    visible = set(_valid_names(agent))
    try:
        if not tool_search_active(agent):
            return visible
        return visible | set(scoped_deferrable_names(agent))
    except Exception as exc:
        logger.debug("guidance_tool_names fell back to visible tools: %s", exc)
        return visible


# ---------------------------------------------------------------------------
# Message hook: a link in the user's message → load the tools that handle it
# ---------------------------------------------------------------------------

# (compiled pattern, tool suffixes to load). Suffixes resolve against the
# registry (``_resolve_tool_entry`` accepts bare MCP tool names), so the rule
# is independent of the server prefix and a no-op when the server is not
# installed. Deterministic on purpose: the model no longer has to *find*
# ``m365_download_chat_files`` behind a pasted Teams link.
_MESSAGE_AUTOLOAD_RULES: Tuple[Tuple["re.Pattern[str]", Tuple[str, ...]], ...] = (
    (
        re.compile(r"teams\.microsoft\.com/l/(?:chat|message)/", re.IGNORECASE),
        (
            "m365_find_chat",
            "m365_download_chat_files",
            "m365_list_chat_messages",
            "m365_send_chat_message",
        ),
    ),
    (
        re.compile(r"(?:\.sharepoint\.com/|1drv\.ms/|onedrive\.live\.com/)", re.IGNORECASE),
        ("m365_download_drive_file",),
    ),
)


def autoload_for_message(agent, text: str) -> List[str]:
    """Load the deferred tools that a link in ``text`` calls for.

    Returns the names newly loaded by this call (tools that were already
    callable are not reported). No-op without an active bridge, without a
    matching link, or when the tools are not registered in this session.
    """
    if not text or not isinstance(text, str) or not tool_search_active(agent):
        return []
    before = set(_valid_names(agent))
    loaded: List[str] = []
    for pattern, suffixes in _MESSAGE_AUTOLOAD_RULES:
        if not pattern.search(text):
            continue
        for suffix in suffixes:
            try:
                name = load_deferred_tool(agent, suffix, reason="message_link", enforce_cap=True)
            except Exception as exc:
                logger.debug("message-link autoload of %s failed: %s", suffix, exc)
                continue
            if name and name not in before and name not in loaded:
                loaded.append(name)
    if loaded:
        logger.info("[AIS-289] autoloaded %s for message link", ", ".join(loaded))
    return loaded


# ---------------------------------------------------------------------------
# Conversation-loop hook: unknown tool name → maybe a deferred one
# ---------------------------------------------------------------------------


def resolve_unknown_tool_name(agent, name: str) -> Optional[str]:
    """Called by the conversation loop before it rejects an unknown name.

    Order: a deferred tool in scope is loaded (and the canonical name
    returned); otherwise the existing fuzzy repair runs. ``None`` means the
    caller should reject the call.
    """
    loaded = load_deferred_tool(agent, name, reason="direct")
    if loaded:
        return loaded
    repair = getattr(agent, "_repair_tool_call", None)
    if callable(repair):
        try:
            repaired = repair(name)
        except Exception:
            repaired = None
        if repaired:
            return repaired
    return None


def unknown_tool_hint(agent) -> str:
    """Suffix for the 'does not exist' error when the bridge is active."""
    if not tool_search_active(agent):
        return ""
    return (
        " Deferred tools must be found via tool_search(query='...'); tools it "
        "returns are added to your tool list and can then be called directly."
    )


# ---------------------------------------------------------------------------
# Server-side skills (memory_context.suggested_skills)
# ---------------------------------------------------------------------------


def _is_memory_context_tool(name: str) -> bool:
    return name == "memory_context" or name.endswith("_memory_context")


def _skill_read_tool_name(agent) -> Optional[str]:
    try:
        from agent.prompt_builder import _resolve_memory_tool_name

        return _resolve_memory_tool_name(_valid_names(agent), "skill")
    except Exception:
        return None


def remember_mcp_skills(agent, function_name: str, function_result: Any) -> int:
    """Capture ``suggested_skills`` from a memory_context result on the agent.

    Returns the number of skills remembered (0 when the result carried none).
    """
    if not _is_memory_context_tool(function_name) or not isinstance(function_result, str):
        return 0
    try:
        payload = json.loads(function_result)
    except Exception:
        return 0
    raw = payload.get("suggested_skills") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return 0
    read_tool = _skill_read_tool_name(agent)
    skills: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or item.get("name") or "").strip()
        name = str(item.get("name") or slug).strip()
        if not slug or not name:
            continue
        how = (
            f"{read_tool}(action='read', slug='{slug}')" if read_tool
            else f"read skill '{slug}' with the memory server's skill tool"
        )
        skills.append({
            "name": name,
            "slug": slug,
            "description": str(item.get("description") or ""),
            "category": str(item.get("category") or ""),
            "updated_at": str(item.get("hash") or ""),
            "kind": "mcp_skill",
            "how_to_use": how,
        })
    try:
        setattr(agent, _MCP_SKILLS_ATTR, skills)
    except Exception:
        return 0
    return len(skills)


def mcp_skills(agent) -> List[Dict[str, Any]]:
    skills = getattr(agent, _MCP_SKILLS_ATTR, None)
    return list(skills) if isinstance(skills, list) else []


def _mcp_skill_hits(agent, query: str, kind: Optional[str]) -> List[Dict[str, Any]]:
    """Rank the server's skills against the query with the catalog scorer."""
    if kind == "tool":
        return []
    skills = mcp_skills(agent)
    if not skills or not query:
        return []
    try:
        from tools.tool_search import _format_search_hit, build_catalog, search_catalog

        catalog = build_catalog([], skills=skills)
        hits = search_catalog(catalog, query, limit=MCP_SKILL_HIT_CAP)
    except Exception as exc:
        logger.debug("mcp skill ranking failed: %s", exc)
        return []
    return [_format_search_hit(h) for h in hits]


# ---------------------------------------------------------------------------
# Bridge results: load what search/describe surfaced
# ---------------------------------------------------------------------------


def _compact_arguments(parameters: Any) -> Dict[str, str]:
    props = (parameters or {}).get("properties") if isinstance(parameters, dict) else None
    if not isinstance(props, dict):
        return {}
    out: Dict[str, str] = {}
    for key, spec in list(props.items())[:20]:
        typ = spec.get("type") if isinstance(spec, dict) else None
        out[str(key)] = str(typ or "any")
    return out


def _finding_notes(agent, name: str) -> str:
    """What earlier sessions learned about this tool (agent/tool_findings.py)."""
    try:
        from agent.tool_findings import finding_notes_for

        return finding_notes_for(agent, name)
    except Exception:
        return ""


def _status_for(agent, name: str) -> str:
    if name in _valid_names(agent):
        return "loaded"
    return "call tool_describe(name) to load, or tool_call(name, arguments)"


def absorb_bridge_result(agent, function_name: str, function_args: Any, function_result: Any) -> Any:
    """Load tools a bridge result surfaced and annotate the result for the model.

    ``tool_describe`` → the tool is loaded; the result drops the schema
    (it now sits in the tools array) and says so.
    ``tool_search`` → the hits listed under ``autoload`` are loaded (cap
    applies); every tool hit gets a ``status``.
    Anything else passes through untouched.
    """
    if _is_memory_context_tool(function_name):
        remember_mcp_skills(agent, function_name, function_result)
        return function_result
    try:
        from tools.tool_search import TOOL_DESCRIBE_NAME, TOOL_SEARCH_NAME
    except Exception:
        return function_result
    if function_name not in (TOOL_DESCRIBE_NAME, TOOL_SEARCH_NAME):
        return function_result
    if not isinstance(function_result, str):
        return function_result
    try:
        payload = json.loads(function_result)
    except Exception:
        return function_result
    if not isinstance(payload, dict) or payload.get("error"):
        return function_result

    if function_name == TOOL_DESCRIBE_NAME:
        name = str(payload.get("name") or "")
        loaded = load_deferred_tool(agent, name, reason="describe") if name else None
        if not loaded:
            return function_result
        rewritten = {
            "name": loaded,
            "kind": "tool",
            "status": "loaded",
            "description": payload.get("description", ""),
            "arguments": _compact_arguments(payload.get("parameters")),
            "required": list((payload.get("parameters") or {}).get("required") or []),
            "usage_hint": (
                f"{loaded} is now in your tools list with its full schema — "
                "call it directly by that name."
            ),
        }
        notes = _finding_notes(agent, loaded)
        if notes:
            rewritten["notes"] = notes
        return json.dumps(rewritten, ensure_ascii=False)

    # tool_search
    for name in payload.pop("autoload", None) or []:
        if isinstance(name, str):
            load_deferred_tool(agent, name, reason="search", enforce_cap=True)
    matches = payload.get("matches")
    if isinstance(matches, list):
        for hit in matches:
            if not isinstance(hit, dict):
                continue
            if hit.get("kind", "tool") != "tool":
                continue
            hit_name = str(hit.get("name") or "")
            hit["status"] = _status_for(agent, hit_name)
            if hit["status"] == "loaded":
                notes = _finding_notes(agent, hit_name)
                if notes:
                    hit["notes"] = notes
        # Server-side skills are ranked here (the dispatcher has no session
        # in hand) and listed first: on a name collision with a local skill
        # the server's copy is the maintained one.
        query = str((function_args or {}).get("query") or payload.get("query") or "") if isinstance(function_args, dict) else str(payload.get("query") or "")
        kind = str((function_args or {}).get("kind") or "").lower() or None if isinstance(function_args, dict) else None
        server_hits = _mcp_skill_hits(agent, query, kind)
        if server_hits:
            payload["matches"] = server_hits + matches
    return json.dumps(payload, ensure_ascii=False)


__all__ = [
    "MAX_AUTOLOADED_TOOLS",
    "absorb_bridge_result",
    "apply_tool_definitions",
    "ensure_loaded_tools_current",
    "is_loaded",
    "load_deferred_tool",
    "mcp_skills",
    "remember_mcp_skills",
    "resolve_unknown_tool_name",
    "scoped_defs_map",
    "scoped_deferrable_names",
    "tool_search_active",
    "unknown_tool_hint",
]
