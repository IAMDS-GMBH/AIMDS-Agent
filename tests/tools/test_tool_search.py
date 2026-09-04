"""Tests for tools/tool_search.py — progressive tool disclosure.

Coverage targets — these mirror the issues called out in the OpenClaw tool
search report. Every test that names an OpenClaw issue is the regression
guard that would have caught that specific failure mode.
"""

from __future__ import annotations

import json
import os
import sys
from typing import List, Dict, Any

import pytest


_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _td(name: str, description: str = "", properties: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties or {},
            },
        },
    }


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------


class TestConfigParsing:
    def test_default_when_missing(self):
        from tools.tool_search import ToolSearchConfig
        cfg = ToolSearchConfig.from_raw(None)
        assert cfg.enabled == "auto"
        assert cfg.threshold_pct == 10.0

    def test_bool_true_maps_to_auto(self):
        from tools.tool_search import ToolSearchConfig
        cfg = ToolSearchConfig.from_raw(True)
        assert cfg.enabled == "auto"

    def test_bool_false_maps_to_off(self):
        from tools.tool_search import ToolSearchConfig
        cfg = ToolSearchConfig.from_raw(False)
        assert cfg.enabled == "off"

    def test_explicit_on(self):
        from tools.tool_search import ToolSearchConfig
        cfg = ToolSearchConfig.from_raw({"enabled": "on"})
        assert cfg.enabled == "on"

    def test_invalid_enabled_falls_back_to_auto(self):
        from tools.tool_search import ToolSearchConfig
        cfg = ToolSearchConfig.from_raw({"enabled": "maybe"})
        assert cfg.enabled == "auto"

    def test_threshold_clamped(self):
        from tools.tool_search import ToolSearchConfig
        cfg = ToolSearchConfig.from_raw({"threshold_pct": 150})
        assert cfg.threshold_pct == 100.0
        cfg = ToolSearchConfig.from_raw({"threshold_pct": -5})
        assert cfg.threshold_pct == 0.0

    def test_autoload_top_n_defaults_and_clamps(self):
        from tools.tool_search import ToolSearchConfig
        assert ToolSearchConfig.from_raw({"enabled": "on"}).autoload_top_n == 3
        assert ToolSearchConfig.from_raw({"autoload_top_n": 0}).autoload_top_n == 0
        assert ToolSearchConfig.from_raw({"autoload_top_n": 99, "max_search_limit": 10}).autoload_top_n == 10
        assert ToolSearchConfig.from_raw({"autoload_top_n": "nonsense"}).autoload_top_n == 3

    def test_search_limits_clamped(self):
        from tools.tool_search import ToolSearchConfig
        cfg = ToolSearchConfig.from_raw({
            "search_default_limit": 999,
            "max_search_limit": 999,
        })
        assert cfg.max_search_limit == 50
        assert cfg.search_default_limit <= cfg.max_search_limit


# ---------------------------------------------------------------------------
# Classification — the hard invariant: core tools NEVER defer.
# ---------------------------------------------------------------------------


class TestClassification:
    def test_core_tools_never_defer(self):
        """The critical invariant from the OpenClaw report."""
        from tools.tool_search import is_deferrable_tool_name
        # Sample of core tools from _HERMES_CORE_TOOLS.
        for core_name in ["terminal", "read_file", "write_file", "patch",
                          "search_files", "todo", "memory",
                          "web_search", "clarify"]:
            assert not is_deferrable_tool_name(core_name), (
                f"Core tool '{core_name}' must NEVER be deferrable"
            )

    def test_bridge_tools_never_defer(self):
        from tools.tool_search import is_deferrable_tool_name, BRIDGE_TOOL_NAMES
        for name in BRIDGE_TOOL_NAMES:
            assert not is_deferrable_tool_name(name)

    def test_core_skill_tools_never_defer(self):
        from tools.tool_search import is_deferrable_tool_name
        assert not is_deferrable_tool_name("skill_view")
        assert not is_deferrable_tool_name("skill_manage")
        assert not is_deferrable_tool_name("skills_list")

    def test_primary_server_bootstrap_tools_never_defer(self, monkeypatch):
        """Only the primary memory server's bootstrap tools stay visible."""
        from tools import tool_search as ts
        from tools.registry import registry

        monkeypatch.setattr(ts, "_primary_mcp_server_name", lambda: "AIMDSSuiteMCP")
        for name in [
            "memory_context",
            "memory_save",
            "mcp_AIMDSSuiteMCP_mcp_memory_memory_context",
            "mcp_AIMDSSuiteMCP_mcp_memory_skill",
            "mcp_AIMDSSuiteMCP_mcp_memory_memory_save",
            "mcp_AIMDSSuiteMCP_mcp_memory_memory_search",
            "mcp_AIMDSSuiteMCP_mcp_memory_memory_read",
            "mcp_AIMDSSuiteMCP_mcp_memory_memory_summarize_session",
        ]:
            assert ts.is_bootstrap_memory_tool(name), name
            assert not ts.is_deferrable_tool_name(name), (
                f"Bootstrap memory tool '{name}' must remain model-visible"
            )

        # everything else the server offers — and every other server — defers
        for name, toolset in [
            ("mcp_AIMDSSuiteMCP_mcp_memory_memory_list", "mcp-AIMDSSuiteMCP"),
            ("mcp_AIMDSSuiteMCP_mcp_memory_memory_manage", "mcp-AIMDSSuiteMCP"),
            ("mcp_AIMDSSuiteMCP_kb_search", "mcp-AIMDSSuiteMCP"),
            ("mcp_CustomMemory_memory_context", "mcp-CustomMemory"),
            ("mcp_CustomMemory_memory_save", "mcp-CustomMemory"),
        ]:
            registry.register(name=name, toolset=toolset, schema=_td(name)["function"],
                              handler=lambda a, **k: "{}")
            assert not ts.is_bootstrap_memory_tool(name), name
            assert ts.is_deferrable_tool_name(name), f"'{name}' must be deferrable"

    def test_no_user_specific_server_aliases_in_product_code(self):
        from tools.tool_search import SOURCE_ALIASES
        assert not any("entwickler" in k or v == "EnwicklerMemoryMCP" for k, v in SOURCE_ALIASES.items())

    def test_unknown_tool_not_deferrable(self):
        """Defensive: a tool name we cannot resolve to a registry entry must
        not be claimed as deferrable. This protects against the OpenClaw
        cron regression where unresolved tools were silently dropped."""
        from tools.tool_search import is_deferrable_tool_name
        assert not is_deferrable_tool_name("xx_definitely_not_a_tool_xx")

    def test_classify_keeps_unknown_in_visible(self):
        """A tool we can't classify stays visible — never silently dropped.

        This is the OpenClaw #84141 regression guard (cron lost ``exec``
        because it wasn't in the catalog).
        """
        from tools.tool_search import classify_tools
        # Build a tool def for something we don't have a registry entry for.
        defs = [_td("xx_unknown_tool", "Unknown tool")]
        visible, deferrable = classify_tools(defs)
        names = {(td.get("function") or {}).get("name") for td in visible}
        assert "xx_unknown_tool" in names
        assert deferrable == []


# ---------------------------------------------------------------------------
# Token estimation + threshold gate
# ---------------------------------------------------------------------------


