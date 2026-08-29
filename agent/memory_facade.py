"""One door for every memory writer: the MCP vault, or the Obsidian vault.

Eight things in this codebase used to write "memory", each choosing its own
backend: the model (local ``memory`` tool or MCP ``memory_save``), compaction
(nothing), session end (nothing), cron findings (``capture_durable_topic``),
the background review fork (local tool only), the LLM extractor (MCP + local
mirror), the dual-write mirror, and the onboarding flow. The result was a
2,200-character local file at its limit while the vault was primary on paper.

``MemoryFacade`` is the single decision "where does this go?":

* ``mcp``   — the primary memory server's tools are in the session
              (``get_primary_mcp_server_name`` + a resolvable ``memory_context``).
              Calls go through ``run_agent.handle_function_call`` like a model
              call would, so hooks and the read-cache mirror apply.
* ``vault`` — no memory MCP, but the Obsidian workspace exists. Facts are
              written as markdown with frontmatter that ``_conventions.md``
              prescribes (``type``, ``title``, ``created``, ``updated``,
              ``tags``) into the folder the type belongs to, and indexed in
              ``VaultMetaIndex`` so search works offline.
* ``none``  — neither. Callers fall back to whatever local store they have.

The facade never picks a *custom* memory server: only the configured primary
server counts as the MCP backend. A failed MCP write falls through to the
vault so nothing is lost when the LiteLLM catalog changes under a session.

Config: ``memory.backend`` (``auto`` | ``mcp`` | ``vault``) forces a mode;
``memory.session_summary_min_turns`` gates the session-end summary.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agent.memory_facade")

MODE_MCP = "mcp"
MODE_VAULT = "vault"
MODE_NONE = "none"

# Vault folder per memory type (see installer/workspace-template/AGENTS.md).
_VAULT_FOLDERS = {
    "session": "journal/sessions",
    "decision": "decisions",
    "profile": "users",
    "person": "contacts",
    "project": "projects",
    "rule": "knowledge",
    "reference": "knowledge",
    "tool": "knowledge/tools",
    "notes": "knowledge",
}
_WORKSPACE_MARKERS = (".workspace-template-version", "_conventions.md", "AGENTS.md")


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return slug[:80] or f"note-{uuid.uuid4().hex[:8]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------


def _memory_backend_config() -> Dict[str, Any]:
    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
        mem = cfg.get("memory") if isinstance(cfg, dict) else None
        return mem if isinstance(mem, dict) else {}
    except Exception:
        return {}


def workspace_root() -> Optional[Path]:
    """The Obsidian workspace, or None when the cwd is not a shipped workspace."""
    try:
        from agent.runtime_cwd import resolve_agent_cwd

        root = Path(resolve_agent_cwd()).expanduser()
    except Exception:
        return None
    if not root.is_dir():
        return None
    if any((root / marker).exists() for marker in _WORKSPACE_MARKERS):
        return root
    return None


def primary_memory_context_registered() -> Optional[str]:
    """Registered ``memory_context`` tool name of the primary server, if any."""
    try:
        from hermes_cli.config import get_primary_mcp_server_name
        from tools.registry import registry

        primary = str(get_primary_mcp_server_name() or "").strip()
        if not primary:
            return None
        prefix = f"mcp_{primary}_"
        for entry in registry._snapshot_entries():
            if entry.name.startswith(prefix) and entry.name.endswith("_memory_context"):
                return entry.name
    except Exception:
        return None
    return None


def resolve_mode(valid_tool_names: Optional[set] = None) -> str:
    """Decide the backend for a session (or, without tool names, for the process)."""
    forced = str(_memory_backend_config().get("backend") or "auto").strip().lower()
    names = set(valid_tool_names or set())

    mcp_tool = None
    if names:
        try:
            from agent.prompt_builder import _resolve_memory_context_tool_name

            mcp_tool = _resolve_memory_context_tool_name(names)
        except Exception:
            mcp_tool = None
    else:
        mcp_tool = primary_memory_context_registered()

    has_vault = workspace_root() is not None
    if forced == MODE_MCP:
        return MODE_MCP if mcp_tool else (MODE_VAULT if has_vault else MODE_NONE)
    if forced == MODE_VAULT:
        return MODE_VAULT if has_vault else MODE_NONE
    if mcp_tool:
        return MODE_MCP
    if has_vault:
        return MODE_VAULT
    return MODE_NONE


# ---------------------------------------------------------------------------
# The facade
# ---------------------------------------------------------------------------


@dataclass
class SaveResult:
    ok: bool
    backend: str
    ref: str = ""  # slug (mcp) or vault-relative path (vault)
    error: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "backend": self.backend, "ref": self.ref, "error": self.error}


@dataclass
class MemoryFacade:
    mode: str
    agent: Any = None
    valid_tool_names: set = field(default_factory=set)

    # ---- construction ----------------------------------------------------

    @classmethod
    def for_agent(cls, agent) -> "MemoryFacade":
        names = set(getattr(agent, "valid_tool_names", None) or [])
        cached = getattr(agent, "_memory_facade", None)
        if isinstance(cached, MemoryFacade) and cached.valid_tool_names == names:
            return cached
        facade = cls(mode=resolve_mode(names), agent=agent, valid_tool_names=names)
        try:
            agent._memory_facade = facade
        except Exception:
            pass
        return facade

    @classmethod
    def for_process(cls) -> "MemoryFacade":
        """A facade without a session (cron findings, CLI): registry-based."""
        names: set = set()
        mcp_tool = primary_memory_context_registered()
        if mcp_tool:
            prefix = mcp_tool[: -len("memory_context")]
            try:
                from tools.registry import registry

                names = {e.name for e in registry._snapshot_entries() if e.name.startswith(prefix)}
            except Exception:
                names = {mcp_tool}
        return cls(mode=resolve_mode(names) if names else resolve_mode(None), agent=None, valid_tool_names=names)

    # ---- tool resolution -------------------------------------------------

    def _tool(self, suffix: str) -> Optional[str]:
        if self.mode != MODE_MCP:
            return None
        try:
            from agent.prompt_builder import _resolve_memory_tool_name

            return _resolve_memory_tool_name(self.valid_tool_names, suffix)
        except Exception:
            return None

    @property
    def save_tool(self) -> Optional[str]:
        return self._tool("memory_save")

    def _call(self, tool_name: str, args: Dict[str, Any]) -> Any:
        import run_agent as _ra

        agent = self.agent
        return _ra.handle_function_call(
            tool_name,
            dict(args),
            str(getattr(agent, "_current_task_id", "") or ""),
            tool_call_id=f"memory-facade-{uuid.uuid4().hex[:10]}",
            session_id=str(getattr(agent, "session_id", "") or ""),
            turn_id=str(getattr(agent, "_current_turn_id", "") or ""),
            api_request_id="",
            enabled_tools=list(self.valid_tool_names) or None,
            skip_pre_tool_call_hook=True,
            skip_tool_request_middleware=True,
            enabled_toolsets=getattr(agent, "enabled_toolsets", None),
            disabled_toolsets=getattr(agent, "disabled_toolsets", None),
            tool_request_middleware_trace=[],
        )

    @staticmethod
    def _ok(result: Any) -> bool:
        try:
            from agent.memory_dual_write import tool_result_indicates_success

            return tool_result_indicates_success(result)
        except Exception:
            return bool(result)

    @staticmethod
    def _slug_from(result: Any) -> str:
        try:
            payload = json.loads(str(result))
            if isinstance(payload, dict):
                return str(payload.get("slug") or "")
        except Exception:
            pass
        return ""

    # ---- save ------------------------------------------------------------

    def save(
        self,
        *,
        title: str,
        content: str,
        type: str = "notes",
        tags: Optional[List[str]] = None,
        scope: str = "project",
        priority: Optional[int] = None,
    ) -> SaveResult:
        title = str(title or "").strip()
        content = str(content or "").strip()
        if not title or not content:
            return SaveResult(False, self.mode, error="missing title or content")
        tags = [str(t).strip() for t in (tags or []) if str(t).strip()]

        if self.mode == MODE_MCP and self.save_tool:
            args: Dict[str, Any] = {"title": title, "content": content, "type": type, "tags": tags}
            if priority is not None:
                args["priority"] = int(priority)
            try:
                result = self._call(self.save_tool, args)
                if self._ok(result):
                    return SaveResult(True, MODE_MCP, ref=self._slug_from(result))
                logger.warning("memory_facade: MCP save rejected (%s); writing to the vault instead", str(result)[:160])
            except Exception as exc:
                logger.warning("memory_facade: MCP save failed (%s); writing to the vault instead", exc)
            if workspace_root() is None:
                return SaveResult(False, MODE_MCP, error="MCP save failed and no vault to fall back to")
        if workspace_root() is None:
            return SaveResult(False, MODE_NONE, error="no memory backend available")
        return self._vault_save(title=title, content=content, type=type, tags=tags, scope=scope)

    def _vault_save(self, *, title: str, content: str, type: str, tags: List[str], scope: str) -> SaveResult:
        root = workspace_root()
        assert root is not None
        folder = _VAULT_FOLDERS.get(type, "knowledge")
        slug = _slugify(title)
        path = root / folder / f"{slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        created = _now_iso()
        if path.exists():
            try:
                m = re.search(r"^created:\s*(\S+)", path.read_text(encoding="utf-8"), re.M)
                if m:
                    created = m.group(1)
            except Exception:
                pass
        tag_str = ", ".join(tags)
        text = (
            "---\n"
            f"type: {type}\n"
            f"title: {json.dumps(title, ensure_ascii=False)}\n"
            f"created: {created}\n"
            f"updated: {_now_iso()}\n"
            f"tags: [{tag_str}]\n"
            f"scope: {scope}\n"
            "source: memory-facade\n"
            "---\n\n"
            f"# {title}\n\n{content}\n"
        )
        try:
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            return SaveResult(False, MODE_VAULT, error=str(exc))
        rel = str(path.relative_to(root))
        try:
            from agent.memory_vault_index import VaultMetaIndex

            VaultMetaIndex().sync_record({
                "id": f"vault:{slug}",
                "slug": slug,
                "path": str(path),
                "scope": scope,
                "type": type,
                "title": title,
                "content": content,
                "tags": tags,
                "updated_at": int(time.time()),
                "source": "vault",
            })
        except Exception as exc:
            logger.debug("memory_facade: vault index update failed: %s", exc)
        return SaveResult(True, MODE_VAULT, ref=rel)

    # ---- search / read ---------------------------------------------------

    def search(self, query: str, *, limit: int = 5) -> List[Dict[str, Any]]:
        query = str(query or "").strip()
        if not query:
            return []
        if self.mode == MODE_MCP:
            tool = self._tool("memory_search")
            if tool:
                try:
                    result = self._call(tool, {"query": query, "limit": limit})
                    payload = json.loads(str(result))
                    items = payload.get("results") if isinstance(payload, dict) else payload
                    if isinstance(items, list):
                        return [i for i in items if isinstance(i, dict)][:limit]
                except Exception as exc:
                    logger.debug("memory_facade: MCP search failed: %s", exc)
        if workspace_root() is None:
            return []
        try:
            from agent.memory_vault_index import VaultMetaIndex

            index = VaultMetaIndex()
            rows = index.hybrid_search(query, top_k=limit)
            return [
                {
                    "title": r.get("title", ""),
                    "content": (r.get("content") or "")[:600],
                    "type": r.get("type", ""),
                    "slug": r.get("slug", ""),
                    "path": r.get("path", ""),
                }
                for r in rows
            ]
        except Exception as exc:
            logger.debug("memory_facade: vault search failed: %s", exc)
            return []

    def read(self, ref: str) -> Optional[str]:
        ref = str(ref or "").strip()
        if not ref:
            return None
        if self.mode == MODE_MCP:
            tool = self._tool("memory_read")
            if tool:
                try:
                    return str(self._call(tool, {"slug": ref}))
                except Exception as exc:
                    logger.debug("memory_facade: MCP read failed: %s", exc)
        root = workspace_root()
        if root is None:
            return None
        candidate = (root / ref) if not Path(ref).is_absolute() else Path(ref)
        if candidate.is_file():
            try:
                return candidate.read_text(encoding="utf-8")
            except OSError:
                return None
        for folder in set(_VAULT_FOLDERS.values()):
            p = root / folder / f"{_slugify(ref)}.md"
            if p.is_file():
                return p.read_text(encoding="utf-8")
        return None

    # ---- session lifecycle -----------------------------------------------

    def context(self, query: str = "") -> Optional[str]:
        if self.mode != MODE_MCP:
            return None
        tool = self._tool("memory_context")
        if not tool:
            return None
        try:
            return str(self._call(tool, {"query": query} if query else {}))
        except Exception as exc:
            logger.debug("memory_facade: memory_context failed: %s", exc)
            return None

    def summarize_session(
        self,
        *,
        summary: str,
        decisions: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        session_id: str = "",
    ) -> SaveResult:
        summary = str(summary or "").strip()
        if not summary:
            return SaveResult(False, self.mode, error="empty summary")
        decisions = [str(d).strip() for d in (decisions or []) if str(d).strip()]
        tags = [str(t).strip() for t in (tags or []) if str(t).strip()]
        if self.mode == MODE_MCP:
            tool = self._tool("memory_summarize_session")
            if tool:
                try:
                    result = self._call(tool, {"summary": summary, "decisions": decisions, "tags": tags})
                    if self._ok(result):
                        return SaveResult(True, MODE_MCP, ref=self._slug_from(result))
                except Exception as exc:
                    logger.warning("memory_facade: memory_summarize_session failed (%s); saving as a session note", exc)
        body = summary
        if decisions:
            body += "\n\n## Decisions\n" + "\n".join(f"- {d}" for d in decisions)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        return self.save(
            title=f"Session {session_id or stamp}",
            content=body,
            type="session",
            tags=["session", *tags],
            scope="project",
        )

    # ---- prompt text -----------------------------------------------------

    def describe_for_prompt(self) -> str:
        if self.mode == MODE_MCP:
            return f"memory vault = the MCP memory tools (`{self.save_tool or 'memory_save'}` …)"
        if self.mode == MODE_VAULT:
            return f"memory vault = the Obsidian workspace at `{workspace_root()}` via `vault_memory`"
        return "no memory vault in this session"


# ---------------------------------------------------------------------------
# Session-end summary (the safety net behind SESSION CLOSE in the prompt)
# ---------------------------------------------------------------------------

_SUMMARY_PROMPT = (
    "Summarize the conversation below for a memory vault entry. Return JSON with keys "
    "\"summary\" (3-6 sentences: what was asked, what was done, what remains), "
    "\"decisions\" (list of short strings; decisions taken or preferences stated — empty if none) and "
    "\"tags\" (3-6 short lowercase tags). Facts only, no praise, no plans you did not execute. "
    "Write in the language the user used.\n\n"
)


def _count_user_turns(messages: List[Dict[str, Any]]) -> int:
    return sum(1 for m in messages or [] if isinstance(m, dict) and m.get("role") == "user")


def _transcript_excerpt(messages: List[Dict[str, Any]], max_chars: int = 24000) -> str:
    parts: List[str] = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content")
        if isinstance(content, list):
            content = " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
        text = str(content or "").strip()
        if not text:
            continue
        parts.append(f"{role}: {text[:1500]}")
    joined = "\n".join(parts)
    return joined[-max_chars:]


def summarize_session_into_memory(agent, messages: List[Dict[str, Any]], *, reason: str) -> Optional[SaveResult]:
    """Write a session summary into the vault once per session, if it earned one."""
    if getattr(agent, "_session_summarized", False):
        return None
    cfg = _memory_backend_config()
    try:
        min_turns = int(cfg.get("session_summary_min_turns", 4))
    except (TypeError, ValueError):
        min_turns = 4
    if _count_user_turns(messages) < max(1, min_turns):
        return None
    facade = MemoryFacade.for_agent(agent)
    if facade.mode == MODE_NONE:
        return None

    excerpt = _transcript_excerpt(messages)
    if not excerpt:
        return None
    summary, decisions, tags = "", [], []
    try:
        from agent.auxiliary_client import call_llm

        response = call_llm(
            task="compression",
            messages=[{"role": "user", "content": _SUMMARY_PROMPT + excerpt}],
            max_tokens=700,
        )
        raw = response.choices[0].message.content
        raw = raw if isinstance(raw, str) else str(raw or "")
        m = re.search(r"\{.*\}", raw, re.S)
        payload = json.loads(m.group(0)) if m else {}
        summary = str(payload.get("summary") or "").strip()
        decisions = [str(d) for d in payload.get("decisions") or [] if str(d).strip()]
        tags = [str(t) for t in payload.get("tags") or [] if str(t).strip()]
    except Exception as exc:
        logger.debug("memory_facade: session summary LLM call failed: %s", exc)
    if not summary:
        return None
    try:
        agent._session_summarized = True
    except Exception:
        pass
    session_id = str(getattr(agent, "session_id", "") or "")
    result = facade.summarize_session(
        summary=summary, decisions=decisions, tags=[reason, *tags], session_id=session_id,
    )
    logger.info("memory_facade: session %s summarized into %s (%s)", session_id, result.backend, reason)
    return result


__all__ = [
    "MODE_MCP",
    "MODE_NONE",
    "MODE_VAULT",
    "MemoryFacade",
    "SaveResult",
    "primary_memory_context_registered",
    "resolve_mode",
    "summarize_session_into_memory",
    "workspace_root",
]
