"""Progressive tool disclosure ("tool search") for Hermes Agent.

When enabled, MCP and non-core plugin tools are replaced in the model-visible
tools array by three bridge tools — ``tool_search``, ``tool_describe``,
``tool_call`` — and surfaced on demand. Core Hermes tools never defer.

Design constraints this module is built around (see ``openclaw-tool-search-report``
for the full rationale):

* Core tools defined in ``toolsets._HERMES_CORE_TOOLS`` are *never* deferred.
  Always-load means always-load. No exceptions.
* The threshold gate runs every assembly: when deferrable tools would consume
  less than ``threshold_pct`` of the model's context window (default 10%),
  tool search is a no-op and the tools array passes through unchanged.
* The catalog is stateless across turns and tools-array assemblies. It is
  rebuilt from the current tool-defs list every time. This is the lesson
  from OpenClaw's cron regression (openclaw/openclaw#84141): a session-keyed
  catalog that drifts out of sync with the live tool registry produces
  silent tool dropouts.
* Bridge tools route through ``model_tools.handle_function_call`` exactly
  like a direct call, so guardrails, plugin pre/post hooks, approval flows,
  and tool-result truncation all fire identically.
* Display and trajectory unwrap is implemented here so the user (CLI activity
  feed, gateway, saved trajectories) always sees the underlying tool, not
  the bridge.
* The catalog is stateless, the *session* is not: ``agent/deferred_tools.py``
  keeps the set of deferred tools a session has loaded into its tools array
  (after a search, a describe, a tool_call, or a direct call by name) and
  re-validates that set against ``registry._generation`` before every API
  call. Statelessness here and per-agent state there are two different
  things; the cron regression above was about the former.
* The catalog holds tools *and skills*. Skills are ``kind="skill"`` entries
  with ``how_to_use`` (``skill_view(name=…)``); the bridge returns them
  alongside tools so the model has one search for "what can help here?".
* Always-visible memory tools are derived from the configured primary
  memory server (``get_primary_mcp_server_name``) and a short list of
  bootstrap suffixes — never from a hardcoded server or tool list. Any
  other server, including a custom memory MCP, is an ordinary deferrable
  source.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("tools.tool_search")


# Bridge tool names. These names are reserved and may not collide with a
# user/plugin/MCP tool — registration of any tool with these names is
# rejected by the registry's existing override-protection logic.
TOOL_SEARCH_NAME = "tool_search"
TOOL_DESCRIBE_NAME = "tool_describe"
TOOL_CALL_NAME = "tool_call"

BRIDGE_TOOL_NAMES = frozenset({TOOL_SEARCH_NAME, TOOL_DESCRIBE_NAME, TOOL_CALL_NAME})

# When estimating tokens from char count without a real tokenizer, this is
# the cheap rule of thumb that's stable across providers. Roughly 4 chars
# per token for English+JSON. Underestimating leads to false negatives
# (tool search not activated when it should); overestimating leads to false
# positives (activated when not needed). 4.0 errs slightly toward
# underestimating, which is the safer default.
CHARS_PER_TOKEN = 4.0


# ---------------------------------------------------------------------------
# Configuration plumbing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolSearchConfig:
    """Resolved, validated tool-search configuration for a single assembly."""

    enabled: str  # "auto" | "on" | "off"
    threshold_pct: float  # 0..100 — only used when enabled == "auto"
    search_default_limit: int
    max_search_limit: int
    # How many top-ranked tool hits a ``tool_search`` call loads into the
    # session's tools array (0 disables). See ``agent/deferred_tools.py``.
    autoload_top_n: int = 3

    @classmethod
    def from_raw(cls, raw: Any) -> "ToolSearchConfig":
        """Build a config from a raw dict / bool / None.

        Accepts the legacy bool shape (``tools.tool_search: true``) and the
        dict shape (``tools.tool_search: {enabled: auto, ...}``). Validates
        and clamps every numeric field; unknown values fall back to safe
        defaults rather than raising, so a typo in user config does not
        break the agent.
        """
        if raw is True:
            return cls(enabled="auto", threshold_pct=10.0,
                       search_default_limit=8, max_search_limit=20)
        if raw is False:
            return cls(enabled="off", threshold_pct=10.0,
                       search_default_limit=8, max_search_limit=20)
        if not isinstance(raw, dict):
            return cls(enabled="auto", threshold_pct=10.0,
                       search_default_limit=8, max_search_limit=20)

        enabled_raw = str(raw.get("enabled", "auto")).strip().lower()
        if enabled_raw in ("true", "1", "yes"):
            enabled = "on"
        elif enabled_raw in ("false", "0", "no"):
            enabled = "off"
        elif enabled_raw in ("auto", "on", "off"):
            enabled = enabled_raw
        else:
            enabled = "auto"

        threshold_pct = _safe_float(raw.get("threshold_pct"), 10.0)
        threshold_pct = max(0.0, min(100.0, threshold_pct))

        max_search_limit = max(1, min(50, _safe_int(raw.get("max_search_limit"), 20)))
        search_default_limit = max(1, min(max_search_limit,
                                          _safe_int(raw.get("search_default_limit"), 8)))

        autoload_top_n = max(0, min(max_search_limit, _safe_int(raw.get("autoload_top_n"), 3)))

        return cls(
            enabled=enabled,
            threshold_pct=threshold_pct,
            search_default_limit=search_default_limit,
            max_search_limit=max_search_limit,
            autoload_top_n=autoload_top_n,
        )


def _safe_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _safe_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def load_config() -> ToolSearchConfig:
    """Load tool-search config from the user config file."""
    try:
        from hermes_cli.config import load_config as _load
        cfg = _load() or {}
        tools_cfg = cfg.get("tools") if isinstance(cfg.get("tools"), dict) else {}
        if not isinstance(tools_cfg, dict):
            tools_cfg = {}
        return ToolSearchConfig.from_raw(tools_cfg.get("tool_search"))
    except Exception as e:
        logger.debug("Failed to load tool-search config: %s", e)
        return ToolSearchConfig.from_raw(None)


# ---------------------------------------------------------------------------
# Tool classification
# ---------------------------------------------------------------------------


def _core_tool_names() -> frozenset[str]:
    """Return the set of tool names that must NEVER be deferred.

    Imported lazily because ``toolsets`` imports from ``tools.registry``
    and we don't want a hard cycle.
    """
    try:
        from toolsets import _HERMES_CORE_TOOLS
        return frozenset(_HERMES_CORE_TOOLS)
    except Exception:
        return frozenset()


# Memory-server tools the session needs before it can search anything: the
# session-start context load, saving/reading/searching the vault, the
# server-side skill reader, and the session close-out. Everything else the
# server offers (list/manage/backlinks/meta/agent/transfer/kb_*/web_*) is
# discoverable through tool_search like any other tool.
BOOTSTRAP_MEMORY_SUFFIXES = frozenset({
    "memory_context",
    "memory_save",
    "memory_search",
    "memory_read",
    "skill",
    "memory_summarize_session",
})

_PRIMARY_SERVER_CACHE: "tuple | None" = None


def _primary_mcp_server_name() -> str:
    """Configured primary memory server, cached per registry generation.

    ``is_deferrable_tool_name`` runs once per tool per assembly; reading the
    config for every call would be ~100 config loads per assembly.
    """
    global _PRIMARY_SERVER_CACHE
    try:
        from tools.registry import registry as _registry

        generation = int(getattr(_registry, "_generation", 0) or 0)
    except Exception:
        generation = -1
    if _PRIMARY_SERVER_CACHE is not None and _PRIMARY_SERVER_CACHE[0] == generation:
        return _PRIMARY_SERVER_CACHE[1]
    try:
        from hermes_cli.config import get_primary_mcp_server_name

        primary = str(get_primary_mcp_server_name() or "").strip()
    except Exception:
        primary = ""
    _PRIMARY_SERVER_CACHE = (generation, primary)
    return primary


def is_bootstrap_memory_tool(name: str, primary_server: Optional[str] = None) -> bool:
    """True for a bootstrap memory tool of the *primary* server.

    Bare names (``memory_context`` …) are the memory-provider path and always
    count. Prefixed MCP names count only under ``mcp_{primary}_``; a second
    or custom memory server is deferrable like any other server.
    """
    if not name:
        return False
    if name in BOOTSTRAP_MEMORY_SUFFIXES:
        return True
    primary = (primary_server if primary_server is not None else _primary_mcp_server_name()).strip()
    if not primary or not name.startswith(f"mcp_{primary}_"):
        return False
    return any(name.endswith(f"_{suffix}") for suffix in BOOTSTRAP_MEMORY_SUFFIXES)


def is_deferrable_tool_name(name: str) -> bool:
    """Return True if a tool with this name is *eligible* for deferral.

    A tool is deferrable iff it is registered with an MCP toolset prefix
    OR it is not in ``_HERMES_CORE_TOOLS``. Core tools are never deferred
    even when their toolset is technically plugin-provided (this protects
    against accidental shadowing). The primary memory server's bootstrap
    tools (``BOOTSTRAP_MEMORY_SUFFIXES``) stay visible too — the session
    start relies on ``memory_context`` being callable before the first
    search.
    """
    if name in BRIDGE_TOOL_NAMES:
        return False
    if name in _core_tool_names():
        return False
    if is_bootstrap_memory_tool(name):
        return False
    # Check registry toolset for MCP prefix.
    try:
        from tools.registry import registry
        entry = registry.get_entry(name)
        if entry is None:
            return False
        if entry.toolset.startswith("mcp-"):
            return True
        # Non-MCP, non-core → plugin tool, eligible.
        return True
    except Exception:
        return False


def classify_tools(tool_defs: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split a tool-defs list into (visible, deferrable).

    ``visible`` retains every tool that must stay in the model-facing array:
    every core tool, plus any tool we can't classify. ``deferrable`` is the
    candidate set for catalog entry.
    """
    visible: List[Dict[str, Any]] = []
    deferrable: List[Dict[str, Any]] = []
    for td in tool_defs:
        fn = td.get("function") or {}
        name = fn.get("name", "")
        if name in BRIDGE_TOOL_NAMES:
            # Should never happen — bridge tools are added after classification —
            # but be defensive.
            continue
        if is_deferrable_tool_name(name):
            deferrable.append(td)
        else:
            visible.append(td)
    return visible, deferrable