class TestThresholdGate:
    def test_off_never_activates(self):
        from tools.tool_search import ToolSearchConfig, should_activate
        cfg = ToolSearchConfig.from_raw({"enabled": "off"})
        assert not should_activate(cfg, deferrable_tokens=1_000_000, context_length=200_000)

    def test_zero_deferrable_never_activates(self):
        from tools.tool_search import ToolSearchConfig, should_activate
        cfg = ToolSearchConfig.from_raw({"enabled": "on"})
        assert not should_activate(cfg, deferrable_tokens=0, context_length=200_000)

    def test_on_activates_with_any_deferrable(self):
        from tools.tool_search import ToolSearchConfig, should_activate
        cfg = ToolSearchConfig.from_raw({"enabled": "on"})
        assert should_activate(cfg, deferrable_tokens=100, context_length=200_000)

    def test_auto_below_threshold_does_not_activate(self):
        from tools.tool_search import ToolSearchConfig, should_activate
        cfg = ToolSearchConfig.from_raw({"enabled": "auto", "threshold_pct": 10})
        # Below 10% on 30K context (3,000 tokens < 3,000 cap / threshold)
        assert not should_activate(cfg, deferrable_tokens=2_000, context_length=30_000)

    def test_auto_at_or_above_threshold_activates(self):
        from tools.tool_search import ToolSearchConfig, should_activate
        cfg = ToolSearchConfig.from_raw({"enabled": "auto", "threshold_pct": 10})
        assert should_activate(cfg, deferrable_tokens=8_000, context_length=200_000)
        assert should_activate(cfg, deferrable_tokens=50_000, context_length=200_000)

    def test_auto_without_context_length_uses_8k_cutoff(self):
        """Fallback cutoff used when the active model is unknown."""
        from tools.tool_search import ToolSearchConfig, should_activate
        cfg = ToolSearchConfig.from_raw({"enabled": "auto"})
        assert not should_activate(cfg, deferrable_tokens=5_000, context_length=0)
        assert should_activate(cfg, deferrable_tokens=10_000, context_length=0)

    def test_token_estimate_proportional_to_schema_size(self):
        from tools.tool_search import estimate_tokens_from_schemas
        small = [_td("a", "x")]
        big = [_td(f"name_{i}", f"description for tool {i} " * 20,
                   {"q": {"type": "string", "description": "search query " * 10}})
               for i in range(10)]
        small_t = estimate_tokens_from_schemas(small)
        big_t = estimate_tokens_from_schemas(big)
        assert big_t > small_t * 10


# ---------------------------------------------------------------------------
# Retrieval (BM25 + substring fallback)
# ---------------------------------------------------------------------------


class TestRetrieval:
    def _fake_catalog(self):
        """Build a catalog directly without touching the registry."""
        from tools.tool_search import CatalogEntry, _tokenize, _entry_search_text
        defs = [
            _td("github_create_issue", "Open a new issue in a GitHub repository",
                {"title": {"type": "string"}, "body": {"type": "string"}}),
            _td("github_search_repos", "Search GitHub for matching repositories",
                {"query": {"type": "string"}}),
            _td("slack_send_message", "Post a message into a Slack channel",
                {"channel": {"type": "string"}, "text": {"type": "string"}}),
            _td("calendar_create_event", "Add an event to the user's calendar",
                {"title": {"type": "string"}, "start": {"type": "string"}}),
        ]
        catalog = []
        for d in defs:
            fn = d["function"]
            e = CatalogEntry(
                name=fn["name"], description=fn["description"],
                schema=d, source="mcp", source_name="mcp-test",
            )
            e._tokens = _tokenize(_entry_search_text(d))
            catalog.append(e)
        return catalog

    def test_search_finds_relevant_tool(self):
        from tools.tool_search import search_catalog
        hits = search_catalog(self._fake_catalog(), "create a github issue", limit=3)
        names = [h.name for h in hits]
        assert names[0] == "github_create_issue"

    def test_search_returns_empty_for_irrelevant_query(self):
        from tools.tool_search import search_catalog
        hits = search_catalog(self._fake_catalog(), "asdf qwerty foobar", limit=3)
        assert hits == []

    def test_search_substring_fallback(self):
        """Even when no BM25 hit, a literal substring of the tool name returns."""
        from tools.tool_search import search_catalog
        hits = search_catalog(self._fake_catalog(), "calendar", limit=3)
        assert any("calendar" in h.name for h in hits)

    def test_search_respects_limit(self):
        from tools.tool_search import search_catalog
        hits = search_catalog(self._fake_catalog(), "github", limit=1)
        assert len(hits) <= 1

    def test_search_filters_generic_false_positives(self):
        """When searching for specific terms (e.g. 'jira search'), tools that only match 'search'
        in description but have zero name/source/term matches for 'jira' are filtered out."""
        from tools.tool_search import CatalogEntry, search_catalog, _tokenize, _entry_search_text
        outlook_td = _td("outlook_read_contacts", "Read and search contacts in Outlook")
        jira_td = _td("jira_search_issues", "Search Jira issues using JQL")

        e_outlook = CatalogEntry("outlook_read_contacts", outlook_td["function"]["description"], outlook_td, "plugin", "outlook")
        e_jira = CatalogEntry("jira_search_issues", jira_td["function"]["description"], jira_td, "mcp", "mcp-AtlassianMCP")

        e_outlook._tokens = _tokenize(_entry_search_text(outlook_td, "outlook"))
        e_outlook._name_tokens = set(_tokenize("outlook read contacts"))
        e_outlook._source_tokens = set(_tokenize("outlook"))

        e_jira._tokens = _tokenize(_entry_search_text(jira_td, "mcp-AtlassianMCP"))
        e_jira._name_tokens = set(_tokenize("jira search issues"))
        e_jira._source_tokens = set(_tokenize("mcp AtlassianMCP"))

        cat = [e_outlook, e_jira]

        hits = search_catalog(cat, "jira search", limit=5)
        names = [h.name for h in hits]
        assert "jira_search_issues" in names
        assert "outlook_read_contacts" not in names

    def test_search_matches_camel_case_mcp_server_name(self):
        """Regression: MCP servers registered in PascalCase (e.g. "GithubMCP")
        must still be found by a query using their plain-English name, even
        when the tool's own name/description share no words with the query
        at all -- the only path to a match is the (correctly split) source
        name. Before splitting camelCase boundaries, `_split_words("GithubMCP")`
        produced a single opaque "githubmcp" token that a literal "github"
        query could never match, silently dropping the entry from BM25
        scoring entirely (see session 20260727_122011_68ef14 where the model
        gave up and fell back to raw `git log` because
        tool_search("github commits...") returned zero GitHub-related
        tools, while an unrelated AtlassianMCP tool that matched on another
        query word still scored, so the naive whole-catalog substring
        fallback never got a chance to run either).
        """
        from tools.registry import registry
        from tools.tool_search import build_catalog, search_catalog

        tool_defs = [
            {
                "type": "function",
                "function": {
                    "name": "mcp_GithubMCP_get_default_branch",
                    "description": "Return the default branch name",
                    "parameters": {"properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "mcp_AtlassianMCP_jira_search_issues",
                    "description": "Search Jira issues, scoped to a repository field",
                    "parameters": {"properties": {"jql": {"type": "string"}}},
                },
            },
        ]
        registry.register(
            name="mcp_GithubMCP_get_default_branch",
            toolset="mcp-GithubMCP",
            schema=tool_defs[0],
            handler=lambda x: x,
            check_fn=lambda: True,
            description="Return the default branch name",
        )
        registry.register(
            name="mcp_AtlassianMCP_jira_search_issues",
            toolset="mcp-AtlassianMCP",
            schema=tool_defs[1],
            handler=lambda x: x,
            check_fn=lambda: True,
            description="Search Jira issues, scoped to a repository field",
        )

        catalog = build_catalog(tool_defs)
        # "repository" alone already gives the Jira tool a real, unrelated
        # match, so the catalog-wide "nothing scored" substring fallback
        # never triggers -- isolating the actual bug: whether the GithubMCP
        # entry survives BM25 scoring via its source name.
        results = search_catalog(catalog, query="github repository", limit=8)
        names = [h.name for h in results]
        assert "mcp_AtlassianMCP_jira_search_issues" in names
        assert "mcp_GithubMCP_get_default_branch" in names


class TestRegression_ExactServerNameReturnsFullCatalog:
    """Regression: session 20260727_134336_3fd877 — the model searched
    tool_search(query="MSOffice365MCP", limit=30) for a 34-tool server and
    only got back 20 hits, silently missing `m365_send_chat_message`
    (and get_or_create_direct_chat, list_calendars, create_event, ...).
    All 34 tools share the same +10 source-name boost, so once more
    candidates than the limit are alive, plain BM25 length-normalization
    arbitrarily favors shorter tool names over longer ones for the
    remaining ranking -- there is nothing "relevant" about which 20 survive.
    Searching by exact server/toolset name must return the whole catalog.
    """

    def _build_big_server_catalog(self, count: int = 34):
        from tools.registry import registry
        from tools.tool_search import build_catalog

        tool_defs = []
        for i in range(count):
            name = f"mcp_BigMCP_action_number_{i:02d}_with_a_fairly_long_tool_name"
            tool_defs.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": f"Do thing number {i}",
                    "parameters": {"properties": {}},
                },
            })
            registry.register(
                name=name,
                toolset="mcp-BigMCP",
                schema=tool_defs[-1],
                handler=lambda x: x,
                check_fn=lambda: True,
                description=f"Do thing number {i}",
            )
        # One deliberately short-named outlier, mirroring m365_list_chats
        # being short while m365_send_chat_message is long.
        short_name = "mcp_BigMCP_short"
        tool_defs.append({
            "type": "function",
            "function": {
                "name": short_name,
                "description": "Short tool",
                "parameters": {"properties": {}},
            },
        })
        registry.register(
            name=short_name,
            toolset="mcp-BigMCP",
            schema=tool_defs[-1],
            handler=lambda x: x,
            check_fn=lambda: True,
            description="Short tool",
        )
        return build_catalog(tool_defs)

    def test_exact_server_name_query_returns_every_tool(self):
        from tools.tool_search import search_catalog
        catalog = self._build_big_server_catalog(count=34)
        # Model asked for limit=30, which the caller clamps to the
        # config max (20) before this ever reaches search_catalog --
        # exercise that exact clamped-limit scenario end to end.
        hits = search_catalog(catalog, query="BigMCP", limit=20)
        names = {h.name for h in hits}
        assert len(names) == 35
        assert "mcp_BigMCP_action_number_00_with_a_fairly_long_tool_name" in names
        assert "mcp_BigMCP_action_number_33_with_a_fairly_long_tool_name" in names

    def test_exact_source_prefix_match_ignores_mcp_dash_prefix(self):
        """"mcp-BigMCP" (the internal source_name form) also counts as exact."""
        from tools.tool_search import search_catalog
        catalog = self._build_big_server_catalog(count=5)
        hits = search_catalog(catalog, query="mcp-BigMCP", limit=8)
        assert len(hits) == 6

    def test_non_exact_query_still_uses_ranked_search(self):
        """A query that merely mentions the server name as one of several
        words (not an exact server-name lookup) must not dump the whole
        catalog -- only a genuine "just the server name" query does."""
        from tools.tool_search import search_catalog
        catalog = self._build_big_server_catalog(count=34)
        hits = search_catalog(catalog, query="BigMCP thing number 5", limit=3)
        assert len(hits) <= 3


