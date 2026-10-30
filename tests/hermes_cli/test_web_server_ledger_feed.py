"""AIS-797: GET /api/sessions/{id}/ledger and GET /api/workspace/feed."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

OP = "mcp_AIMDSSuiteMCP_mcp_openproject_"


class TestLedgerAndFeedEndpoints:
    @pytest.fixture(autouse=True)
    def _setup_test_client(self, monkeypatch, _isolate_hermes_home):
        try:
            from starlette.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi/starlette not installed")

        import hermes_state
        from hermes_constants import get_hermes_home
        from hermes_cli.web_server import _SESSION_HEADER_NAME, _SESSION_TOKEN, app

        monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", get_hermes_home() / "state.db")
        self.client = TestClient(app)
        self.client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN

    def _seed_compressed_conversation(self):
        from agent.session_ledger import record_tool_call
        from hermes_state import SessionDB

        db = SessionDB()
        try:
            db.create_session(session_id="ledger-root", source="tui")
            agent = SimpleNamespace(session_id="ledger-root", _session_db=db, _current_turn_id="t1")
            record_tool_call(agent, OP + "pm_get_work_package", {"id": "DEMO-1"},
                             json.dumps({"result": {"id": 9001, "key": "DEMO-1", "status": "Backlog"}}),
                             tool_call_id="c1")
            db.end_session("ledger-root", "compression")
            time.sleep(0.01)
            db.create_session(session_id="ledger-tip", source="tui", parent_session_id="ledger-root")
            agent.session_id = "ledger-tip"
            record_tool_call(agent, OP + "pm_update_work_package", {"id": "DEMO-1", "status": "Closed"},
                             json.dumps({"result": {"id": 9001, "key": "DEMO-1", "status": "Closed"}}),
                             tool_call_id="c2")
        finally:
            db.close()

    def test_ledger_of_any_segment_is_the_lineage_ledger(self):
        self._seed_compressed_conversation()
        for sid in ("ledger-root", "ledger-tip"):
            resp = self.client.get(f"/api/sessions/{sid}/ledger")
            assert resp.status_code == 200
            page = resp.json()
            assert page["v"] == 1 and page["session_id"] == sid and page["lineage_root"] == "ledger-root"
            assert page["total"] == 2
            assert [(e["verb"], e.get("status_from"), e.get("status_to")) for e in page["entries"]] == [
                ("read", None, None), ("update", "Backlog", "Closed")]

    def test_ledger_after_returns_the_delta(self):
        self._seed_compressed_conversation()
        first = self.client.get("/api/sessions/ledger-tip/ledger").json()["entries"][0]["seq"]
        page = self.client.get(f"/api/sessions/ledger-tip/ledger?after={first}").json()
        assert [e["verb"] for e in page["entries"]] == ["update"] and page["total"] == 2

    def test_ledger_of_unknown_session_is_404(self):
        assert self.client.get("/api/sessions/nope/ledger").status_code == 404

    def test_empty_ledger(self):
        from hermes_state import SessionDB

        db = SessionDB()
        try:
            db.create_session(session_id="quiet", source="tui")
        finally:
            db.close()
        page = self.client.get("/api/sessions/quiet/ledger").json()
        assert page == {"v": 1, "session_id": "quiet", "lineage_root": "quiet", "entries": [], "total": 0}

    def test_feed_endpoint_serves_the_cached_feed(self, monkeypatch):
        from cron import workspace_feed as wf

        built = []

        def fake_build(**kw):
            built.append(kw)
            return {"v": 1, "generated_at": "2026-10-30T09:00:00+01:00",
                    "window": {"kind": "workspace", "today": "2026-10-30", "preview_day": "2026-11-02",
                               "start": "2026-10-30T00:00:00+01:00", "end": "2026-11-02T23:59:59+01:00"},
                    "items": [], "sources": []}

        wf.clear_cache()
        monkeypatch.setattr(wf, "build_feed", fake_build)
        try:
            first = self.client.get("/api/workspace/feed")
            second = self.client.get("/api/workspace/feed")
            assert first.status_code == second.status_code == 200
            assert first.json()["cached"] is False and second.json()["cached"] is True
            assert self.client.get("/api/workspace/feed?refresh=true").json()["cached"] is False
            assert len(built) == 2
        finally:
            wf.clear_cache()

    def test_feed_without_sources_reads_nothing(self, monkeypatch):
        """No MCP server configured, no vault: every source is skipped and no tool runs."""
        from cron import workspace_feed as wf
        import tools.registry as registry_mod

        def no_vault(*_a, **_k):
            raise OSError("no vault in this test")

        calls = []
        monkeypatch.setattr(registry_mod.registry, "dispatch", lambda *a, **k: calls.append(a) or "{}")
        monkeypatch.setattr("tools.mcp_tool.get_mcp_status", lambda: [])
        monkeypatch.setattr("agent.runtime_cwd.resolve_agent_cwd", no_vault)
        wf.clear_cache()
        try:
            feed = self.client.get("/api/workspace/feed").json()
        finally:
            wf.clear_cache()
        assert calls == [] and feed["items"] == []
        assert {s["status"] for s in feed["sources"]} == {"skipped"}