# ---------------------------------------------------------------------------
# Token estimation and threshold gate
# ---------------------------------------------------------------------------


def estimate_tokens_from_schemas(tool_defs: Iterable[Dict[str, Any]]) -> int:
    """Estimate the token cost of a tool-defs list via the chars/4 rule.

    Cheap and stable across providers. The number doesn't need to be exact —
    it gates the activate/skip decision, and a typical 200K context with a
    10% threshold means the decision flips around 20K tokens of schema.
    Order-of-magnitude precision is fine.
    """
    total_chars = 0
    for td in tool_defs:
        try:
            total_chars += len(json.dumps(td, ensure_ascii=False, separators=(",", ":")))
        except (TypeError, ValueError):
            total_chars += len(str(td))
    return int(math.ceil(total_chars / CHARS_PER_TOKEN))


def should_activate(
    config: ToolSearchConfig,
    deferrable_tokens: int,
    context_length: Optional[int],
) -> bool:
    """Decide whether tool search should activate for the current assembly.

    ``"off"`` skips unconditionally. ``"on"`` activates unconditionally
    (as long as there is at least one deferrable tool — there's no point
    swapping a no-op). ``"auto"`` activates when the deferrable schemas
    would consume ``threshold_pct`` of context or more.
    """
    if config.enabled == "off":
        return False
    if deferrable_tokens <= 0:
        return False
    if config.enabled == "on":
        return True
    # auto
    if not context_length or context_length <= 0:
        # Without a known context size, fall back to an 8K-token cutoff
        return deferrable_tokens >= 8_000
    threshold_tokens = int(context_length * (config.threshold_pct / 100.0))
    # Cap auto threshold tokens at 8k so large-context models (e.g. 200k)
    # do not receive >8k tokens of tool schemas before activating tool_search.
    threshold_tokens = min(threshold_tokens, 8_000)
    return deferrable_tokens >= threshold_tokens


# ---------------------------------------------------------------------------
# Catalog + BM25 retrieval
# ---------------------------------------------------------------------------


@dataclass
class CatalogEntry:
    """One deferrable tool, in a form the bridge tools can search and serve."""

    name: str
    description: str
    schema: Dict[str, Any]  # The full {"type":"function", "function": {...}} entry.
    source: str  # "mcp" | "plugin" | "other" | "skill"
    source_name: str  # Toolset name, e.g. "mcp-github" or "kanban"; "skill:<category>" for skills
    # "tool" (callable after load) | "skill" (local, read with skill_view)
    # | "mcp_skill" (served by the memory MCP, read with its skill tool)
    kind: str = "tool"
    how_to_use: str = ""

    # Pre-tokenized fields for BM25.
    _tokens: List[str] = field(default_factory=list)
    _name_tokens: set[str] = field(default_factory=set)
    _source_tokens: set[str] = field(default_factory=set)
    # Precomputed at catalog-build time. `search_catalog` used to rebuild every
    # entry's vector on every single call; the catalog itself is already
    # rebuilt per invocation, so this was quadratic work for nothing.
    _vector: Dict[str, float] = field(default_factory=dict)
    # One shared dict across the whole catalog, so the query can be weighted
    # with the same corpus statistics as the entries.
    _idf: Dict[str, float] = field(default_factory=dict)


from hermes_text_vector import build_vector as _shared_build_vector
from hermes_text_vector import compute_idf as _shared_compute_idf
from hermes_text_vector import cosine as _shared_cosine

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _tokenize(text: str) -> List[str]:
    if not text:
        return []
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _split_words(text: str) -> str:
    """Break a tool/source identifier into space-separated words for BM25.

    Splits on `_`/`.`/`-`/`:` AND on camelCase boundaries — MCP server names
    are typically registered in PascalCase (e.g. "GithubMCP", "AtlassianMCP")
    with no separator, so without the camelCase split `_tokenize` collapses
    them into a single opaque token ("githubmcp") that a literal query like
    "github" can never match, silently hiding that server's tools from
    search results.
    """
    separated = text.replace("_", " ").replace(".", " ").replace("-", " ").replace(":", " ")
    return _CAMEL_BOUNDARY_RE.sub(" ", separated)


