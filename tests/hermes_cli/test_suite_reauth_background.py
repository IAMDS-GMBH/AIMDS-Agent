"""AIS-756: background work leaves a Suite key alone while it waits for a re-login.

A revoked prod key answered model discovery, metadata lookups and MCP
(re)connects with a 401 KeyNotFound, burst after burst every night. The first
background 401 now flags the environment; every background path checks the
flag (with the key it failed for) and only the hourly health probe may clear it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import types
from unittest.mock import MagicMock, patch

import pytest

from hermes_cli import iamds_suite as suite

PROD_KEY = "sk-revoked-prod-key-0000051wA"
NEW_KEY = "sk-fresh-prod-key-1111122xB"
CFG = {"model": {"provider": "aimds-suite-prod"}, "providers": {"aimds-suite-prod": {"base_url": "https://suite.iamds.com/litellm/v1"}}}


@pytest.fixture
def prod(monkeypatch):
    for var in ("IAMDS_LITELLM_API_KEY", "IAMDS_LITELLM_BASE_URL", "OPENAI_BASE_URL", "OPENAI_API_KEY",
                "IAMDS_LITELLM_STAGING_API_KEY", "IAMDS_LITELLM_DEV_API_KEY", "IAMDS_LITELLM_LOCALDEV_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("IAMDS_LITELLM_API_KEY", PROD_KEY)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: CFG)
    suite.clear_suite_auth_failure()
    yield
    suite.clear_suite_auth_failure()


def _flag_401(source: str = "llm") -> None:
    suite.mark_suite_auth_failure("aimds-suite-prod", 401, "Authentication Error - KeyNotFoundError", source=source)


# --------------------------------------------------------------------------- the gate


class TestGate:
    def test_blocked_with_the_failed_key_and_free_with_a_new_one(self, prod, monkeypatch):
        assert suite.suite_background_blocked("aimds-suite-prod") is False
        _flag_401()
        assert suite.suite_background_blocked("aimds-suite-prod") is True
        assert suite.suite_background_blocked("iamds-litellm") is True  # legacy slug
        assert suite.suite_background_blocked("openrouter") is False
        monkeypatch.setenv("IAMDS_LITELLM_API_KEY", NEW_KEY)
        assert suite.suite_background_blocked("aimds-suite-prod") is False

    def test_a_new_key_that_fails_again_is_a_new_lost_login(self, prod, monkeypatch):
        _flag_401()
        monkeypatch.setenv("IAMDS_LITELLM_API_KEY", NEW_KEY)
        assert suite.mark_suite_auth_failure("aimds-suite-prod", 401, "again", source="mcp") is True
        assert suite.suite_background_blocked("aimds-suite-prod") is True

    def test_flags_of_older_clients_without_key_fp_still_block(self, prod):
        _flag_401()
        flags = json.loads(suite._flag_path().read_text())
        flags["aimds-suite-prod"].pop("key_fp")
        suite._flag_path().write_text(json.dumps(flags))
        assert suite.suite_background_blocked("aimds-suite-prod") is True

    def test_the_flag_never_holds_the_key(self, prod):
        _flag_401()
        assert PROD_KEY not in suite._flag_path().read_text()

    def test_background_401_marks_once(self, prod):
        assert suite.note_background_suite_401("https://suite.iamds.com/litellm/v1", "x", source="test") == "aimds-suite-prod"
        since = suite.suite_auth_failures()["aimds-suite-prod"]["since"]
        assert suite.mark_suite_auth_failure("aimds-suite-prod", 401, "y", source="test") is False
        assert suite.suite_auth_failures()["aimds-suite-prod"]["since"] == since
        assert suite.note_background_suite_401("https://example.com/v1", "x", source="test") is None

    def test_a_model_denial_is_no_lost_login(self, prod):
        """AIS-747 stays: a 403 for a model the key may not use blocks nothing."""
        msg = "key not allowed to access model. This key can only access models=['gpt-5-mini']"
        assert suite.mark_suite_auth_failure("aimds-suite-prod", 403, msg, source="llm") is False
        assert suite.suite_background_blocked("aimds-suite-prod") is False


# --------------------------------------------------------------------------- the hourly probe


class TestHourlyProbe:
    def test_one_probe_per_hour_that_may_clear_the_flag(self, prod, monkeypatch):
        _flag_401()
        t0 = suite.suite_auth_failures()["aimds-suite-prod"]["since"]
        calls: list = []
        reauth: list = []
        monkeypatch.setattr(suite, "apply_reauth", lambda provider, **kw: reauth.append(provider) or suite.clear_suite_auth_failure(provider))

        def probe(url, key):
            calls.append(key)
            return (401, "unauthorized")

        for minutes in (1, 30, 59):
            result = suite.check_suite_environment_health("aimds-suite-prod", probe_fn=probe, now=t0 + minutes * 60)
            assert result["outcome"] == "waiting_reauth"
        assert calls == []

        assert suite.check_suite_environment_health("aimds-suite-prod", probe_fn=probe, now=t0 + 3600)["outcome"] == "still_broken"
        assert len(calls) == 1
        assert suite.check_suite_environment_health("aimds-suite-prod", probe_fn=probe, now=t0 + 5400)["outcome"] == "waiting_reauth"
        assert len(calls) == 1

        # The key was re-enabled at the Suite: the next hourly probe clears it.
        result = suite.check_suite_environment_health("aimds-suite-prod", probe_fn=lambda u, k: (200, ""), now=t0 + 7200)
        assert result["outcome"] == "recovered"
        assert reauth == ["aimds-suite-prod"]
        assert suite.suite_background_blocked("aimds-suite-prod") is False

    def test_another_failure_keeps_the_probe_time(self, prod):
        _flag_401()
        suite.note_suite_reauth_probe("aimds-suite-prod", now=12345.0)
        _flag_401(source="mcp")
        assert suite.suite_auth_failures()["aimds-suite-prod"]["last_probe_at"] == 12345.0

    def test_a_changed_key_is_probed_at_once(self, prod, monkeypatch):
        _flag_401()
        monkeypatch.setenv("IAMDS_LITELLM_API_KEY", NEW_KEY)
        monkeypatch.setattr(suite, "apply_reauth", lambda provider, **kw: suite.clear_suite_auth_failure(provider))
        result = suite.check_suite_environment_health("aimds-suite-prod", probe_fn=lambda u, k: (200, "") if k == NEW_KEY else (401, ""))
        assert result["outcome"] == "recovered"


# --------------------------------------------------------------------------- background paths


class TestModelDiscovery:
    def test_blocked_key_is_not_asked(self, prod):
        from hermes_cli import models

        _flag_401()
        with patch.object(models, "fetch_litellm_model_info_models") as info, patch.object(models, "fetch_api_models") as api:
            assert models.provider_model_ids("aimds-suite-prod") == []
        info.assert_not_called()
        api.assert_not_called()

    def test_a_401_marks_once_and_skips_the_fallback(self, prod):
        from hermes_cli import models

        def info_401(api_key, base, timeout=5.0, *, status_out=None):
            status_out.update(status=401, error="Unauthorized")
            return None

        with patch.object(models, "fetch_litellm_model_info_models", side_effect=info_401) as info, \
                patch.object(models, "fetch_api_models") as api:
            assert models.provider_model_ids("aimds-suite-prod") == []
            assert models.provider_model_ids("aimds-suite-prod") == []
        assert info.call_count == 1
        api.assert_not_called()
        flag = suite.suite_auth_failures()["aimds-suite-prod"]
        assert flag["source"] == "model_discovery" and flag["http_status"] == 401


@pytest.mark.real_fetchers
class TestModelMetadata:
    URL = "https://suite.iamds.com/litellm/v1"

    @pytest.fixture(autouse=True)
    def _fresh_cache(self):
        from agent import model_metadata as mm

        mm._endpoint_model_metadata_cache.pop(self.URL, None)
        mm._endpoint_model_metadata_cache_time.pop(self.URL, None)
        yield
        mm._endpoint_model_metadata_cache.pop(self.URL, None)
        mm._endpoint_model_metadata_cache_time.pop(self.URL, None)

    def _fetch(self):
        from agent import model_metadata as mm

        return mm.fetch_endpoint_model_metadata(self.URL, api_key=PROD_KEY, force_refresh=True)

    def test_blocked_key_is_not_asked(self, prod):
        _flag_401()
        with patch("agent.model_metadata.requests.get") as get:
            assert self._fetch() == {}
        get.assert_not_called()

    def test_a_401_marks_and_stops_after_the_first_request(self, prod):
        response = MagicMock(status_code=401, ok=False)
        with patch("agent.model_metadata.requests.get", return_value=response) as get:
            assert self._fetch() == {}
        assert get.call_count == 1
        assert suite.suite_auth_failures()["aimds-suite-prod"]["source"] == "model_metadata"


class TestNtfyKeyInfo:
    def test_no_key_info_lookup_while_blocked(self, prod):
        _flag_401()
        with patch.object(suite, "fetch_suite_key_info") as info:
            result = suite.resolve_suite_ntfy(config=CFG, use_cache=False)
        info.assert_not_called()
        assert result.user_source == "waiting_reauth"


class TestMcp:
    SUITE = {"url": "https://suite.iamds.com/litellm/mcp/"}

    def test_discovery_skips_the_waiting_suite_server_without_a_request(self, prod, monkeypatch):
        from tools import mcp_tool

        _flag_401()
        monkeypatch.setattr(mcp_tool, "_MCP_AVAILABLE", True)
        monkeypatch.setattr(mcp_tool, "_ensure_mcp_loop", lambda: pytest.fail("no connect while blocked"))
        assert mcp_tool.register_mcp_servers({"AIMDSSuiteMCP": dict(self.SUITE)}) == mcp_tool._existing_tool_names()

    def test_other_servers_still_connect(self, prod):
        from tools import mcp_tool

        _flag_401()
        kept = mcp_tool._drop_reauth_blocked({"AIMDSSuiteMCP": dict(self.SUITE), "files": {"command": "x"}})
        assert list(kept) == ["files"]

    def test_tool_calls_do_not_reconnect_while_blocked(self, prod, monkeypatch):
        from tools import mcp_tool

        _flag_401()
        monkeypatch.setattr(mcp_tool, "reconnect_mcp_server", lambda name: pytest.fail("no reconnect while blocked"))
        mcp_tool._servers.pop("AIMDSSuiteMCP", None)
        handler = mcp_tool._make_tool_handler("AIMDSSuiteMCP", "memory_session", 10.0)

        answer = json.loads(handler({}))
        assert answer["needs_reauth"] is True
        with mcp_tool.mcp_background_call():
            answer = json.loads(handler({}))
        # The session sync reads "is not connected" as "backend unavailable" and stops its run.
        assert "is not connected" in answer["error"]
        assert mcp_tool._server_error_counts.get("AIMDSSuiteMCP", 0) == 0

    def test_background_calls_skip_a_live_session(self, prod):
        from tools import mcp_tool

        _flag_401()
        server = MagicMock()
        server.session = MagicMock()
        server.session.call_tool = MagicMock(side_effect=AssertionError("no call while blocked"))
        mcp_tool._servers["AIMDSSuiteMCP"] = server
        try:
            with mcp_tool.mcp_background_call():
                answer = json.loads(mcp_tool._make_tool_handler("AIMDSSuiteMCP", "memory_session", 10.0)({}))
            assert "is not connected" in answer["error"]
            assert mcp_tool.refresh_mcp_server_tools("AIMDSSuiteMCP") is False
        finally:
            mcp_tool._servers.pop("AIMDSSuiteMCP", None)

    def test_session_sync_stops_its_run(self, prod):
        from agent import session_sync

        class Remote:
            calls = 0

            def call(self, args, **kw):
                Remote.calls += 1
                raise session_sync.BackendUnavailable("memory_session: MCP server 'AIMDSSuiteMCP' is not connected")

        db = MagicMock()
        db.sync_tombstones_due.return_value = []
        db.sync_due_sessions.return_value = [{"id": "s1", "sync_attempts": 0}, {"id": "s2", "sync_attempts": 0}]
        db.sync_remote_id.side_effect = lambda sid: sid
        db.sync_older_segment_pending.return_value = False
        with patch.object(session_sync, "sync_session", side_effect=lambda db, remote, row: remote.call({})):
            stats = session_sync.run_once(db, Remote())
        assert Remote.calls == 1 and stats.get("stopped") == 1
        db.sync_fail.assert_not_called()

    @pytest.mark.asyncio
    async def test_keepalive_does_not_ping_while_blocked(self, prod, monkeypatch):
        from tools import mcp_tool

        _flag_401()
        task = mcp_tool.MCPServerTask("AIMDSSuiteMCP")
        task._config = dict(self.SUITE)
        task.session = MagicMock()
        task.session.list_tools = MagicMock(side_effect=AssertionError("no keepalive while blocked"))
        real_wait = asyncio.wait
        rounds = {"n": 0}

        async def fake_wait(aws, timeout=None, return_when=None):
            rounds["n"] += 1
            if rounds["n"] == 1:
                return set(), set(aws)  # the keepalive interval elapsed
            task._shutdown_event.set()
            return await real_wait(aws, return_when=return_when)

        proxy = types.SimpleNamespace(**{k: getattr(asyncio, k) for k in dir(asyncio) if not k.startswith("__")})
        proxy.wait = fake_wait
        monkeypatch.setattr(mcp_tool, "asyncio", proxy)
        assert await task._wait_for_lifecycle_event() == "shutdown"
        task.session.list_tools.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_revoked_running_session_is_not_reconnected(self, prod, monkeypatch):
        import httpx

        from tools import mcp_tool

        response = MagicMock(status_code=401)
        attempts = {"n": 0}

        async def run_http(self, config):
            attempts["n"] += 1
            raise httpx.HTTPStatusError("401 Unauthorized", request=MagicMock(), response=response)

        monkeypatch.setattr(mcp_tool.MCPServerTask, "_run_http", run_http)
        monkeypatch.setattr(mcp_tool.MCPServerTask, "_is_http", lambda self: True)
        monkeypatch.setattr(mcp_tool.asyncio, "sleep", MagicMock(side_effect=AssertionError("no reconnect backoff")))
        task = mcp_tool.MCPServerTask("AIMDSSuiteMCP")
        task._ready.set()  # the session had been up
        await task.run(dict(self.SUITE))
        assert attempts["n"] == 1
        assert suite.suite_auth_failures()["aimds-suite-prod"]["source"] == "mcp"


class TestCronAndCurator:
    def test_cron_waits_and_resumes_with_a_new_key(self, prod, monkeypatch):
        import cron.scheduler as sched

        monkeypatch.setattr(sched, "load_config", lambda: CFG)
        _flag_401()
        assert sched.suite_reauth_blocking({"id": "j"}) == "aimds-suite-prod"
        monkeypatch.setenv("IAMDS_LITELLM_API_KEY", NEW_KEY)
        assert sched.suite_reauth_blocking({"id": "j"}) is None

    def test_curator_waits_without_counting_a_run(self, prod, monkeypatch):
        from agent import curator

        _flag_401()
        monkeypatch.setattr(curator, "should_run_now", lambda: pytest.fail("not even due-checked while blocked"))
        assert curator.maybe_run_curator(idle_for_seconds=float("inf")) is None


# --------------------------------------------------------------------------- telemetry


class TestTelemetry:
    def test_payload_carries_the_hash_prefix_never_the_key(self, prod, monkeypatch, caplog):
        from hermes_cli import support_logs

        monkeypatch.setenv("IAMDS_LITELLM_STAGING_API_KEY", NEW_KEY)
        sent: list = []

        class Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"{}"

        def fake_urlopen(req, timeout=None):
            sent.append(req.data.decode("utf-8"))
            return Resp()

        monkeypatch.setattr(support_logs.urllib.request, "urlopen", fake_urlopen)
        with caplog.at_level(logging.DEBUG):
            res = support_logs.send_client_telemetry(types.SimpleNamespace(url="https://support.example.test/api/v1/upload", api_key="", version="1.0"))
        assert res["ok"] is True
        body = json.loads(sent[0])
        assert body["suite_key_hash_prefix"] == {
            "prod": hashlib.sha256(PROD_KEY.encode()).hexdigest()[:12],
            "staging": hashlib.sha256(NEW_KEY.encode()).hexdigest()[:12],
        }
        for secret in (PROD_KEY, NEW_KEY):
            assert secret not in sent[0]
            assert secret not in caplog.text

    def test_prefix_is_litellms_token_hash(self, prod):
        """LiteLLM 1.101: ``hash_token(token) = sha256(token.encode()).hexdigest()``."""
        try:
            from litellm.proxy._types import hash_token
        except Exception:
            def hash_token(token):
                return hashlib.sha256(token.encode()).hexdigest()
        assert suite.suite_key_hash_prefixes()["prod"] == hash_token(PROD_KEY)[:12]

    def test_no_key_no_prefix(self, prod, monkeypatch):
        monkeypatch.delenv("IAMDS_LITELLM_API_KEY")
        assert suite.suite_key_hash_prefixes() == {}


def test_logs_of_a_blocked_night_never_hold_the_key(prod, caplog):
    with caplog.at_level(logging.DEBUG):
        _flag_401()
        suite.note_background_suite_401("aimds-suite-prod", "model discovery: Unauthorized", source="model_discovery")
        suite.check_suite_environment_health("aimds-suite-prod", probe_fn=lambda u, k: (401, ""), now=time.time() + 7200)
    assert PROD_KEY not in caplog.text