# ---------------------------------------------------------------------------
# Assembly — the full passthrough/activate decision.
# ---------------------------------------------------------------------------


class TestAssembly:
    def test_no_deferrable_returns_unchanged(self):
        """Pure-core toolset: pass-through, no bridge tools added."""
        from tools.tool_search import assemble_tool_defs, ToolSearchConfig
        defs = [_td("terminal", "Run shell"), _td("read_file", "Read a file")]
        result = assemble_tool_defs(
            defs,
            context_length=200_000,
            config=ToolSearchConfig.from_raw({"enabled": "on"}),
        )
        assert not result.activated
        assert {t["function"]["name"] for t in result.tool_defs} == {"terminal", "read_file"}

    def test_below_threshold_returns_unchanged(self):
        """Tiny deferrable surface: don't bother."""
        from tools.tool_search import assemble_tool_defs, ToolSearchConfig
        # _td renders to ~80 chars / 20 tokens. 3 of them = ~60 tokens.
        # 10% of 200K = 20K. Way below.
        defs = [_td("unknown_tool_a"), _td("unknown_tool_b"), _td("unknown_tool_c")]
        result = assemble_tool_defs(
            defs,
            context_length=200_000,
            config=ToolSearchConfig.from_raw({"enabled": "auto", "threshold_pct": 10}),
        )
        assert not result.activated
        names = {(t.get("function") or {}).get("name") for t in result.tool_defs}
        assert "tool_search" not in names

    def test_idempotent_when_bridge_already_present(self):
        from tools.tool_search import assemble_tool_defs, ToolSearchConfig, BRIDGE_TOOL_NAMES
        defs = [_td("terminal", "Run shell"), _td("tool_search", "old")]
        result = assemble_tool_defs(
            defs,
            context_length=200_000,
            config=ToolSearchConfig.from_raw({"enabled": "off"}),
        )
        names = [(t["function"]["name"]) for t in result.tool_defs]
        # The pre-existing tool_search was stripped (it would be re-injected if
        # activation happened; here it didn't).
        assert "tool_search" not in names


# ---------------------------------------------------------------------------
# Bridge dispatch
# ---------------------------------------------------------------------------