def _entry_search_text(td: Dict[str, Any], source_name: str = "") -> str:
    """Build the search-text blob for a deferrable tool.

    Includes the tool name (with underscores broken into words so BM25 can
    match against query terms), the source/toolset name, description, and
    parameter names. Schema bodies are excluded.
    """
    fn = td.get("function") or {}
    name = fn.get("name", "")
    desc = fn.get("description", "") or ""
    params = ((fn.get("parameters") or {}).get("properties") or {})
    param_names = " ".join(params.keys())
    # Break snake_case, dotted, dashed, and camelCase names into words for BM25.
    name_words = _split_words(name)
    source_words = _split_words(source_name)
    return f"{name_words} {name_words} {source_words} {desc} {param_names}"


def _classify_source(name: str) -> Tuple[str, str]:
    """Return (source_kind, source_name) for a registered tool name."""
    try:
        from tools.registry import registry
        entry = registry.get_entry(name)
        if entry is None:
            return ("other", "")
        if entry.toolset.startswith("mcp-"):
            return ("mcp", entry.toolset)
        return ("plugin", entry.toolset)
    except Exception:
        return ("other", "")


def _get_dynamic_mcp_keywords_map() -> Dict[str, List[str]]:
    try:
        from tools.mcp_tool import get_mcp_dynamic_keywords_map
        return get_mcp_dynamic_keywords_map()
    except Exception:
        return {}


_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "in", "on", "at", "to", "for", "of", "with", "by",
    "from", "is", "it", "this", "that", "use", "when", "as", "be", "are", "was", "were",
    "into", "out", "about", "all", "any", "some", "such", "than", "then", "their", "them",
})


def _get_dynamic_skill_keywords_map() -> Dict[str, List[str]]:
    """Dynamically extract semantic query term expansions from installed and bundled skills.

    Parses skill names, categories, tags, and descriptions to build reciprocal
    keyword mappings so queries match relevant skills semantically without needing
    manually maintained static synonym lists.
    """
    try:
        from tools.skills_tool import _find_all_skills
        # skip_disabled=True means "include disabled skills" (it skips the
        # disabled *check*). Query expansion must not learn from skills the
        # user switched off.
        skills = _find_all_skills(skip_disabled=False)
    except Exception:
        return {}

    mapping: Dict[str, set[str]] = {}

    def _add_link(term: str, target: str) -> None:
        t = term.lower().strip()
        tg = target.lower().strip()
        if len(t) >= 3 and len(tg) >= 3 and t != tg and t not in _STOP_WORDS and tg not in _STOP_WORDS:
            mapping.setdefault(t, set()).add(tg)

    for s in skills:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name") or "").strip()
        if not name:
            continue

        category = str(s.get("category") or "").strip()
        desc = str(s.get("description") or "").strip()
        tags = s.get("tags") or []
        tag_tokens = [str(t).strip().lower() for t in tags if str(t).strip() and str(t).strip().lower() not in _STOP_WORDS] if isinstance(tags, list) else [str(tags).strip().lower()]

        name_parts = [p for p in re.split(r"[_\-/\s]+", name.lower()) if len(p) >= 3 and p not in _STOP_WORDS and p not in _GENERIC_SEARCH_TERMS]
        desc_tokens = [t for t in _tokenize(desc) if t not in _STOP_WORDS and t not in _GENERIC_SEARCH_TERMS]
        cat_tokens = [t for t in _tokenize(category) if t not in _STOP_WORDS and t not in _GENERIC_SEARCH_TERMS]

        all_tokens = set(name_parts) | set(tag_tokens) | set(cat_tokens)

        for name_p in name_parts:
            for tok in all_tokens:
                _add_link(tok, name_p)
                _add_link(name_p, tok)
            for desc_t in desc_tokens:
                _add_link(desc_t, name_p)

    return {k: list(v) for k, v in mapping.items()}


def _get_mcp_server_metadata() -> Dict[str, Dict[str, Any]]:
    try:
        from tools.mcp_tool import get_mcp_server_metadata
        return get_mcp_server_metadata()
    except Exception:
        return {}


SKILL_HIT_CAP = 2  # skill hits per search unless the query asks for skills


def local_skill_catalog_entries() -> List[Dict[str, Any]]:
    """Installed skills as catalog input (``kind="skill"``), disabled ones excluded."""
    try:
        from tools.skills_tool import _find_all_skills

        skills = _find_all_skills(skip_disabled=False, include_source=True)
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for sk in skills:
        if not isinstance(sk, dict) or not sk.get("name"):
            continue
        name = str(sk["name"])
        out.append({
            "name": name,
            "description": str(sk.get("description") or ""),
            "category": str(sk.get("category") or ""),
            "tags": sk.get("tags") or [],
            "updated_at": sk.get("updated_at", 0),
            "kind": "skill",
            "how_to_use": f"skill_view(name='{name}')",
        })
    return out


def _skill_catalog_entry(sk: Dict[str, Any]) -> Tuple[CatalogEntry, str]:
    name = str(sk.get("name") or "")
    desc = str(sk.get("description") or "")
    category = str(sk.get("category") or "")
    tags = sk.get("tags") or []
    tag_blob = " ".join(str(t) for t in tags) if isinstance(tags, list) else str(tags)
    name_words = _split_words(name)
    cat_words = _split_words(category)
    kind = str(sk.get("kind") or "skill")
    entry = CatalogEntry(
        name=name,
        description=desc,
        schema={},
        source="skill",
        source_name=f"skill:{category}" if category else "skill",
        kind=kind,
        how_to_use=str(sk.get("how_to_use") or f"skill_view(name='{name}')"),
        _tokens=_tokenize(f"{name_words} {name_words} {cat_words} {desc} {tag_blob}"),
        _name_tokens=set(_tokenize(name_words)),
        _source_tokens={"skill", "skills"} | set(_tokenize(cat_words)),
    )
    return entry, f"{name_words} {name_words} {cat_words} {desc}"


def build_catalog(
    tool_defs: List[Dict[str, Any]],
    skills: Optional[List[Dict[str, Any]]] = None,
) -> List[CatalogEntry]:
    """Build the catalog from a tool-defs list plus optional skill dicts.

    Caller is expected to pass only the deferrable subset (``classify_tools``
    returns it as the second element). ``skills`` are dicts with ``name``,
    ``description``, ``category`` and optionally ``tags``/``kind``/
    ``how_to_use`` (see ``local_skill_catalog_entries``). Tools and skills
    share one IDF corpus so a query is weighted the same against both.
    """
    catalog: List[CatalogEntry] = []
    mcp_meta = _get_mcp_server_metadata()
    # Two passes: the IDF weights need the whole corpus before any single
    # entry can be vectorized against it.
    entry_texts: List[str] = []

    for td in tool_defs:
        fn = td.get("function") or {}
        name = fn.get("name", "")
        if not name:
            continue
        desc = fn.get("description", "") or ""
        source, source_name = _classify_source(name)
        # `_split_words`, not a hand-rolled replace chain: it also breaks
        # camelCase. Without that, `mcp_TempoMCP_retrieveWorklogs` tokenizes to
        # {mcp, tempomcp, retrieveworklogs} — so a query for "tempo" or
        # "worklog" matches neither the name nor the source, the +5 name boost
        # never fires, and the false-positive filter below drops the entry
        # entirely. The server then looks absent even though it is connected
        # and registered.
        name_words = _split_words(name)
        source_words = _split_words(source_name)

        extra_mcp_tokens: List[str] = []
        if source == "mcp":
            server_raw = source_name.removeprefix("mcp-")
            if server_raw in mcp_meta:
                extra_mcp_tokens = mcp_meta[server_raw].get("keywords", [])

        extra_blob = " ".join(extra_mcp_tokens)
        search_blob = f"{_entry_search_text(td, source_name)} {extra_blob}".strip()

        entry = CatalogEntry(
            name=name,
            description=desc,
            schema=td,
            source=source,
            source_name=source_name,
            _tokens=_tokenize(search_blob),
            _name_tokens=set(_tokenize(name_words)),
            _source_tokens=set(_tokenize(f"{source_words} {extra_blob}")),
        )
        catalog.append(entry)
        # Name twice, mirroring `_entry_search_text`: cosine on unit vectors
        # favours short documents, so a tool that carries a real description
        # would otherwise rank below its undocumented siblings. Doubling the
        # most reliable signal offsets that.
        entry_texts.append(f"{name_words} {name_words} {source_words} {desc}")

    for sk in skills or []:
        if not isinstance(sk, dict) or not sk.get("name"):
            continue
        entry, text = _skill_catalog_entry(sk)
        catalog.append(entry)
        entry_texts.append(text)

    idf = _shared_compute_idf(entry_texts)
    for entry, text in zip(catalog, entry_texts):
        entry._idf = idf
        entry._vector = _build_vector(text, idf)

    return catalog


