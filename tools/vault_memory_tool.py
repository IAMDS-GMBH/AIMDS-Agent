"""``vault_memory`` — the model's memory tool when no memory MCP is in the session.

Same argument shapes as the MCP memory tools (title / type / content / tags,
query, slug), backed by the Obsidian workspace through ``MemoryFacade``: one
markdown note per fact with frontmatter, indexed for search. It only appears
when the facade is in ``vault`` mode — with the MCP present, the MCP tools
themselves are the surface, and this one stays out of the tools array.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from tools.registry import registry, tool_error

MAX_CHOICES = 4


def check_vault_memory_requirements() -> bool:
    try:
        from agent.memory_facade import MODE_VAULT, resolve_mode

        return resolve_mode(None) == MODE_VAULT
    except Exception:
        return False


def vault_memory_tool(args: Dict[str, Any], **kw: Any) -> str:
    from agent.memory_facade import MemoryFacade, MODE_NONE

    action = str(args.get("action") or "").strip().lower()
    facade = MemoryFacade.for_process()
    if facade.mode == MODE_NONE:
        return tool_error("No memory vault is available (no workspace and no memory MCP).", success=False)

    if action == "save":
        res = facade.save(
            title=str(args.get("title") or ""),
            content=str(args.get("content") or ""),
            type=str(args.get("type") or "notes"),
            tags=list(args.get("tags") or []),
            scope=str(args.get("scope") or "project"),
        )
        payload = res.as_dict()
        payload["saved"] = res.ok
        return json.dumps(payload, ensure_ascii=False)

    if action == "search":
        limit = args.get("limit") or 5
        try:
            limit = max(1, min(20, int(limit)))
        except (TypeError, ValueError):
            limit = 5
        results = facade.search(str(args.get("query") or ""), limit=limit)
        return json.dumps({"results": results, "count": len(results)}, ensure_ascii=False)

    if action == "read":
        text = facade.read(str(args.get("slug") or args.get("path") or ""))
        if text is None:
            return tool_error("Not found in the vault.", success=False)
        return json.dumps({"content": text}, ensure_ascii=False)

    if action == "context":
        rows = facade.search(str(args.get("query") or "rules profile preferences"), limit=8)
        return json.dumps({"memories": rows, "note": "Vault mode: rules and profile live in the workspace notes."}, ensure_ascii=False)

    if action == "summarize_session":
        res = facade.summarize_session(
            summary=str(args.get("summary") or ""),
            decisions=list(args.get("decisions") or []),
            tags=list(args.get("tags") or []),
            session_id=str(kw.get("session_id") or ""),
        )
        return json.dumps(res.as_dict(), ensure_ascii=False)

    return tool_error("Unknown action. Use: save, search, read, context, summarize_session", success=False)


VAULT_MEMORY_SCHEMA = {
    "name": "vault_memory",
    "description": (
        "The memory vault for durable facts, rules, decisions, profile and session summaries — "
        "backed by the Obsidian workspace because no memory MCP is in this session. "
        "save: upsert one fact by title. search: find saved facts. read: one entry by slug or path. "
        "context: rules/profile relevant to a query. summarize_session: close out the session."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["save", "search", "read", "context", "summarize_session"],
            },
            "title": {"type": "string", "description": "save: descriptive title (the upsert key)."},
            "content": {"type": "string", "description": "save: the fact, in declarative form."},
            "type": {
                "type": "string",
                "description": "save: notes | rule | decision | profile | person | project | reference | tool | session",
            },
            "tags": {"type": "array", "items": {"type": "string"}},
            "query": {"type": "string", "description": "search/context: what to look for."},
            "slug": {"type": "string", "description": "read: slug or vault-relative path."},
            "summary": {"type": "string", "description": "summarize_session: 3-6 sentences."},
            "decisions": {"type": "array", "items": {"type": "string"}},
            "limit": {"type": "integer"},
        },
        "required": ["action"],
    },
}


registry.register(
    name="vault_memory",
    toolset="memory",
    schema=VAULT_MEMORY_SCHEMA,
    handler=vault_memory_tool,
    check_fn=check_vault_memory_requirements,
    emoji="🗄️",
)