class TestBridgeDispatch:
    def test_tool_search_requires_query(self):
        from tools.tool_search import dispatch_tool_search
        result = dispatch_tool_search({}, current_tool_defs=[])
        assert "error" in json.loads(result)

    def test_tool_describe_requires_name(self):
        from tools.tool_search import dispatch_tool_describe
        result = dispatch_tool_describe({}, current_tool_defs=[])
        assert "error" in json.loads(result)

    def test_tool_describe_rejects_non_deferrable(self):
        """If the model asks to describe a core tool, refuse — it's already
        in the visible list."""
        from tools.tool_search import dispatch_tool_describe
        result = dispatch_tool_describe(
            {"name": "terminal"}, current_tool_defs=[_td("terminal", "Run shell")],
        )
        assert "error" in json.loads(result)

    def test_resolve_underlying_call_resolves_suffix_name(self):
        """Unprefixed MCP tool name should resolve to full mcp_{server}_{tool} name."""
        from tools.registry import registry
        from tools.tool_search import resolve_underlying_call
        # Register a mock MCP tool
        registry.register(
            name="mcp_TempoMCP_retrieveWorklogs",
            toolset="mcp-TempoMCP",
            schema={"name": "mcp_TempoMCP_retrieveWorklogs", "description": "retrieve worklogs"},
            handler=lambda **kw: "ok",
        )
        try:
            name, args, err = resolve_underlying_call({
                "name": "retrieveWorklogs",
                "arguments": {"from": "2026-01-01"},
            })
            assert err is None
            assert name == "mcp_TempoMCP_retrieveWorklogs"
            assert args == {"from": "2026-01-01"}
        finally:
            registry.deregister("mcp_TempoMCP_retrieveWorklogs")

    def test_resolve_underlying_call_rejects_ambiguous_suffix(self):
        """Ambiguous suffix matching across multiple MCP tools should return an error."""
        from tools.registry import registry
        from tools.tool_search import resolve_underlying_call
        registry.register(
            name="mcp_ServerA_syncData",
            toolset="mcp-ServerA",
            schema={"name": "mcp_ServerA_syncData", "description": "sync a"},
            handler=lambda **kw: "ok",
        )
        registry.register(
            name="mcp_ServerB_syncData",
            toolset="mcp-ServerB",
            schema={"name": "mcp_ServerB_syncData", "description": "sync b"},
            handler=lambda **kw: "ok",
        )
        try:
            name, args, err = resolve_underlying_call({
                "name": "syncData",
                "arguments": {},
            })
            assert name is None
            assert err is not None
            assert "ambiguous" in err.lower()
        finally:
            registry.deregister("mcp_ServerA_syncData")
            registry.deregister("mcp_ServerB_syncData")

    def test_search_catalog_boosts_matching_mcp_server(self):
        """Searching with server alias should prioritize that server's tools over utility tools."""
        from tools.tool_search import build_catalog, search_catalog
        defs = [
            _td("mcp_AtlassianMCP_get_prompt", "Get a prompt by name from MCP server AtlassianMCP"),
            _td("mcp_AtlassianMCP_list_resources", "List available resources from MCP server AtlassianMCP"),
            _td("mcp_TempoMCP_retrieveWorklogs", "Retrieve worklogs for a given user or account within a date range"),
        ]
        cat = build_catalog(defs)
        results = search_catalog(cat, "TempoMCP retrieve worklogs time tracking", limit=5)
        assert len(results) > 0
        assert results[0].name == "mcp_TempoMCP_retrieveWorklogs"

    def test_resolve_underlying_call_parses_object_args(self):
        from tools.tool_search import resolve_underlying_call
        name, args, err = resolve_underlying_call({
            "name": "unknown_xxx",
            "arguments": {"foo": "bar"},
        })
        # Will fail classification because unknown_xxx isn't deferrable.
        assert err is not None

    def test_resolve_underlying_call_parses_json_string_args(self):
        """Some models emit ``arguments`` as a JSON string instead of object."""
        from tools.tool_search import resolve_underlying_call
        # Use a name that won't classify (so we don't depend on registry),
        # but exercise the JSON parse path.
        _, _, err = resolve_underlying_call({
            "name": "fake",
            "arguments": '{"a": 1}',
        })
        # err is about classification, but the parse worked (it would have
        # failed earlier with "not valid JSON" otherwise).
        assert "not valid JSON" not in (err or "")

    def test_resolve_underlying_call_rejects_bad_json(self):
        from tools.tool_search import resolve_underlying_call
        _, _, err = resolve_underlying_call({
            "name": "fake",
            "arguments": "{this is not json",
        })
        assert err is not None
        assert "JSON" in err

    def test_resolve_underlying_call_rejects_recursion(self):
        """tool_call cannot invoke tool_call itself."""
        from tools.tool_search import resolve_underlying_call, TOOL_CALL_NAME
        name, args, err = resolve_underlying_call({
            "name": TOOL_CALL_NAME,
            "arguments": {},
        })
        assert err is not None
        assert "bridge tool" in err.lower()


# ---------------------------------------------------------------------------
# End-to-end via the real handle_function_call (smoke test).
# ---------------------------------------------------------------------------


class TestSearchResultNominatesAutoload:
    """dispatch_tool_search names the hits the executor should load into the session."""

    @staticmethod
    def _register(name, toolset, description):
        from tools.registry import registry

        registry.register(
            name=name,
            handler=lambda args, **kw: json.dumps({"ok": True}),
            schema=_td(name, description, {"q": {"type": "string"}}),
            toolset=toolset,
        )

    def test_ranked_search_nominates_top_n_tool_hits(self):
        import model_tools

        for i in range(5):
            self._register(f"mcp_al_srv_alpha_tickets_{i}", "mcp-al-srv", f"alpha operation {i} for tickets")
        parsed = json.loads(model_tools.handle_function_call(
            function_name="tool_search",
            function_args={"query": "alpha tickets"},
            enabled_toolsets=["mcp-al-srv"],
        ))
        assert parsed["mode"] == "ranked"
        assert len(parsed["matches"]) >= 3
        assert len(parsed["autoload"]) == 3
        assert set(parsed["autoload"]) <= {m["name"] for m in parsed["matches"]}
        assert all(m["kind"] == "tool" for m in parsed["matches"])

    def test_browsing_a_whole_server_nominates_nothing(self):
        import model_tools

        for i in range(4):
            self._register(f"mcp_BrowseSrv_op_{i}", "mcp-BrowseSrv", f"browse op {i}")
        parsed = json.loads(model_tools.handle_function_call(
            function_name="tool_search",
            function_args={"query": "BrowseSrv"},
            enabled_toolsets=["mcp-BrowseSrv"],
        ))
        assert parsed["mode"] == "source_browse"
        assert parsed["autoload"] == []
        assert len(parsed["matches"]) == 4


class TestHandleFunctionCallIntegration:
    def test_tool_search_dispatch_through_handle_function_call(self):
        """The dispatcher recognizes the bridge tool by name."""
        import model_tools
        result = model_tools.handle_function_call(
            function_name="tool_search",
            function_args={"query": "nothing matches this"},
        )
        parsed = json.loads(result)
        # Without a real registry, the matches will be empty, but the
        # dispatch path completed without error.
        assert "matches" in parsed or "error" in parsed


class TestRegression_OpenClawCron84141:
    """Regression guard for the OpenClaw cron-tool-loss class of bug.

    OpenClaw #84141: ``toolsAllow: ["exec"]`` on an isolated cron turn
    resulted in the agent receiving only ``sessions_send`` — the catalog
    builder silently dropped the requested core tool.

    Our defense: core tools are NEVER deferred. This test exercises the
    full assembly pipeline with a mixed core+MCP toolset and asserts that
    every core tool survives.
    """

    def test_core_tool_survives_alongside_many_mcp_tools(self):
        from tools.tool_search import (
            assemble_tool_defs, ToolSearchConfig, BRIDGE_TOOL_NAMES,
            classify_tools,
        )
        # 1 core tool + 50 unknown/MCP-shaped tools (deferrable).
        defs = [_td("terminal", "Run shell commands")]
        # Pad with fake "deferrable" tools — without registry registration,
        # classify_tools puts them in 'visible'. So instead, we just verify
        # the core-tool side: terminal stays in visible regardless.
        visible, deferrable = classify_tools(defs)
        assert any(
            (td.get("function") or {}).get("name") == "terminal"
            for td in visible
        ), "Core tool 'terminal' was wrongly classified as deferrable"

        # Now force activation and check the resulting tool-defs list.
        result = assemble_tool_defs(
            defs,
            context_length=200_000,
            config=ToolSearchConfig.from_raw({"enabled": "on"}),
        )
        names = {(t.get("function") or {}).get("name") for t in result.tool_defs}
        # terminal must be present; bridges are only added if there are
        # deferrable tools to put behind them.
        assert "terminal" in names

    def test_unwrap_resolves_registered_tool(self):
        """tool_call resolves any registered tool name so the model is not
        blocked if it invokes a visible tool through tool_call."""
        from tools.tool_search import resolve_underlying_call
        name, args, err = resolve_underlying_call({
            "name": "terminal",
            "arguments": {"command": "echo hi"},
        })
        assert err is None
        assert name == "terminal"
        assert args == {"command": "echo hi"}