def _bm25_score(query_tokens: List[str], doc_tokens: List[str],
                doc_lengths: List[int], avg_dl: float,
                doc_freq: Dict[str, int], n_docs: int,
                k1: float = 1.5, b: float = 0.75) -> float:
    """Standard BM25 score for one query against one document.

    Inlined small implementation rather than adding a dependency. Performance
    is fine — the catalog is bounded by N (tools) typically < 500, and we
    score against the in-memory tokens list.
    """
    if not doc_tokens:
        return 0.0
    score = 0.0
    dl = len(doc_tokens)
    # Pre-count tokens in the doc.
    doc_tf: Dict[str, int] = {}
    for t in doc_tokens:
        doc_tf[t] = doc_tf.get(t, 0) + 1
    for q in query_tokens:
        df = doc_freq.get(q, 0)
        if df == 0:
            continue
        idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
        tf = doc_tf.get(q, 0)
        if tf == 0:
            continue
        norm = tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / max(avg_dl, 1.0)))
        score += idf * norm
    return score


_GENERIC_SEARCH_TERMS = frozenset({
    "search", "get", "list", "read", "find", "show", "fetch",
    "create", "update", "delete", "tool", "mcp"
})

_GERMAN_SYNONYMS: Dict[str, List[str]] = {
    "office": ["m365", "msoffice365", "msoffice365mcp", "outlook", "calendar", "event", "email", "teams", "sharepoint", "onedrive"],
    "msoffice": ["m365", "msoffice365", "msoffice365mcp", "outlook", "calendar", "event", "email", "teams", "sharepoint", "onedrive"],
    "m365": ["m365", "msoffice365", "msoffice365mcp", "outlook", "calendar", "event", "email", "teams", "sharepoint", "onedrive"],
    "office365": ["m365", "msoffice365", "msoffice365mcp", "outlook", "calendar", "event", "email", "teams", "sharepoint", "onedrive"],
    "microsoft": ["m365", "msoffice365", "msoffice365mcp", "outlook", "calendar", "event", "email", "teams", "sharepoint", "onedrive"],
    "sharepoint": ["m365", "msoffice365", "msoffice365mcp", "sharepoint", "drive", "file", "sites"],
    "teams": ["m365", "msoffice365", "msoffice365mcp", "teams", "chat", "channel", "message", "gruppenchat"],
    "onedrive": ["m365", "msoffice365", "msoffice365mcp", "onedrive", "drive", "file"],
    "outlook": ["m365", "msoffice365", "msoffice365mcp", "outlook", "mail", "email", "calendar", "event"],
    "kalender": ["m365", "msoffice365", "msoffice365mcp", "outlook", "calendar", "event"],
    "calendar": ["m365", "msoffice365", "msoffice365mcp", "outlook", "calendar", "event"],
    "email": ["m365", "msoffice365", "msoffice365mcp", "outlook", "email", "mail"],
    "mail": ["m365", "msoffice365", "msoffice365mcp", "outlook", "email", "mail"],
    "chat": ["m365", "msoffice365", "msoffice365mcp", "teams", "chat", "message"],
    "chats": ["m365", "msoffice365", "msoffice365mcp", "teams", "chat", "message"],
    "gruppenchat": ["m365", "msoffice365", "msoffice365mcp", "teams", "chat", "group"],
    "gruppenchats": ["m365", "msoffice365", "msoffice365mcp", "teams", "chat", "group"],
    "notiz": ["memory", "note", "save", "read"],
    "notizen": ["memory", "note", "save", "read"],
    "gedächtnis": ["memory", "note", "save", "read", "search"],
    "gedaechtnis": ["memory", "note", "save", "read", "search"],
    "erinnerung": ["memory", "note", "save", "read", "search"],
    "wissensbasis": ["memory", "note", "kb", "search", "read"],
    "vektor": ["vector", "memory", "search", "hybrid"],
    "vector": ["vector", "memory", "search", "hybrid"],
    "aufgabe": ["todo", "task", "job", "kanban", "issue", "jira"],
    "aufgaben": ["todo", "task", "job", "kanban", "issue", "jira"],
    "ticket": ["issue", "jira", "bug", "task"],
    "tickets": ["issue", "jira", "bug", "task"],
    "zeitbuchung": ["worklog", "tempo", "time", "jira"],
    "zeitbuchungen": ["worklog", "tempo", "time", "jira"],
    "zeiterfassung": ["worklog", "tempo", "time", "jira"],
    "termin": ["calendar", "event", "outlook", "schedule"],
    "termine": ["calendar", "event", "outlook", "schedule"],
    "kalender": ["calendar", "event", "outlook"],
    "urlaub": ["calendar", "event", "m365", "outlook", "vacation"],
    "urlaube": ["calendar", "event", "m365", "outlook", "vacation"],
    "officezeiten": ["calendar", "event", "m365", "outlook", "schedule"],
    "arbeitszeiten": ["calendar", "event", "m365", "outlook", "schedule"],
    "feiertag": ["calendar", "event", "m365", "outlook", "holiday"],
    "feiertage": ["calendar", "event", "m365", "outlook", "holiday"],
    "abwesenheit": ["calendar", "event", "m365", "outlook", "vacation"],
    "abwesenheiten": ["calendar", "event", "m365", "outlook", "vacation"],
    "mail": ["email", "outlook", "message"],
    "mails": ["email", "outlook", "message"],
    "email": ["email", "outlook", "message"],
    "emails": ["email", "outlook", "message"],
    "nachricht": ["message", "chat", "send"],
    "nachrichten": ["message", "chat", "send"],
    "suche": ["search", "find", "get", "read"],
    "suchen": ["search", "find", "get", "read"],
    "erstellen": ["create", "add", "new", "write"],
    "anlegen": ["create", "add", "new", "write"],
    "bearbeiten": ["update", "edit", "patch"],
    "löschen": ["delete", "remove"],
    "loeschen": ["delete", "remove"],
}


