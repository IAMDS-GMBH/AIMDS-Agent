"""Which CLI commands may connect the MCP fleet at startup.

Inline discovery defaults to on, so a command is opted *out*. Before this was
gated, `hermes gateway restart` — a process-control command that never touches
the tool registry — connected every configured MCP server synchronously and
kept that whole fleet alive for its own lifetime. On a running desktop that
produced a second set of stdio servers (tempo, m365, atlassian) beside the
ones the long-running backend already owned.
"""

from __future__ import annotations

import argparse

import pytest

from hermes_cli.main import (
    _command_has_dedicated_mcp_startup,
    _command_needs_no_mcp,
    _should_background_mcp_startup,
)


def _args(command, **kw):
    return argparse.Namespace(command=command, tui=False, **kw)


class TestControlCommandsSkipDiscovery:
    @pytest.mark.parametrize("command", ["gateway", "update", "uninstall"])
    def test_control_commands_are_mcp_free(self, command):
        assert _command_needs_no_mcp(_args(command)) is True

    def test_gateway_run_keeps_its_dedicated_startup(self):
        """`gateway run` really does need tools — via its own executor path."""
        args = _args("gateway", gateway_command="run")

        assert _command_has_dedicated_mcp_startup(args) is True

    @pytest.mark.parametrize("command", [None, "chat", "rl"])
    def test_agent_commands_still_get_discovery(self, command):
        args = _args(command)

        assert _command_needs_no_mcp(args) is False
        assert _should_background_mcp_startup(args) is True

    @pytest.mark.parametrize("command", ["mcp", "tools", "sessions"])
    def test_tool_facing_commands_are_not_opted_out(self, command):
        """These inspect or exercise the registry, so they must keep it."""
        assert _command_needs_no_mcp(_args(command)) is False
