"""The desktop backend must populate the MCP tool registry.

`dashboard` is the process behind "Hermes lokale API (embedded)" — it serves
the desktop's agent turns. It was in neither `_AGENT_COMMANDS` nor the
background-discovery set, so `_prepare_agent_startup` returned immediately and
the process never ran MCP discovery.

The consequence was subtle because nothing errored: deferred tools are meant
to stay out of the prompt and be surfaced by `tool_search` on demand, but
`tool_search` can only offer what is registered. Sessions reported
`total_available: 8` (local tools only) and every MCP call came back "is not
available", while the desktop panel kept showing "6/6 (91 Tools)" because
`_mcp_server_summary` counts `tools.include` from config rather than loaded
tools.
"""

from __future__ import annotations

import argparse

import pytest

from hermes_cli.main import (
    _AGENT_COMMANDS,
    _should_background_mcp_startup,
)


def _args(command, **kw):
    fields = {"command": command, "tui": False}
    fields.update(kw)

    return argparse.Namespace(**fields)


class TestDashboardGetsMcpDiscovery:
    def test_dashboard_prepares_agent_startup(self):
        assert "dashboard" in _AGENT_COMMANDS

    def test_dashboard_discovers_in_the_background(self):
        """Inline discovery would stall the desktop boot on a cold npx server."""
        assert _should_background_mcp_startup(_args("dashboard")) is True

    @pytest.mark.parametrize("command", [None, "chat", "rl"])
    def test_existing_agent_commands_are_unchanged(self, command):
        assert command in _AGENT_COMMANDS
        assert _should_background_mcp_startup(_args(command)) is True

    def test_tui_launch_still_uses_its_own_path(self):
        assert _should_background_mcp_startup(_args("dashboard", tui=True)) is False

    @pytest.mark.parametrize("command", ["update", "uninstall", "sessions"])
    def test_unrelated_commands_do_not_gain_discovery(self, command):
        assert command not in _AGENT_COMMANDS
        assert _should_background_mcp_startup(_args(command)) is False
