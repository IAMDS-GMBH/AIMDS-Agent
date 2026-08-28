"""`hermes gateway restart` must hand the terminal back.

Without a service manager — the normal case when the desktop app owns the
gateway — restart fell through to a foreground `run_gateway()`. The command
printed "Press Ctrl+C to stop" and blocked, and the Ctrl+C that followed
killed the gateway: the user ended up with no gateway at all, which is the
opposite of a restart. Windows already avoided this; macOS and Linux did not.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture
def gw():
    from hermes_cli import gateway

    return gateway


class TestRestartDoesNotBlock:
    def test_detached_spawn_is_preferred(self, gw, capsys):
        with patch.object(gw, "_spawn_detached_gateway", return_value=True) as spawn, patch.object(
            gw, "run_gateway"
        ) as run_fg:
            assert gw._spawn_detached_gateway() is True
            spawn.assert_called_once()
            run_fg.assert_not_called()

    def test_foreground_is_only_the_fallback(self, gw):
        """If detaching fails the gateway must still come up, just noisily."""
        source = (gw.__file__ or "").replace(".pyc", ".py")
        text = open(source, encoding="utf-8").read()

        # Every remaining foreground start in the restart flow must sit behind
        # a failed detached attempt, never be reached unconditionally.
        assert "if _spawn_detached_gateway():" in text
        assert text.count("_spawn_detached_gateway()") >= 3

    def test_restart_paths_no_longer_call_run_gateway_unguarded(self, gw):
        source = (gw.__file__ or "").replace(".pyc", ".py")
        lines = open(source, encoding="utf-8").read().splitlines()

        for idx, line in enumerate(lines):
            if line.strip() != "run_gateway(verbose=0)":
                continue
            # Wide enough to span the explanatory comment between the
            # guard and the fallback call.
            window = "\n".join(lines[max(0, idx - 16) : idx])
            assert "_spawn_detached_gateway()" in window, (
                f"run_gateway at line {idx + 1} is reachable without trying a "
                "detached start first"
            )
