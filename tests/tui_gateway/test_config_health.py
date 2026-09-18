"""Tests for _probe_config_health -- catching bare/null YAML keys that
silently drop nested settings before they cause a hard-to-diagnose failure
(e.g. a null mcpServers.<name>.timeout used to cause an unbounded hang in
tools.mcp_tool._run_on_mcp_loop)."""

from tui_gateway.server import _probe_config_health


def test_top_level_bare_key_still_flagged():
    warning = _probe_config_health({"agent": None, "display": {"personality": "default"}})
    assert "`agent`" in warning
    assert "empty section(s)" in warning


def test_null_mcp_server_timeout_flagged():
    warning = _probe_config_health(
        {"mcpServers": {"AIMDSSuiteMCP": {"command": "uvx", "timeout": None}}}
    )
    assert "AIMDSSuiteMCP" in warning
    assert "`timeout`" in warning


def test_null_mcp_server_connect_timeout_flagged():
    warning = _probe_config_health(
        {"mcpServers": {"OpenProjectMCP": {"command": "uvx", "connect_timeout": None}}}
    )
    assert "OpenProjectMCP" in warning
    assert "`connect_timeout`" in warning


def test_configured_numeric_timeout_not_flagged():
    warning = _probe_config_health(
        {"mcpServers": {"AIMDSSuiteMCP": {"command": "uvx", "timeout": 180}}}
    )
    assert warning == ""


def test_non_dict_mcp_server_entry_ignored():
    # Malformed config shouldn't crash the probe.
    warning = _probe_config_health({"mcpServers": {"Broken": None}})
    assert warning == ""


def test_no_mcp_servers_key_unaffected():
    warning = _probe_config_health({"display": {}})
    assert warning == ""
