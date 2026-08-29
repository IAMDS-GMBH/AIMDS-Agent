"""agent/memory_facade.py — one door for every memory writer.

Mode matrix, the vault fallback (frontmatter-conformant notes that the index
finds again), the compaction summary and the session-end summary, and the
tool surface (`vault_memory` only in vault mode, local `memory` only when no
vault backend exists).
"""

from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

import pytest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agent import memory_facade as mf  # noqa: E402


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A shipped-style workspace as the agent cwd, and an isolated HERMES_HOME."""
    root = tmp_path / "vault"
    root.mkdir()
    (root / "_conventions.md").write_text("---\ntype: conventions\n---\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("agent.runtime_cwd.resolve_agent_cwd", lambda: root)
    return root


def _agent(names):
    return SimpleNamespace(valid_tool_names=set(names), session_id="sess-1", enabled_toolsets=None, disabled_toolsets=None)


class TestModeMatrix:
    def test_mcp_wins_when_the_primary_context_tool_is_in_the_session(self, workspace):
        assert mf.resolve_mode({"mcp_AIMDSSuiteMCP_mcp_memory_memory_context", "terminal"}) == mf.MODE_MCP

    def test_vault_when_no_mcp_but_a_workspace(self, workspace):
        assert mf.resolve_mode({"terminal", "read_file"}) == mf.MODE_VAULT

    def test_none_without_workspace_or_mcp(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
        monkeypatch.setattr("agent.runtime_cwd.resolve_agent_cwd", lambda: tmp_path)  # no markers
        monkeypatch.setattr(mf, "primary_memory_context_registered", lambda: None)
        assert mf.resolve_mode({"terminal"}) == mf.MODE_NONE

    def test_a_custom_memory_server_is_not_a_backend(self, workspace):
        # only the primary server's memory_context resolves; a custom server's does not
        assert mf.resolve_mode({"mcp_EnwicklerMemoryMCP_memory_context"}) == mf.MODE_VAULT

    def test_config_can_force_the_vault(self, workspace, monkeypatch):
        monkeypatch.setattr(mf, "_memory_backend_config", lambda: {"backend": "vault"})
        assert mf.resolve_mode({"mcp_AIMDSSuiteMCP_mcp_memory_memory_context"}) == mf.MODE_VAULT


class TestVaultMode:
    def test_save_writes_a_conformant_note_that_search_finds(self, workspace):
        facade = mf.MemoryFacade.for_agent(_agent({"terminal"}))
        assert facade.mode == mf.MODE_VAULT
        res = facade.save(
            title="EVN time booking rule",
            content="All ECO work is booked to EXT-95 with the comment 'ECO-XXX: title'.",
            type="rule", tags=["evn", "worklog"],
        )
        assert res.ok and res.backend == mf.MODE_VAULT
        path = workspace / res.ref
        assert path.is_file() and res.ref.startswith("knowledge/")
        text = path.read_text(encoding="utf-8")
        # _conventions.md schema: closed type vocabulary, YYYY-MM-DD, YAML list tags;
        # the memory type "rule" survives as a tag.
        for key in ("type: knowledge", 'title: "EVN time booking rule"', "tags:\n  - evn\n  - worklog\n  - rule"):
            assert key in text, key
        import re as _re
        assert _re.search(r"^created: \d{4}-\d{2}-\d{2}$", text, _re.M) and _re.search(r"^updated: \d{4}-\d{2}-\d{2}$", text, _re.M)

        hits = facade.search("EXT-95 booking")
        assert hits and hits[0]["title"] == "EVN time booking rule"
        assert facade.read(res.ref).startswith("---")

    def test_save_is_an_upsert_by_title(self, workspace):
        facade = mf.MemoryFacade.for_agent(_agent({"terminal"}))
        first = facade.save(title="Decision: sql toolset", content="v1", type="decision")
        second = facade.save(title="Decision: sql toolset", content="v2", type="decision")
        assert first.ref == second.ref and second.ref.startswith("decisions/")
        assert "v2" in (workspace / second.ref).read_text(encoding="utf-8")
        assert len(list((workspace / "decisions").glob("*.md"))) == 1

    def test_session_summary_lands_in_the_journal(self, workspace):
        facade = mf.MemoryFacade.for_agent(_agent({"terminal"}))
        res = facade.summarize_session(summary="Fixed the sql toolset.", decisions=["sql is configurable"], tags=["hermes"], session_id="s42")
        assert res.ok and res.ref.startswith("journal/sessions/")
        text = (workspace / res.ref).read_text(encoding="utf-8")
        assert "## Decisions" in text and "sql is configurable" in text


class TestMcpMode:
    def test_save_goes_through_the_primary_servers_tool(self, workspace, monkeypatch):
        calls = []

        def fake_handle(name, args, task_id, **kw):
            calls.append((name, args))
            return json.dumps({"saved": True, "slug": "evn-rule"})

        import run_agent
        monkeypatch.setattr(run_agent, "handle_function_call", fake_handle)
        facade = mf.MemoryFacade.for_agent(_agent({
            "mcp_AIMDSSuiteMCP_mcp_memory_memory_context",
            "mcp_AIMDSSuiteMCP_mcp_memory_memory_save",
        }))
        res = facade.save(title="EVN rule", content="…", type="rule", tags=["evn"])
        assert res.ok and res.backend == mf.MODE_MCP and res.ref == "evn-rule"
        assert calls[0][0] == "mcp_AIMDSSuiteMCP_mcp_memory_memory_save"
        assert calls[0][1]["type"] == "rule"

    def test_failed_mcp_save_falls_through_to_the_vault(self, workspace, monkeypatch):
        import run_agent
        monkeypatch.setattr(run_agent, "handle_function_call", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("server gone")))
        facade = mf.MemoryFacade.for_agent(_agent({
            "mcp_AIMDSSuiteMCP_mcp_memory_memory_context",
            "mcp_AIMDSSuiteMCP_mcp_memory_memory_save",
        }))
        res = facade.save(title="Survives an outage", content="fact", type="notes")
        assert res.ok and res.backend == mf.MODE_VAULT
        assert (workspace / res.ref).is_file()


class TestSessionSummary:
    def _messages(self, turns):
        msgs = []
        for i in range(turns):
            msgs.append({"role": "user", "content": f"question {i}"})
            msgs.append({"role": "assistant", "content": f"answer {i}"})
        return msgs

    def _fake_llm(self, monkeypatch):
        class _R:
            choices = [SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                "summary": "The user asked five questions and got five answers.",
                "decisions": ["keep asking"], "tags": ["test"]})))]
        import agent.auxiliary_client as aux
        monkeypatch.setattr(aux, "call_llm", lambda **kw: _R())

    def test_short_sessions_are_not_summarized(self, workspace, monkeypatch):
        self._fake_llm(monkeypatch)
        agent = _agent({"terminal"})
        assert mf.summarize_session_into_memory(agent, self._messages(2), reason="session_end") is None

    def test_a_real_session_is_summarized_exactly_once(self, workspace, monkeypatch):
        self._fake_llm(monkeypatch)
        agent = _agent({"terminal"})
        res = mf.summarize_session_into_memory(agent, self._messages(5), reason="session_end")
        assert res is not None and res.ok and res.ref.startswith("journal/sessions/")
        assert "keep asking" in (workspace / res.ref).read_text(encoding="utf-8")
        assert mf.summarize_session_into_memory(agent, self._messages(5), reason="session_end") is None


class TestCompactionSummary:
    def test_compaction_summary_is_saved_and_the_transcript_points_at_it(self, workspace):
        from agent.conversation_compression import _persist_compaction_summary
        from agent.context_compressor import SUMMARY_PREFIX

        agent = _agent({"terminal"})
        agent.context_compressor = SimpleNamespace(_previous_summary="Goal: fix sql. Progress: done.")
        agent.platform = "cli"
        compressed = [{"role": "system", "content": "sys"}, {"role": "user", "content": SUMMARY_PREFIX + "\nGoal: fix sql."}]
        _persist_compaction_summary(agent, compressed)
        notes = list((workspace / "journal" / "sessions").glob("*.md"))
        assert len(notes) == 1 and "compaction-1" in notes[0].name
        assert "memory vault" in compressed[1]["content"] and "journal/sessions/" in compressed[1]["content"]


class TestToolSurface:
    def test_vault_memory_tool_only_in_vault_mode(self, workspace, monkeypatch):
        from tools.vault_memory_tool import check_vault_memory_requirements
        from tools.memory_tool import check_memory_requirements

        monkeypatch.setattr(mf, "primary_memory_context_registered", lambda: None)
        assert check_vault_memory_requirements() is True
        assert check_memory_requirements() is False  # the vault exists → local store hidden

        monkeypatch.setattr(mf, "primary_memory_context_registered", lambda: "mcp_AIMDSSuiteMCP_mcp_memory_memory_context")
        assert check_vault_memory_requirements() is False

    def test_local_memory_tool_returns_when_nothing_else_exists(self, tmp_path, monkeypatch):
        from tools.memory_tool import check_memory_requirements
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
        monkeypatch.setattr("agent.runtime_cwd.resolve_agent_cwd", lambda: tmp_path)
        monkeypatch.setattr(mf, "primary_memory_context_registered", lambda: None)
        assert check_memory_requirements() is True

    def test_vault_memory_tool_round_trip(self, workspace, monkeypatch):
        from tools.vault_memory_tool import vault_memory_tool
        monkeypatch.setattr(mf, "primary_memory_context_registered", lambda: None)
        out = json.loads(vault_memory_tool({"action": "save", "title": "Prefers tables", "content": "User prefers markdown tables.", "type": "profile"}))
        assert out["saved"] and out["ref"].startswith("users/")
        found = json.loads(vault_memory_tool({"action": "search", "query": "markdown tables"}))
        assert found["count"] >= 1 and found["results"][0]["title"] == "Prefers tables"
        read = json.loads(vault_memory_tool({"action": "read", "slug": out["ref"]}))
        assert "markdown tables" in read["content"]