SOURCE_ALIASES: Dict[str, str] = {
    "office": "MSOffice365MCP",
    "msoffice": "MSOffice365MCP",
    "msoffice365": "MSOffice365MCP",
    "ms365": "MSOffice365MCP",
    "office365": "MSOffice365MCP",
    "officetools": "MSOffice365MCP",
    "m365": "MSOffice365MCP",
    "outlook": "MSOffice365MCP",
    "teams": "MSOffice365MCP",
    "sharepoint": "MSOffice365MCP",
    "onedrive": "MSOffice365MCP",
    "tempo": "TempoMCP",
    "tempomcp": "TempoMCP",
    "timesheet": "TempoMCP",
    "timesheets": "TempoMCP",
    "worklogs": "TempoMCP",
    "worklog": "TempoMCP",
    "atlassian": "AtlassianMCP",
    "atlassianmcp": "AtlassianMCP",
    "jira": "AtlassianMCP",
    "confluence": "AtlassianMCP",
    "github": "GithubMCP",
    "githubmcp": "GithubMCP",
    "git": "GithubMCP",
    "aimds": "AIMDSSuiteMCP",
    "aimdssuite": "AIMDSSuiteMCP",
    "aimdssuitemcp": "AIMDSSuiteMCP",
}


def _normalize_source_key(text: str) -> str:
    """Collapse a source/toolset name to bare lowercase alnum for exact comparison.

    e.g. "mcp-MSOffice365MCP" and "MSOffice365MCP" both normalize to
    "msoffice365mcp" so a query naming the server matches regardless of the
    "mcp-" prefix or original casing.
    """
    raw = text.lower().removeprefix("mcp-")
    norm = re.sub(r"[^a-z0-9]", "", raw)
    if norm in SOURCE_ALIASES:
        return re.sub(r"[^a-z0-9]", "", SOURCE_ALIASES[norm].lower())
    return norm


def _match_full_source(catalog: List[CatalogEntry], query_lower: str) -> List[CatalogEntry]:
    """Return every tool of a source whose name or alias matches the query.

    When a user/model searches for e.g. "MSOffice365MCP", "office", or "github"
    they are asking to browse that server's *entire* tool catalog.
    """
    norm_query = re.sub(r"[^a-z0-9]", "", query_lower)
    if norm_query in SOURCE_ALIASES:
        norm_query = re.sub(r"[^a-z0-9]", "", SOURCE_ALIASES[norm_query].lower())
    if len(norm_query) < 3:
        return []
    by_source: Dict[str, List[CatalogEntry]] = {}
    for entry in catalog:
        if entry.source_name:
            by_source.setdefault(entry.source_name, []).append(entry)
    for source_name, entries in by_source.items():
        if _normalize_source_key(source_name) == norm_query:
            return sorted(entries, key=lambda e: e.name)
    return []


def _build_vector(text: str, idf: "Dict[str, float] | None" = None) -> Dict[str, float]:
    """Lexical vector for `text`, from the shared vectorizer.

    Previously a whole-word frequency count, which scored exact-token overlap
    and nothing else: a query for "worklog" could not reach a tool named
    `retrieveWorklogs`. `hermes_text_vector` adds character trigrams alongside
    the words, so morphology and typos survive while exact matches still rank
    higher. The same module now backs `agent/memory_vault_index.py`, which
    carried an independent copy of the old approach.
    """
    return _shared_build_vector(text, idf=idf)


def _cosine_sim(v1: Dict[str, float], v2: Dict[str, float]) -> float:
    """Cosine similarity between two vectors from :func:`_build_vector`."""
    return _shared_cosine(v1, v2)


