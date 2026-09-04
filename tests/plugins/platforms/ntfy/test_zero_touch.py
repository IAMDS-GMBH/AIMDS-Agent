"""ntfy platform adapter — AIMDS-Suite zero-touch configuration (AIS-232)."""

from __future__ import annotations

import pytest

from gateway.config import PlatformConfig
from hermes_cli.iamds_suite import SuiteNtfy
from plugins.platforms.ntfy import adapter as ntfy

SUITE = SuiteNtfy(provider_id="aimds-suite-dev", server_url="https://dev.suite.iamds.com/ntfy", token="sk-dev-key-1234", user_id="u-42", topic="private-u-42", topics=["general/*"], user_source="key_info")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for var in ("NTFY_TOPIC", "NTFY_SERVER_URL", "NTFY_TOKEN", "NTFY_PUBLISH_TOPIC", "NTFY_AUTO_SUITE", "NTFY_HOME_CHANNEL"):
        monkeypatch.delenv(var, raising=False)
    ntfy._SUITE_AUTO_CACHE["at"] = 0.0
    ntfy._SUITE_AUTO_CACHE["value"] = None
    yield
    ntfy._SUITE_AUTO_CACHE["at"] = 0.0
    ntfy._SUITE_AUTO_CACHE["value"] = None


def _with_suite(monkeypatch, value=SUITE):
    monkeypatch.setattr("hermes_cli.iamds_suite.resolve_suite_ntfy", lambda **kw: value)


def test_not_configured_without_env_or_suite(monkeypatch):
    _with_suite(monkeypatch, None)
    assert ntfy.check_requirements() is False
    assert ntfy.is_connected(PlatformConfig(enabled=True)) is False
    assert ntfy._env_enablement() is None


def test_suite_zero_touch_configures_adapter_and_seed(monkeypatch):
    _with_suite(monkeypatch)
    assert ntfy.check_requirements() is True
    assert ntfy.is_connected(PlatformConfig(enabled=True)) is True
    assert ntfy.validate_config(PlatformConfig(enabled=True)) is True
    seed = ntfy._env_enablement()
    assert seed["topic"] == "private-u-42" and seed["server"] == "https://dev.suite.iamds.com/ntfy"
    assert seed["token"] == "sk-dev-key-1234" and seed["source"] == "aimds-suite" and seed["provider"] == "aimds-suite-dev"
    assert seed["home_channel"] == {"chat_id": "private-u-42", "name": "private-u-42"}
    a = ntfy.NtfyAdapter(PlatformConfig(enabled=True))
    assert a._server == "https://dev.suite.iamds.com/ntfy" and a._topic == "private-u-42"
    assert a._publish_topic == "private-u-42" and a._config_source == "aimds-suite"
    assert a._auth_headers() == {"Authorization": "Bearer sk-dev-key-1234"}


def test_explicit_topic_wins_over_suite(monkeypatch):
    _with_suite(monkeypatch)
    monkeypatch.setenv("NTFY_TOPIC", "hermes-in")
    a = ntfy.NtfyAdapter(PlatformConfig(enabled=True))
    assert a._topic == "hermes-in" and a._server == ntfy.DEFAULT_SERVER and a._token == "" and a._config_source == "explicit"
    assert ntfy._env_enablement()["topic"] == "hermes-in" and "source" not in ntfy._env_enablement()


def test_config_extra_wins_over_suite(monkeypatch):
    _with_suite(monkeypatch)
    a = ntfy.NtfyAdapter(PlatformConfig(enabled=True, extra={"topic": "team", "server": "https://ntfy.example", "token": "t0k"}))
    assert (a._server, a._topic, a._token) == ("https://ntfy.example", "team", "t0k")


def test_suite_without_user_id_gives_no_topic(monkeypatch):
    _with_suite(monkeypatch, SuiteNtfy(provider_id="aimds-suite-prod", server_url="https://suite.iamds.com/ntfy", token="sk-prod", user_source="no_user_id"))
    assert ntfy.check_requirements() is False
    assert ntfy._env_enablement() is None
    a = ntfy.NtfyAdapter(PlatformConfig(enabled=True))
    assert a._topic == "" and a._server == "https://suite.iamds.com/ntfy" and a._token == "sk-prod"


def test_auto_can_be_disabled(monkeypatch):
    _with_suite(monkeypatch)
    monkeypatch.setenv("NTFY_AUTO_SUITE", "0")
    assert ntfy.suite_auto_config() is None and ntfy.check_requirements() is False


def test_auto_config_is_cached(monkeypatch):
    calls = []

    def fake(**kw):
        calls.append(1)
        return SUITE
    monkeypatch.setattr("hermes_cli.iamds_suite.resolve_suite_ntfy", fake)
    ntfy.suite_auto_config(); ntfy.suite_auto_config(); ntfy.check_requirements()
    assert len(calls) == 1
    ntfy.suite_auto_config(force=True)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_standalone_send_uses_suite_server_and_token(monkeypatch):
    _with_suite(monkeypatch)
    posted = {}

    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"id": "m1"}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, content=None, headers=None):
            posted["url"], posted["headers"] = url, headers
            return _Resp()

    monkeypatch.setattr(ntfy.httpx, "AsyncClient", _Client)
    res = await ntfy._standalone_send(PlatformConfig(enabled=True), "", "hallo")
    assert res["success"] and res["chat_id"] == "private-u-42"
    assert posted["url"] == "https://dev.suite.iamds.com/ntfy/private-u-42"
    assert posted["headers"]["Authorization"] == "Bearer sk-dev-key-1234"