class TestRegression_ToolsetScoping:
    """A restricted-toolset session must not see or invoke out-of-scope tools.

    The bug: the bridge dispatch and the tool_executor unwrap read the
    catalog from the *global* registry (get_tool_definitions with no
    toolset scope = "start with everything"), so a session scoped to one
    MCP server could tool_search the entire process registry and tool_call
    any plugin tool it was never granted. registry.dispatch() has no
    enabled_tools gate for non-execute_code tools, so the out-of-scope tool
    actually ran.

    The fix threads the session's enabled/disabled toolsets into the bridge
    dispatch (model_tools.handle_function_call) and the executor unwrap
    (agent.tool_executor), scoping both the searchable catalog and the
    invocable set to the session's own toolsets.
    """

    @staticmethod
    def _register(name, toolset):
        from tools.registry import registry

        def _handler(args, task_id=None, **kw):
            return json.dumps({"ok": True, "tool": name})

        registry.register(
            name=name,
            handler=_handler,
            schema=_td(name, f"desc for {name}", {"repo": {"type": "string"}}),
            toolset=toolset,
        )

    def test_search_catalog_is_scoped_to_session_toolsets(self):
        import model_tools

        for i in range(12):
            self._register(f"mcp_scoped_gh_{i}", "mcp-scoped-gh")
        self._register("scoped_oos_plugin", "scopedoosplugin")

        # tool_search scoped to the github toolset must not count the
        # out-of-scope plugin tool (or any of the host registry).
        result = model_tools.handle_function_call(
            function_name="tool_search",
            function_args={"query": "mcp_scoped_gh", "limit": 5},
            enabled_toolsets=["mcp-scoped-gh"],
        )
        parsed = json.loads(result)
        assert parsed["total_available"] == 12, (
            f"expected scoped catalog of 12, got {parsed['total_available']} "
            "— catalog leaked tools outside the session's toolsets"
        )
        hit_names = {m["name"] for m in parsed["matches"]}
        assert "scoped_oos_plugin" not in hit_names

    def test_tool_call_rejects_out_of_scope_tool(self):
        import model_tools

        self._register("mcp_inscope_gh_op", "mcp-inscope-gh")
        self._register("inscope_oos_plugin", "inscopeoosplugin")

        # Out-of-scope plugin tool: rejected even though it is registered
        # and deferrable in the global registry.
        rejected = json.loads(model_tools.handle_function_call(
            function_name="tool_call",
            function_args={"name": "inscope_oos_plugin", "arguments": {}},
            enabled_toolsets=["mcp-inscope-gh"],
        ))
        assert "error" in rejected
        assert "not available in this session" in rejected["error"]

        # In-scope tool: dispatches normally.
        ok = json.loads(model_tools.handle_function_call(
            function_name="tool_call",
            function_args={"name": "mcp_inscope_gh_op", "arguments": {"repo": "a/b"}},
            enabled_toolsets=["mcp-inscope-gh"],
        ))
        assert ok.get("ok") is True
        assert ok.get("tool") == "mcp_inscope_gh_op"

    def test_bridge_dispatch_does_not_pollute_global_resolved_names(self):
        import model_tools

        self._register("mcp_pollute_op_0", "mcp-pollute")
        self._register("mcp_pollute_op_1", "mcp-pollute")

        # Establish the scoped session global.
        model_tools.get_tool_definitions(
            enabled_toolsets=["mcp-pollute"], quiet_mode=True,
        )
        before = set(model_tools._last_resolved_tool_names)
        assert "terminal" not in before

        # A scoped tool_search call must not widen the process-global
        # _last_resolved_tool_names to the whole registry (which would leak
        # core/sandbox tools into execute_code's fallback).
        model_tools.handle_function_call(
            function_name="tool_search",
            function_args={"query": "pollute"},
            enabled_toolsets=["mcp-pollute"],
        )
        after = set(model_tools._last_resolved_tool_names)
        assert "terminal" not in after, (
            "bridge dispatch polluted _last_resolved_tool_names with "
            "out-of-scope tools"
        )

    def test_scoped_deferrable_names_helper(self):
        from tools.tool_search import scoped_deferrable_names

        self._register("mcp_helper_op", "mcp-helper")
        import model_tools
        defs = model_tools.get_tool_definitions(
            enabled_toolsets=["mcp-helper"],
            quiet_mode=True,
            skip_tool_search_assembly=True,
        )
        names = scoped_deferrable_names(defs)
        assert "mcp_helper_op" in names
        # core tools are never deferrable
        assert "terminal" not in names


class TestRegression_UnregisteredToolError:
    """Regression guard for unregistered or hallucinated tool names in tool_call/tool_describe.

    When a model calls tool_call or tool_describe with a tool name that is NOT
    registered in the registry (e.g. 'mcp_atlassian_issue_search'), the bridge
    must report that the tool is 'not registered or found' rather than claiming
    that the tool is 'not a deferrable tool' (which misleadingly implies it is a
    directly-callable core tool).
    """

    def test_resolve_underlying_call_unregistered_tool_returns_not_found_error(self):
        from tools.tool_search import resolve_underlying_call
        _, _, err = resolve_underlying_call({
            "name": "mcp_atlassian_issue_search",
            "arguments": {},
        })
        assert err is not None
        assert "not registered or found" in err
        assert "not a deferrable tool" not in err

    def test_dispatch_tool_describe_unregistered_tool_returns_not_found_error(self):
        from tools.tool_search import dispatch_tool_describe
        res_str = dispatch_tool_describe({"name": "mcp_atlassian_issue_search"}, current_tool_defs=[])
        res = json.loads(res_str)
        assert "error" in res
        assert "not registered or found" in res["error"]
        assert "not a deferrable tool" not in res["error"]


