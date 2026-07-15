from __future__ import annotations

from gateway import run as gateway_run


def test_gateway_mcp_discovery_startup_timeout_defaults(monkeypatch):
    monkeypatch.delenv("HERMES_GATEWAY_MCP_DISCOVERY_STARTUP_TIMEOUT_SECONDS", raising=False)
    assert (
        gateway_run._gateway_mcp_discovery_startup_timeout()
        == gateway_run._GATEWAY_MCP_DISCOVERY_STARTUP_TIMEOUT_SECS_DEFAULT
    )


def test_gateway_mcp_discovery_startup_timeout_honors_positive_env(monkeypatch):
    monkeypatch.setenv("HERMES_GATEWAY_MCP_DISCOVERY_STARTUP_TIMEOUT_SECONDS", "3.5")
    assert gateway_run._gateway_mcp_discovery_startup_timeout() == 3.5