def search_catalog(catalog: List[CatalogEntry], query: str, limit: int = 8) -> List[CatalogEntry]:
    """Return top catalog entries using vector-first similarity + BM25 keyword fallback.

    Performs vector cosine similarity across tool names, descriptions, parameters,
    source aliases, and tags first, then applies BM25/keyword scoring as a secondary
    refinement.
    """
    if not catalog or limit <= 0:
        return []
    query_raw = str(query or "").strip()
    if not query_raw:
        return []
    query_tokens = _tokenize(_split_words(query_raw))
    if not query_tokens:
        return []

    # Fast path: query is an MCP server/toolset name or source alias -> return full tool catalog
    full_source_hits = _match_full_source(catalog, query_raw.lower())
    if full_source_hits:
        effective_limit = max(limit, min(len(full_source_hits), 60))
        return full_source_hits[:effective_limit]

    # Vector-first scoring setup
    # Same corpus statistics as the entries, or plain frequency for a
    # hand-built catalog that never went through `build_catalog`.
    catalog_idf = next((e._idf for e in catalog if e._idf), None)
    query_vec = _build_vector(query_raw, catalog_idf)

    # Expand query tokens with synonyms for BM25 fallback
    expanded_tokens = set(query_tokens)
    dynamic_mcp = _get_dynamic_mcp_keywords_map()
    dynamic_skills = _get_dynamic_skill_keywords_map()
    for qt in query_tokens:
        if qt in _GERMAN_SYNONYMS:
            expanded_tokens.update(_GERMAN_SYNONYMS[qt])
        if qt in dynamic_mcp:
            expanded_tokens.update(dynamic_mcp[qt])
        if qt in dynamic_skills:
            expanded_tokens.update(dynamic_skills[qt])
    expanded_query_tokens = list(expanded_tokens)

    query_lower = query.lower().strip()
    non_generic_query_tokens = [t for t in query_tokens if t not in _GENERIC_SEARCH_TERMS]

    # Precompute doc statistics.
    doc_lengths = [len(e._tokens) for e in catalog]
    avg_dl = sum(doc_lengths) / max(len(doc_lengths), 1)
    doc_freq: Dict[str, int] = {}
    for e in catalog:
        for t in set(e._tokens):
            doc_freq[t] = doc_freq.get(t, 0) + 1
    n_docs = len(catalog)

    k1 = 1.5
    b = 0.75
    scored: List[Tuple[float, CatalogEntry]] = []
    for entry in catalog:
        # Precomputed in `build_catalog`; recomputed only for entries a
        # caller constructed by hand (several tests do this).
        entry_vec = entry._vector
        if not entry_vec:
            entry_text = f"{_split_words(entry.name)} {_split_words(entry.source_name)} {entry.description}"
            entry_vec = _build_vector(entry_text, catalog_idf)
        vec_sim = _cosine_sim(query_vec, entry_vec)

        doc_tf: Dict[str, int] = {}
        for t in entry._tokens:
            doc_tf[t] = doc_tf.get(t, 0) + 1

        bm25_score = 0.0
        matched_query_tokens: set[str] = set()
        matched_specific_tokens: set[str] = set()
        matched_name_tokens: set[str] = set()

        for q in set(expanded_query_tokens):
            if q in doc_tf:
                matched_query_tokens.add(q)
                if q not in _GENERIC_SEARCH_TERMS:
                    matched_specific_tokens.add(q)
                if q in entry._name_tokens or q in entry._source_tokens:
                    matched_name_tokens.add(q)
                df = doc_freq.get(q, 0)
                idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
                tf = doc_tf[q]
                norm = tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(entry._tokens) / max(avg_dl, 1.0)))
                bm25_score += idf * norm
            elif len(q) >= 5 and q not in _GENERIC_SEARCH_TERMS:
                # Inflection tolerance for the false-positive filter below:
                # "worklog" must count as matching a tool that only says
                # "worklogs" (and vice versa). The trigram vector already
                # scores such pairs; without this the filter dropped them
                # anyway because no *exact* token overlapped.
                if any(
                    (t.startswith(q) or q.startswith(t)) and abs(len(t) - len(q)) <= 3
                    for t in doc_tf
                    if len(t) >= 5
                ):
                    matched_query_tokens.add(q)
                    matched_specific_tokens.add(q)
                    if any((t.startswith(q) or q.startswith(t)) for t in entry._name_tokens if len(t) >= 5):
                        matched_name_tokens.add(q)

        if vec_sim <= 0 and bm25_score <= 0 and not matched_query_tokens:
            continue

        boost = 0.0
        clean_name = entry.name.lower().replace("_", "").replace("-", "")
        clean_query = query_lower.replace("_", "").replace("-", "")
        if clean_query and (clean_query in clean_name or clean_name in clean_query):
            boost += 10.0

        boost += len(matched_name_tokens) * 5.0

        # Source / Server match boost: if query mentions the MCP server or source alias (e.g. tempo, atlassian, github)
        for qt in query_tokens:
            norm_qt = _normalize_source_key(qt)
            if norm_qt and (norm_qt == _normalize_source_key(entry.source_name) or norm_qt in entry.source_name.lower()):
                boost += 30.0
                break

        # Utility tool de-prioritization: boilerplate MCP utility tools shouldn't drown domain tools
        if any(entry.name.endswith(f"_{u}") for u in ("get_prompt", "list_prompts", "list_resources", "read_resource")):
            if not any(k in query_lower for k in ("prompt", "prompts", "resource", "resources")):
                boost -= 15.0

        # Vector similarity receives primary weighting
        vector_score = vec_sim * 100.0
        total_score = vector_score + bm25_score + boost

        # Filtering: if user searched specific terms (e.g. 'jira'), but entry matched ONLY generic terms (e.g. 'search')
        # in description and zero specific, name, or source terms, drop this false positive.
        if non_generic_query_tokens and not matched_specific_tokens and not matched_name_tokens and not any(t in entry._source_tokens for t in non_generic_query_tokens):
            continue

        scored.append((total_score, entry))

    if not scored:
        # Fallback: literal substring match on original tool name if filtered out
        ql = query_lower
        for entry in catalog:
            if ql in entry.name.lower():
                scored.append((0.1, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    results = [e for _, e in scored[:limit]]

    # Guarantee a slot for a server the query named outright. A source with
    # many tools otherwise fills every slot with moderate matches and buries
    # the one the user actually asked for: "Tempo worklog time tracking"
    # returned eight AtlassianMCP tools and no TempoMCP, because "worklog"
    # matched fourteen Jira tools while "tempo" matched seven Tempo ones. The
    # model then worked around the absence by fetching worklogs issue by
    # issue — sixteen calls to do one tool's job.
    named_sources = {
        entry.source_name
        for entry in catalog
        if entry.kind == "tool" and entry.source_name and any(t in entry._source_tokens for t in query_tokens)
    }
    if named_sources and scored:
        represented = {e.source_name for e in results}
        for source in named_sources - represented:
            best = next((e for _, e in scored if e.source_name == source), None)
            if best is None:
                continue
            if len(results) >= limit:
                results.pop()
            results.append(best)

    # MCP Server Grouping: if top matches belong to an MCP server/toolset,
    # include sibling tools from the same source so the model gets related actions in 1 turn.
    if results and len(results) < limit:
        seen_names = {e.name for e in results}
        top_sources = {e.source_name for e in results[:2] if e.source_name}
        for entry in catalog:
            if len(results) >= limit:
                break
            if entry.source != "mcp":
                continue
            if entry.name not in seen_names and entry.source_name in top_sources:
                results.append(entry)
                seen_names.add(entry.name)

    return results


def cap_skill_hits(results: List[CatalogEntry], query: str, kind: Optional[str] = None) -> List[CatalogEntry]:
    """At most ``SKILL_HIT_CAP`` skills per search unless skills were asked for.

    ``kind="skill"`` keeps only skills, ``kind="tool"`` only tools; a query
    that says "skill" lifts the cap.
    """
    if kind == "skill":
        return [e for e in results if e.kind != "tool"]
    if kind == "tool":
        return [e for e in results if e.kind == "tool"]
    if "skill" in query.lower():
        return results
    kept: List[CatalogEntry] = []
    skills_seen = 0
    for entry in results:
        if entry.kind != "tool":
            if skills_seen >= SKILL_HIT_CAP:
                continue
            skills_seen += 1
        kept.append(entry)
    return kept


# ---------------------------------------------------------------------------
# Bridge tool schemas
# ---------------------------------------------------------------------------


def bridge_tool_schemas(deferred_count: int) -> List[Dict[str, Any]]:
    """Build the bridge tool schemas to inject in place of deferred tools.

    The schemas are intentionally short — every byte added here is a byte
    the user pays on every turn. Descriptions are tuned to be unambiguous
    about the call sequence the model should follow.
    """
    desc_search = (
        f"Search {deferred_count} additional tools (loaded on demand) and the "
        "installed skills. Use it whenever the task needs external data or side "
        "effects (API calls, remote reads, ticket/file updates, sends, writes) "
        "and no tool in your list is an exact fit. Each match carries `kind`: "
        "tools marked `status: loaded` are added to your tools list — call them "
        f"directly by `name` next; for others call `{TOOL_DESCRIBE_NAME}` first. "
        "Skills are not callable: read them with the command in `how_to_use`."
    )
    desc_describe = (
        f"Load one deferred tool's full schema into your tools list so you can "
        f"call it directly by name (the result tells you when it is loaded)."
    )
    desc_call = (
        "Fallback: invoke a deferred tool by name without loading it first. "
        f"Argument shape matches the tool's schema (see `{TOOL_DESCRIBE_NAME}`). "
        "Policy, hooks, and approvals run exactly as for any directly-listed tool."
    )

    return [
        {
            "type": "function",
            "function": {
                "name": TOOL_SEARCH_NAME,
                "description": desc_search,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Keywords describing the capability you need (e.g. 'create github issue').",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results to return. Default 8.",
                        },
                        "kind": {
                            "type": "string",
                            "enum": ["tool", "skill"],
                            "description": "Restrict results to tools or to skills. Omit for both.",
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": TOOL_DESCRIBE_NAME,
                "description": desc_describe,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Exact tool name (as returned by tool_search).",
                        },
                    },
                    "required": ["name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": TOOL_CALL_NAME,
                "description": desc_call,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Exact tool name to invoke.",
                        },
                        "arguments": {
                            "type": "object",
                            "description": "Arguments for the tool, matching its schema.",
                        },
                    },
                    "required": ["name", "arguments"],
                },
            },
        },
    ]


# ---------------------------------------------------------------------------
# Public entry point: assemble tool-defs with optional tool search
# ---------------------------------------------------------------------------


@dataclass
class AssemblyResult:
    """Outcome of one assembly. Useful for tests and observability."""

    tool_defs: List[Dict[str, Any]]
    activated: bool
    deferred_count: int = 0
    deferred_tokens: int = 0
    threshold_tokens: int = 0