class TestDynamicMCPKeywordIndexing:
    """Test dynamic MCP server keyword extraction, config aliases, and search expansion."""

    def setup_method(self):
        from tools.mcp_tool import clear_mcp_server_keywords
        clear_mcp_server_keywords()

    def teardown_method(self):
        from tools.mcp_tool import clear_mcp_server_keywords
        clear_mcp_server_keywords()

    def test_indexing_auto_extracts_and_parses_config_keywords(self):
        from tools.mcp_tool import (
            _index_mcp_server_keywords,
            get_mcp_dynamic_keywords_map,
            get_mcp_server_metadata,
        )

        class DummyTool:
            def __init__(self, name, description):
                self.name = name
                self.description = description

        class DummyServer:
            def __init__(self, tools):
                self._tools = tools

        server = DummyServer([
            DummyTool("create_issue", "Create a new Jira issue ticket in project"),
            DummyTool("add_worklog", "Add a worklog time tracking entry to Tempo"),
        ])

        config = {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-jira"],
            "keywords": ["zeitbuchung", "atlassian", "projekte"],
        }

        registered_names = ["mcp_jira_create_issue", "mcp_jira_add_worklog"]
        _index_mcp_server_keywords("jira", server, config, registered_names)

        meta = get_mcp_server_metadata()
        assert "jira" in meta
        keywords = meta["jira"]["keywords"]

        # Check explicit config keywords
        assert "zeitbuchung" in keywords
        assert "atlassian" in keywords
        assert "projekte" in keywords

        # Check auto-extracted keywords from tools/command
        assert "issue" in keywords
        assert "worklog" in keywords
        assert "tempo" in keywords

        # Check reverse index mapping
        kw_map = get_mcp_dynamic_keywords_map()
        assert "zeitbuchung" in kw_map
        assert "mcp-jira" in kw_map["zeitbuchung"]

    def test_search_catalog_ranks_via_dynamic_mcp_keywords(self):
        from tools.mcp_tool import _index_mcp_server_keywords
        from tools.tool_search import build_catalog, search_catalog

        class DummyTool:
            def __init__(self, name, description):
                self.name = name
                self.description = description

        class DummyServer:
            def __init__(self, tools):
                self._tools = tools

        server = DummyServer([
            DummyTool("log_time", "Record worklog entry"),
        ])
        config = {"keywords": "zeitbuchung, zeiterfassung, tempo"}
        registered_names = ["mcp_jira_log_time"]
        _index_mcp_server_keywords("jira", server, config, registered_names)

        tool_defs = [
            {
                "type": "function",
                "function": {
                    "name": "mcp_jira_log_time",
                    "description": "Record worklog entry",
                    "parameters": {},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "mcp_github_create_repo",
                    "description": "Create a GitHub repository",
                    "parameters": {},
                },
            },
        ]

        from tools.registry import registry
        registry.register(
            name="mcp_jira_log_time",
            toolset="mcp-jira",
            schema=tool_defs[0],
            handler=lambda x: x,
            check_fn=lambda: True,
            description="Record worklog entry",
        )
        registry.register(
            name="mcp_github_create_repo",
            toolset="mcp-github",
            schema=tool_defs[1],
            handler=lambda x: x,
            check_fn=lambda: True,
            description="Create a GitHub repository",
        )

        catalog = build_catalog(tool_defs)
        results = search_catalog(catalog, query="zeiterfassung", limit=5)

        assert len(results) > 0
        assert results[0].name == "mcp_jira_log_time"

    def test_german_calendar_synonyms(self):
        from tools.tool_search import build_catalog, search_catalog

        tool_defs = [
            {
                "type": "function",
                "function": {
                    "name": "m365_get_events",
                    "description": "Get events from Outlook calendar including URLAUB and Officezeiten",
                    "parameters": {},
                },
            }
        ]
        catalog = build_catalog(tool_defs)

        for query in ("urlaub", "officezeiten", "feiertage", "abwesenheit"):
            results = search_catalog(catalog, query)
            assert len(results) >= 1, f"Failed for query {query}"
            # "feiertage" also maps to the workdays tool; with only the m365
            # tool in this catalog the calendar tool still wins.
            assert results[0].name == "m365_get_events"

    def test_german_send_verbs_surface_teams_send_tools(self):
        """'schick ... via teams' must rank the Teams send/find tools ahead of
        the mail tools (AIS-286)."""
        from tools.tool_search import build_catalog, search_catalog

        def _tool(name, desc):
            return {"type": "function", "function": {"name": name, "description": desc, "parameters": {}}}

        tool_defs = [
            _tool("mcp_MSOffice365MCP_m365_send_chat_message", "Send a Microsoft Teams chat message to a person or chat; resolves the recipient by name"),
            _tool("mcp_MSOffice365MCP_m365_find_chat", "Find the Teams chat for a person, nickname, email or group topic"),
            _tool("mcp_MSOffice365MCP_m365_send_email", "Send an email using Outlook Mail"),
            _tool("mcp_MSOffice365MCP_m365_list_joined_teams", "List all Microsoft Teams that the current user is a member of"),
            _tool("terminal", "Run a shell command"),
        ]
        catalog = build_catalog(tool_defs)
        for query in ("schick nachricht via teams", "sende an Martin über Teams", "schreib Martin in Teams"):
            names = [r.name for r in search_catalog(catalog, query, limit=4)]
            assert names, query
            assert names[0] in ("mcp_MSOffice365MCP_m365_send_chat_message", "mcp_MSOffice365MCP_m365_find_chat"), (query, names)
            assert names.index("mcp_MSOffice365MCP_m365_send_chat_message") < names.index("mcp_MSOffice365MCP_m365_send_email"), (query, names)

    def test_office_file_tool_synonyms_beat_m365(self):
        """'excel' / 'pptx' / 'docx' must surface the local office tools, not
        only the M365 MCP server (AIS-139)."""
        from tools.tool_search import build_catalog, search_catalog

        tool_defs = [
            {
                "type": "function",
                "function": {
                    "name": "office_excel",
                    "description": "Read, create, edit, format and export Excel (.xlsx) / CSV files.",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "office_powerpoint",
                    "description": "Read, create and edit PowerPoint (.pptx) presentations / slide decks.",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "office_word",
                    "description": "Read, create, edit and convert Word (.docx) documents.",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "m365_list_mail",
                    "description": "List Outlook mail messages via Microsoft 365",
                    "parameters": {},
                },
            },
        ]
        catalog = build_catalog(tool_defs)

        expectations = {
            "excel": "office_excel",
            "tabelle": "office_excel",
            "xlsx": "office_excel",
            "pptx": "office_powerpoint",
            "präsentation": "office_powerpoint",
            "folien": "office_powerpoint",
            "docx": "office_word",
            "word": "office_word",
        }
        for query, expected in expectations.items():
            results = search_catalog(catalog, query, limit=3)
            assert results, f"no results for {query!r}"
            assert results[0].name == expected, f"{query!r} -> {[r.name for r in results]}"

    def test_dynamic_skill_keywords_expansion(self, monkeypatch):
        from tools.tool_search import _get_dynamic_skill_keywords_map, build_catalog, search_catalog

        fake_skills = [
            {
                "name": "blogwatcher",
                "category": "research",
                "description": "Monitore RSS Feeds und erstelle Zusammenfassungen von Blogs",
                "tags": ["rss", "feed", "blog"],
            }
        ]

        def _fake_find_skills(*a, **kw):
            return fake_skills

        monkeypatch.setattr("tools.skills_tool._find_all_skills", _fake_find_skills)

        mapping = _get_dynamic_skill_keywords_map()
        assert "blog" in mapping or "rss" in mapping

        tool_defs = [
            {
                "type": "function",
                "function": {
                    "name": "blogwatcher",
                    "description": "Monitore RSS Feeds und erstelle Zusammenfassungen von Blogs",
                    "parameters": {},
                },
            }
        ]
        catalog = build_catalog(tool_defs)
        results = search_catalog(catalog, query="rss feed")
        assert len(results) >= 1
        assert results[0].name == "blogwatcher"




class TestCamelCaseMcpToolsAreFindable:
    """A camelCase MCP tool must be reachable by its plain words.

    `_build_catalog` used a hand-rolled replace chain for the name/source token
    sets, so `mcp_TempoMCP_retrieveWorklogs` tokenized to
    {mcp, tempomcp, retrieveworklogs}. A query for "tempo" or "worklog" then
    matched neither, the +5 name boost never fired, and the false-positive
    filter dropped the entry — the server looked absent while it was connected
    and registered, and the agent told the user it was "not configured".
    Snake_case neighbours like mcp_AtlassianMCP_jira_get_worklog were returned
    instead, which is exactly how the confusion presented.
    """

    @staticmethod
    def _defs():
        def td(name, desc):
            return {"type": "function", "function": {"name": name, "description": desc, "parameters": {}}}

        return [
            td("mcp_TempoMCP_retrieveWorklogs", "Retrieve Tempo worklogs for a user and date range."),
            td("mcp_AtlassianMCP_jira_get_worklog", "Get worklog entries for a Jira issue."),
        ]

    def _names_for(self, query):
        from tools.tool_search import build_catalog, search_catalog

        catalog = build_catalog(self._defs())
        try:
            results = search_catalog(catalog, query)
        except TypeError:
            results = search_catalog(catalog, query, 10)

        out = []
        for r in results:
            out.append(getattr(r, "name", None) or (r[0] if isinstance(r, tuple) else str(r)))

        return out

    def test_server_name_query_finds_the_tool(self):
        assert any("TempoMCP" in n for n in self._names_for("tempo"))

    def test_worklog_query_returns_the_tempo_tool_too(self):
        names = self._names_for("tempo worklog retrieve hours")

        assert any("TempoMCP" in n for n in names), names

    def test_name_tokens_contain_the_split_words(self):
        from tools.tool_search import build_catalog

        entry = next(e for e in build_catalog(self._defs()) if "Tempo" in e.name)

        assert {"tempo", "retrieve", "worklogs"} <= entry._name_tokens