def assemble_tool_defs(
    tool_defs: List[Dict[str, Any]],
    *,
    context_length: Optional[int] = None,
    config: Optional[ToolSearchConfig] = None,
) -> AssemblyResult:
    """Return the tool-defs list the model should actually see.

    When tool search is inactive (off, no deferrable tools, or below
    threshold), this is a passthrough. When active, MCP and plugin tools
    are stripped from the visible list and replaced with the three bridge
    tools. Core tools are *never* deferred regardless of config.

    Idempotent: calling with bridge tools already in the input is a no-op
    (they classify as non-core/non-deferrable but their names are reserved,
    so they are filtered out of the deferrable set).
    """
    if config is None:
        config = load_config()

    # Defensive: strip any bridge tools that may already be in the list
    # (e.g. someone called assemble twice).
    incoming = [td for td in tool_defs
                if (td.get("function") or {}).get("name") not in BRIDGE_TOOL_NAMES]

    visible, deferrable = classify_tools(incoming)
    if not deferrable:
        return AssemblyResult(tool_defs=incoming, activated=False)

    deferrable_tokens = estimate_tokens_from_schemas(deferrable)
    if not should_activate(config, deferrable_tokens, context_length):
        return AssemblyResult(
            tool_defs=incoming,
            activated=False,
            deferred_count=len(deferrable),
            deferred_tokens=deferrable_tokens,
            threshold_tokens=int((context_length or 0) * (config.threshold_pct / 100.0)),
        )

    bridge = bridge_tool_schemas(len(deferrable))
    result = visible + bridge
    # Mirror should_activate: the auto threshold is capped at 8k tokens. The
    # uncapped figure (e.g. 25,600 for a 256k model) used to be logged here,
    # which made the activation look mis-tuned.
    threshold_tokens = min(int((context_length or 0) * (config.threshold_pct / 100.0)), 8_000)
    if config.enabled == "on":
        threshold_tokens = 0

    logger.info(
        "[AIS-161] tool_search activated: %d core/visible tools kept, %d deferred (~%d tokens, %s)",
        len(visible), len(deferrable), deferrable_tokens,
        "enabled=on" if config.enabled == "on" else f"auto threshold ~{threshold_tokens} (cap 8000)",
    )

    return AssemblyResult(
        tool_defs=result,
        activated=True,
        deferred_count=len(deferrable),
        deferred_tokens=deferrable_tokens,
        threshold_tokens=threshold_tokens,
    )


# ---------------------------------------------------------------------------
# Bridge tool dispatch
# ---------------------------------------------------------------------------


def is_bridge_tool(name: str) -> bool:
    return name in BRIDGE_TOOL_NAMES


def _ensure_mcp_discovery_completed() -> None:
    """Ensure background MCP discovery finishes before querying the registry/catalog."""
    try:
        from hermes_cli.mcp_startup import wait_for_mcp_discovery
        wait_for_mcp_discovery()
    except Exception:
        pass


_CATALOG_CACHE: "tuple | None" = None


def _cached_catalog(
    tool_defs: List[Dict[str, Any]],
    skills: Optional[List[Dict[str, Any]]] = None,
) -> List[CatalogEntry]:
    """`build_catalog` bound to the registry generation and the def set.

    Building a catalog resolves every tool through `registry.get_entry`, pulls
    MCP server metadata, and vectorizes each entry against corpus IDF. That ran
    from scratch on every single `tool_search` call, even when nothing had
    changed between them — and a model typically searches several times per
    turn.

    The key mirrors `model_tools.get_tool_definitions`: `registry._generation`
    captures registry mutations (MCP refresh, plugin load), and the tool-name
    set captures a caller passing a different slice of defs.
    """
    global _CATALOG_CACHE

    try:
        from tools.registry import registry as _registry

        generation = _registry._generation
    except Exception:
        generation = None

    key = (
        generation,
        tuple(td.get("function", {}).get("name", "") for td in tool_defs),
        tuple((sk.get("name", ""), sk.get("updated_at", 0), sk.get("kind", "skill")) for sk in (skills or [])),
    )
    if _CATALOG_CACHE is not None and _CATALOG_CACHE[0] == key:
        return _CATALOG_CACHE[1]

    catalog = build_catalog(tool_defs, skills=skills)
    _CATALOG_CACHE = (key, catalog)

    return catalog


def _format_search_hit(entry: CatalogEntry) -> Dict[str, Any]:
    raw_desc = (entry.description or "").strip()
    first_line = raw_desc.split("\n")[0].strip() if raw_desc else ""
    if len(first_line) > 150:
        first_line = first_line[:147] + "..."
    hit: Dict[str, Any] = {
        "name": entry.name,
        "kind": entry.kind or "tool",
        "description": first_line,
    }
    if entry.kind != "tool":
        category = entry.source_name.removeprefix("skill:") if entry.source_name else ""
        if category:
            hit["category"] = category
        hit["how_to_use"] = entry.how_to_use
        return hit
    if entry.source_name:
        hit["source"] = entry.source_name
    return hit


def _skills_in_scope(current_tool_defs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Local skills join the catalog only when the session can read them."""
    names = {(td.get("function") or {}).get("name", "") for td in current_tool_defs}
    if "skill_view" not in names and "skills_read" not in names:
        return []
    return local_skill_catalog_entries()


def dispatch_tool_search(args: Dict[str, Any],
                         *,
                         current_tool_defs: List[Dict[str, Any]],
                         config: Optional[ToolSearchConfig] = None) -> str:
    """Execute the ``tool_search`` bridge tool. Returns a JSON string."""
    _ensure_mcp_discovery_completed()
    if config is None:
        config = load_config()
    query = str(args.get("query") or "").strip()
    if not query:
        return json.dumps({"error": "query is required"}, ensure_ascii=False)

    raw_limit = args.get("limit")
    if raw_limit is None:
        limit = config.search_default_limit
    else:
        limit = max(1, min(config.max_search_limit, _safe_int(raw_limit, config.search_default_limit)))

    kind = str(args.get("kind") or "").strip().lower() or None
    if kind not in (None, "tool", "skill"):
        kind = None

    _, deferrable = classify_tools(current_tool_defs)
    if not any(d.get("function", {}).get("name", "").startswith("mcp_") for d in deferrable):
        try:
            import model_tools
            live_defs = model_tools.get_tool_definitions(skip_tool_search_assembly=True, quiet_mode=True)
            _, live_deferrable = classify_tools(live_defs)
            if len(live_deferrable) > len(deferrable):
                deferrable = live_deferrable
        except Exception:
            pass
    skills = _skills_in_scope(current_tool_defs)
    catalog = _cached_catalog(deferrable, skills)
    # Skills compete for the same slots; fetch a little deeper so the cap
    # does not leave the result short of tools.
    hits = search_catalog(catalog, query, limit=limit + SKILL_HIT_CAP)
    hits = cap_skill_hits(hits, query, kind)[:limit]
    # A query that names a whole server browses its catalog (up to 60 hits);
    # loading all of those would defeat deferral, so only ranked searches
    # nominate hits for autoload. The executor (agent/deferred_tools.py)
    # performs the load — this function has no session in hand.
    browsing = bool(_match_full_source(catalog, query.lower()))
    tool_hits = [h for h in hits if h.kind == "tool"]
    autoload = [] if browsing else [h.name for h in tool_hits[: max(0, config.autoload_top_n)]]
    return json.dumps({
        "query": query,
        "mode": "source_browse" if browsing else "ranked",
        "total_available": len(catalog),
        "matches": [_format_search_hit(h) for h in hits],
        "autoload": autoload,
    }, ensure_ascii=False)


def _resolve_tool_entry(name: str):
    """Find a ToolEntry by exact name, case-insensitive match, or unique suffix match (e.g. unprefixed MCP tool name)."""
    _ensure_mcp_discovery_completed()
    from tools.registry import registry
    entry = registry.get_entry(name)
    if entry is not None:
        return entry.name, entry, None

    entries = registry._snapshot_entries()
    exact_ci = [e for e in entries if e.name.lower() == name.lower()]
    if len(exact_ci) == 1:
        return exact_ci[0].name, exact_ci[0], None

    suffix_matches = [
        e for e in entries
        if e.name.endswith(f"_{name}") or e.name.endswith(f":{name}")
    ]
    if len(suffix_matches) == 1:
        return suffix_matches[0].name, suffix_matches[0], None
    if len(suffix_matches) > 1:
        match_names = ", ".join(e.name for e in suffix_matches)
        return None, None, (
            f"Tool '{name}' is ambiguous ({match_names}). "
            "Use tool_search to find the exact registered tool name."
        )

    suffix_matches_ci = [
        e for e in entries
        if e.name.lower().endswith(f"_{name.lower()}") or e.name.lower().endswith(f":{name.lower()}")
    ]
    if len(suffix_matches_ci) == 1:
        return suffix_matches_ci[0].name, suffix_matches_ci[0], None
    if len(suffix_matches_ci) > 1:
        match_names = ", ".join(e.name for e in suffix_matches_ci)
        return None, None, (
            f"Tool '{name}' is ambiguous ({match_names}). "
            "Use tool_search to find the exact registered tool name."
        )

    return None, None, (
        f"Tool '{name}' is not registered or found. "
        "Use tool_search to find the exact registered tool name."
    )


def dispatch_tool_describe(args: Dict[str, Any],
                           *,
                           current_tool_defs: List[Dict[str, Any]]) -> str:
    """Execute the ``tool_describe`` bridge tool. Returns a JSON string."""
    raw_name = str(args.get("name") or "").strip()
    if not raw_name:
        return json.dumps({"error": "name is required"}, ensure_ascii=False)

    resolved_name, entry, err = _resolve_tool_entry(raw_name)
    if err or entry is None or not resolved_name:
        for sk in _skills_in_scope(current_tool_defs):
            if sk["name"].lower() == raw_name.lower():
                return json.dumps({
                    "name": sk["name"],
                    "kind": "skill",
                    "description": sk["description"],
                    "category": sk["category"],
                    "how_to_use": sk["how_to_use"],
                    "note": "Skills are not callable functions; read the skill and follow it.",
                }, ensure_ascii=False)
        return json.dumps({
            "error": err or (
                f"Tool '{raw_name}' is not registered or found. "
                "Use tool_search to find the exact registered tool name."
            ),
        }, ensure_ascii=False)

    if not is_deferrable_tool_name(resolved_name):
        return json.dumps({
            "error": f"Tool '{resolved_name}' is a core tool and is already fully visible in your tool list.",
        }, ensure_ascii=False)

    for td in current_tool_defs:
        fn = td.get("function") or {}
        if fn.get("name") == resolved_name:
            return json.dumps({
                "name": resolved_name,
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {}),
                "usage_hint": f"Call tool_call(name='{resolved_name}', arguments={{...}}) to execute this tool.",
            }, ensure_ascii=False)
    return json.dumps({
        "error": f"Tool '{resolved_name}' is not available in this session. Use tool_search to find tools you can call.",
    }, ensure_ascii=False)


def scoped_tool_names(tool_defs: List[Dict[str, Any]]) -> frozenset[str]:
    """Return the set of callable non-bridge tool names present in ``tool_defs``.

    ``tool_defs`` is expected to be the *pre-assembly* tool list for the
    current session's toolset scope (i.e. what
    ``get_tool_definitions(skip_tool_search_assembly=True)`` returns for the
    session's enabled/disabled toolsets). Used as a scoping gate by both the
    ``model_tools`` bridge dispatch and the ``tool_executor`` unwrap so a
    restricted-toolset session can never invoke an out-of-scope tool via the
    bridge.
    """
    names: set[str] = set()
    for td in tool_defs:
        name = (td.get("function") or {}).get("name", "")
        if name and name not in BRIDGE_TOOL_NAMES:
            names.add(name)
    return frozenset(names)


def scoped_deferrable_names(tool_defs: List[Dict[str, Any]]) -> frozenset[str]:
    """Backward-compatible alias for ``scoped_tool_names``."""
    return scoped_tool_names(tool_defs)


def resolve_underlying_call(args: Dict[str, Any]) -> Tuple[Optional[str], Dict[str, Any], Optional[str]]:
    """Parse a ``tool_call`` invocation into (underlying_name, args, error_msg).

    Used by:
    * the dispatcher in ``model_tools.handle_function_call``,
    * the display layer (so the activity feed shows the underlying tool),
    * the trajectory recorder.

    On parse error, returns ``(None, {}, error_message)``.
    """
    raw_args = args.get("arguments")
    if raw_args is None:
        raw_args = {}
    if isinstance(raw_args, str):
        try:
            raw_args = json.loads(raw_args)
        except json.JSONDecodeError as e:
            return None, {}, f"tool_call 'arguments' is not valid JSON: {e}"
    if not isinstance(raw_args, dict):
        return None, {}, "tool_call 'arguments' must be an object"

    raw_name = str(args.get("name") or "").strip()
    if not raw_name and ("name" in raw_args or "tool_name" in raw_args):
        raw_name = str(raw_args.pop("name", None) or raw_args.pop("tool_name", None) or "").strip()

    if not raw_name:
        return None, {}, "tool_call requires a 'name' argument"
    if raw_name in BRIDGE_TOOL_NAMES:
        return None, {}, f"tool_call cannot invoke '{raw_name}' (it is itself a bridge tool)"

    resolved_name, entry, err = _resolve_tool_entry(raw_name)
    if err or entry is None or not resolved_name:
        return None, {}, (
            err or (
                f"Tool '{raw_name}' is not registered or found. "
                "Use tool_search to find the exact registered tool name."
            )
        )

    return resolved_name, raw_args, None


__all__ = [
    "TOOL_SEARCH_NAME",
    "TOOL_DESCRIBE_NAME",
    "TOOL_CALL_NAME",
    "BRIDGE_TOOL_NAMES",
    "ToolSearchConfig",
    "CatalogEntry",
    "AssemblyResult",
    "load_config",
    "is_deferrable_tool_name",
    "is_bootstrap_memory_tool",
    "BOOTSTRAP_MEMORY_SUFFIXES",
    "local_skill_catalog_entries",
    "cap_skill_hits",
    "classify_tools",
    "estimate_tokens_from_schemas",
    "should_activate",
    "build_catalog",
    "search_catalog",
    "bridge_tool_schemas",
    "assemble_tool_defs",
    "is_bridge_tool",
    "dispatch_tool_search",
    "dispatch_tool_describe",
    "resolve_underlying_call",
    "scoped_tool_names",
    "scoped_deferrable_names",
]