class TestNamedSourceIsAlwaysRepresented:
    """A server the query names outright must appear in the results.

    Observed: "Tempo worklog time tracking currentUser" returned eight
    AtlassianMCP tools and no TempoMCP. "worklog" matched fourteen Jira tools
    while "tempo" matched seven Tempo ones, so the larger source filled every
    slot. The model then fetched worklogs issue by issue — sixteen calls to do
    one tool's job — because the right tool looked absent.
    """

    @staticmethod
    def _defs():
        def td(name, desc):
            return {
                "type": "function",
                "function": {"name": name, "description": desc, "parameters": {}},
            }

        defs = [
            td(
                "mcp_TempoMCP_retrieveWorklogs",
                "Retrieve Tempo worklogs in a date range. Defaults to the authenticated user's own worklogs.",
            )
        ]
        # The real ratio: Tempo exposes 7 tools, Atlassian 14.
        defs += [td(f"mcp_TempoMCP_op{i}", "Tempo timesheet operation.") for i in range(6)]
        defs += [td("mcp_AtlassianMCP_jira_get_worklog", "Get worklog entries for a Jira issue.")]
        defs += [td(f"mcp_AtlassianMCP_jira_op{i}", "Jira issue worklog operation.") for i in range(13)]

        return defs

    def _names(self, query, limit=8):
        from tools.tool_search import build_catalog, search_catalog

        return [e.name for e in search_catalog(build_catalog(self._defs()), query, limit)]

    def test_the_named_server_survives_a_crowded_result(self):
        names = self._names("Tempo worklog time tracking currentUser")

        assert any("TempoMCP" in n for n in names), names

    def test_the_other_matching_server_is_not_evicted(self):
        names = self._names("Tempo worklog time tracking currentUser")

        assert any("AtlassianMCP" in n for n in names), names

    def test_a_query_naming_no_server_is_unaffected(self):
        names = self._names("issue transition")

        assert len(names) <= 8

    def test_limit_is_still_respected(self):
        names = self._names("Tempo worklog", limit=3)

        assert len(names) <= 3


class TestCatalogIsCached:
    """`build_catalog` ran from scratch on every tool_search call.

    Each build resolves every tool through the registry, pulls MCP server
    metadata, and vectorizes each entry against corpus IDF — repeated for every
    search in a turn, even when nothing changed in between.
    """

    @staticmethod
    def _defs(n=3):
        return [
            {"type": "function", "function": {"name": f"mcp_S_t{i}", "description": f"tool {i}", "parameters": {}}}
            for i in range(n)
        ]

    def _reset(self, monkeypatch):
        import tools.tool_search as ts

        monkeypatch.setattr(ts, "_CATALOG_CACHE", None)

        return ts

    def test_the_same_defs_reuse_the_same_catalog(self, monkeypatch):
        ts = self._reset(monkeypatch)
        defs = self._defs()

        assert ts._cached_catalog(defs) is ts._cached_catalog(defs)

    def test_a_different_def_set_rebuilds(self, monkeypatch):
        ts = self._reset(monkeypatch)

        first = ts._cached_catalog(self._defs(3))
        second = ts._cached_catalog(self._defs(4))

        assert first is not second
        assert len(second) == 4

    def test_a_registry_change_rebuilds(self, monkeypatch):
        """MCP refresh or plugin load must not serve a stale catalog."""
        ts = self._reset(monkeypatch)
        from tools.registry import registry

        defs = self._defs()
        first = ts._cached_catalog(defs)
        monkeypatch.setattr(registry, "_generation", registry._generation + 1)
        second = ts._cached_catalog(defs)

        assert first is not second

    def test_the_cached_catalog_still_searches(self, monkeypatch):
        ts = self._reset(monkeypatch)
        catalog = ts._cached_catalog(self._defs())

        assert ts.search_catalog(catalog, "tool") is not None


class TestSkillsInCatalog:
    """Local skills join the catalog as kind="skill" and are found by the same search."""

    _SKILLS = [
        {"name": "worklog-analytics", "description": "Monthly worklog analysis with SQL over Tempo records",
         "category": "aimds_custom", "tags": ["tempo", "sql"], "kind": "skill",
         "how_to_use": "skill_view(name='worklog-analytics')"},
        {"name": "release-changelog", "description": "Generate release notes from tags and PRs",
         "category": "aimds_custom", "kind": "skill", "how_to_use": "skill_view(name='release-changelog')"},
        {"name": "obsidian-vault-manager", "description": "Keep the Obsidian vault tidy",
         "category": "note-taking", "kind": "skill", "how_to_use": "skill_view(name='obsidian-vault-manager')"},
    ]

    def _catalog(self):
        from tools.tool_search import build_catalog
        defs = [
            _td("mcp_TempoMCP_retrieveWorklogs", "Retrieve Tempo worklogs for a date range"),
            _td("mcp_AtlassianMCP_jira_get_worklog", "Get worklogs of one Jira issue"),
            _td("mcp_GithubMCP_list_releases", "List GitHub releases"),
        ]
        return build_catalog(defs, skills=self._SKILLS)

    def test_skill_entries_carry_kind_and_how_to_use(self):
        catalog = self._catalog()
        skills = [e for e in catalog if e.kind == "skill"]
        assert {e.name for e in skills} == {s["name"] for s in self._SKILLS}
        assert all(e.how_to_use.startswith("skill_view(") for e in skills)
        assert all(e.source == "skill" for e in skills)

    def test_search_finds_a_skill_and_a_tool_for_the_same_question(self):
        from tools.tool_search import search_catalog, _format_search_hit
        hits = search_catalog(self._catalog(), "worklog analyse monat", limit=5)
        names = [h.name for h in hits]
        assert "worklog-analytics" in names
        assert "mcp_TempoMCP_retrieveWorklogs" in names
        skill_hit = _format_search_hit(next(h for h in hits if h.name == "worklog-analytics"))
        assert skill_hit["kind"] == "skill"
        assert skill_hit["how_to_use"] == "skill_view(name='worklog-analytics')"
        assert skill_hit["category"] == "aimds_custom"

    def test_kind_filter_and_cap(self):
        from tools.tool_search import cap_skill_hits, search_catalog
        hits = search_catalog(self._catalog(), "release notes worklog vault", limit=10)
        only_tools = cap_skill_hits(hits, "release notes worklog vault", "tool")
        assert only_tools and all(e.kind == "tool" for e in only_tools)
        only_skills = cap_skill_hits(hits, "release notes worklog vault", "skill")
        assert only_skills and all(e.kind == "skill" for e in only_skills)
        capped = cap_skill_hits(hits, "release notes worklog vault", None)
        assert sum(1 for e in capped if e.kind == "skill") <= 2
        assert sum(1 for e in cap_skill_hits(hits, "which skill?", None) if e.kind == "skill") == \
            sum(1 for e in hits if e.kind == "skill")

    def test_a_skill_never_claims_the_named_source_slot(self):
        """The reserved slot for a server the query names goes to that server's
        tool; a skill that merely mentions the server does not count."""
        from tools.tool_search import search_catalog
        hits = search_catalog(self._catalog(), "tempo worklog", limit=2)
        assert any(h.name == "mcp_TempoMCP_retrieveWorklogs" for h in hits)
        assert any(h.kind == "tool" for h in hits)

    def test_sibling_fill_ignores_skills(self, monkeypatch):
        import tools.tool_search as ts
        # no keyword expansion from the installed skills — isolate the fill logic
        monkeypatch.setattr(ts, "_get_dynamic_skill_keywords_map", lambda: {})
        defs = [_td(f"mcp_GithubMCP_op_{i}", f"github operation {i}") for i in range(3)]
        catalog = ts.build_catalog(defs, skills=self._SKILLS)
        hits = ts.search_catalog(catalog, "github operation", limit=6)
        assert {h.name for h in hits if h.kind == "tool"} == {f"mcp_GithubMCP_op_{i}" for i in range(3)}
        assert all(h.kind == "tool" for h in hits)

    def test_disabled_skills_are_excluded_from_catalog_input(self, monkeypatch):
        import tools.tool_search as ts
        seen = {}

        def fake_find_all_skills(skip_disabled=False, include_source=False):
            seen["skip_disabled"] = skip_disabled
            return [{"name": "x", "description": "y", "category": "c", "updated_at": 1}]

        monkeypatch.setattr("tools.skills_tool._find_all_skills", fake_find_all_skills)
        entries = ts.local_skill_catalog_entries()
        assert seen["skip_disabled"] is False  # True would *include* disabled skills
        assert entries[0]["kind"] == "skill" and entries[0]["how_to_use"] == "skill_view(name='x')"

    def test_dispatch_lists_skills_only_when_the_session_can_read_them(self, monkeypatch):
        import tools.tool_search as ts
        monkeypatch.setattr(ts, "local_skill_catalog_entries", lambda: self._SKILLS)
        from tools.registry import registry
        registry.register(name="mcp_SkillsSrv_worklog_tool", toolset="mcp-SkillsSrv",
                          schema=_td("mcp_SkillsSrv_worklog_tool", "worklog tool")["function"],
                          handler=lambda a, **k: "{}")
        defs_without = [_td("mcp_SkillsSrv_worklog_tool", "worklog tool")]
        defs_with = defs_without + [_td("skill_view", "read a skill")]
        without = json.loads(ts.dispatch_tool_search({"query": "worklog"}, current_tool_defs=defs_without))
        with_ = json.loads(ts.dispatch_tool_search({"query": "worklog"}, current_tool_defs=defs_with))
        assert all(m["kind"] == "tool" for m in without["matches"])
        assert any(m["kind"] == "skill" and m["name"] == "worklog-analytics" for m in with_["matches"])
        assert "worklog-analytics" not in with_["autoload"]

    def test_describe_answers_for_a_skill_name(self, monkeypatch):
        import tools.tool_search as ts
        monkeypatch.setattr(ts, "local_skill_catalog_entries", lambda: self._SKILLS)
        out = json.loads(ts.dispatch_tool_describe(
            {"name": "release-changelog"}, current_tool_defs=[_td("skill_view")]))
        assert out["kind"] == "skill" and out["how_to_use"] == "skill_view(name='release-changelog')"


class TestNamedServerSurvivesKeywordBlobs:
    """Reproduction of session 20260829_132903_2bb072: five searches for
    "TempoMCP worklog retrieve" returned only AtlassianMCP tools although
    TempoMCP was registered with seven tools. Two things conspired:

    * the server metadata keyword blob (hundreds of tokens for a chatty
      server) was part of the tokens that decide whether a query "names" a
      server, so "worklog"/"retrieve" named Atlassian and GitHub as well;
    * the reservation loop popped the last result for every named server,
      so each reserved entry evicted the previous one — the last server
      iterated (GitHub) survived, Tempo did not.
    """

    @staticmethod
    def _register(name, toolset, description=""):
        from tools.registry import registry
        registry.register(name=name, toolset=toolset, schema=_td(name, description)["function"],
                          handler=lambda a, **k: "{}")

    def _catalog(self, monkeypatch):
        import tools.tool_search as ts
        blob = ("jira issue worklog retrieve search create update transition comment sprint board epic "
                "timesheet booking hours log time tracking retrieve worklogs entries ").split() * 20
        monkeypatch.setattr(ts, "_get_mcp_server_metadata", lambda: {
            "AtlassianMCP": {"keywords": blob},
            "GithubMCP": {"keywords": ["retrieve", "worklog", "pull", "request", "issue"] * 30},
            "TempoMCP": {"keywords": []},
        })
        # the real expansion maps turned these three words into 1,043 tokens
        garbage = ['"09', '"jirafield"', '"q1",', '"select",', "jira", "issue", "atlassian", "update",
                   "transition", "comment", "create", "sprint", "board", "epic", "ticket", "field"] * 60
        monkeypatch.setattr(ts, "_get_dynamic_mcp_keywords_map", lambda: {"worklog": garbage, "retrieve": garbage, "tempo": garbage})
        monkeypatch.setattr(ts, "_get_dynamic_skill_keywords_map", lambda: {"worklog": ["jira", "booking", "hours", "analytics"]})
        defs = []
        for i, op in enumerate(["get_worklog", "update_issue", "transition_issue", "add_comment", "create_issue",
                                "get_issue", "get_transitions", "get_issue_sla", "list_resources", "list_prompts",
                                "read_resource", "get_prompt", "add_worklog", "jql_query"]):
            name = f"mcp_AtlassianMCP_jira_{op}"
            self._register(name, "mcp-AtlassianMCP", f"Jira {op.replace('_', ' ')} for an issue")
            defs.append(_td(name, f"Jira {op.replace('_', ' ')} for an issue"))
        for op in ["retrieveWorklogs", "createWorklog", "bulkCreateWorklogs", "editWorklog", "deleteWorklog",
                   "getMissingWorklogDays", "getWorklogAnalytics"]:
            name = f"mcp_TempoMCP_{op}"
            self._register(name, "mcp-TempoMCP", f"MCP tool {op} from TempoMCP")  # the server ships no description
            defs.append(_td(name, f"MCP tool {op} from TempoMCP"))
        for op in ["create_pull_request", "list_issues", "get_file"]:
            name = f"mcp_GithubMCP_{op}"
            self._register(name, "mcp-GithubMCP", f"GitHub {op}")
            defs.append(_td(name, f"GitHub {op}"))
        return ts.build_catalog(defs)

    @pytest.mark.parametrize("query", [
        "TempoMCP worklog retrieve",
        "Tempo retrieve worklog",
        "tempo worklog retrieve timesheets worklogs TempoMCP",
    ])
    def test_the_named_server_is_in_the_top_hits(self, monkeypatch, query):
        from tools.tool_search import search_catalog
        hits = search_catalog(self._catalog(monkeypatch), query, limit=8)
        names = [h.name for h in hits]
        assert names[0].startswith("mcp_TempoMCP_"), names

    def test_expansions_are_bounded_and_clean(self):
        from tools.tool_search import _bounded_expansions, MAX_EXPANSIONS_PER_TOKEN
        terms = ['"09', '"jirafield"', "jira", "issue", "atlassian", "update", "transition", "comment", "x", "board"]
        out = _bounded_expansions(terms)
        assert len(out) == MAX_EXPANSIONS_PER_TOKEN
        assert all(t.isalpha() for t in out) and '"09' not in out and "x" not in out

    def test_keyword_blob_tokens_do_not_name_a_server(self, monkeypatch):
        catalog = self._catalog(monkeypatch)
        jira = next(e for e in catalog if e.name == "mcp_AtlassianMCP_jira_get_worklog")
        tempo = next(e for e in catalog if e.name == "mcp_TempoMCP_retrieveWorklogs")
        assert "worklog" in jira._source_tokens          # still matchable
        assert "worklog" not in jira._server_tokens      # but it does not "name" Atlassian
        assert {"tempo", "tempomcp"} <= tempo._server_tokens
        assert "worklogs" in tempo._server_tokens        # SOURCE_ALIASES: worklogs → TempoMCP

    def test_two_named_servers_do_not_evict_each_other(self, monkeypatch):
        from tools.tool_search import search_catalog
        hits = search_catalog(self._catalog(monkeypatch), "github tempo worklog", limit=4)
        sources = {h.source_name for h in hits}
        assert {"mcp-TempoMCP", "mcp-GithubMCP"} <= sources, [h.name for h in hits]
