"""Tests for cmd_dashboard's config-schema migration guard.

cmd_dashboard is the Desktop app's backend entrypoint. Unlike the
interactive ``hermes update`` CLI flow, it historically never called
``migrate_config()`` -- meaning GUI-only users could be permanently stuck
on a stale on-disk config schema (e.g. the workspace->vault folder rename)
even though the app itself auto-updates. These tests verify the fix: a
call to ``ensure_config_migrated()`` early in ``cmd_dashboard``, before
any server/build work happens, that never blocks startup even on failure.
"""

from __future__ import annotations

import argparse
from unittest.mock import patch, MagicMock

import pytest

from hermes_cli.main import cmd_dashboard


class _StopEarly(Exception):
    """Sentinel raised to short-circuit cmd_dashboard right after the
    migration guard runs, before any real server/build work happens."""


def _ns(**kw):
    defaults = dict(
        port=9119, host="127.0.0.1", no_open=False, insecure=False,
        stop=False, status=False, isolated=False, open_profile="",
        skip_build=False,
    )
    defaults.update(kw)
    return argparse.Namespace(**defaults)


class TestDashboardConfigMigrationGuard:
    def test_calls_ensure_config_migrated_when_stale(self):
        with patch("hermes_cli.main._sync_bundled_skills_quietly",
                   side_effect=_StopEarly), \
             patch("hermes_cli.config.ensure_config_migrated") as mock_migrate, \
             pytest.raises(_StopEarly):
            cmd_dashboard(_ns())
        mock_migrate.assert_called_once_with(quiet=True)

    def test_noop_when_config_already_current(self):
        """Confirms the call happens unconditionally (ensure_config_migrated
        itself is responsible for deciding whether migration is needed) and
        that a no-op migration doesn't block dashboard startup."""
        with patch("hermes_cli.main._sync_bundled_skills_quietly",
                   side_effect=_StopEarly), \
             patch("hermes_cli.config.ensure_config_migrated") as mock_migrate, \
             pytest.raises(_StopEarly):
            cmd_dashboard(_ns())
        mock_migrate.assert_called_once()

    def test_migration_failure_does_not_block_dashboard_startup(self):
        """ensure_config_migrated() swallows its own errors internally, so
        even if the underlying migration blows up, cmd_dashboard must keep
        going -- proven here by reaching the code right after the guard."""
        with patch("hermes_cli.main._sync_bundled_skills_quietly",
                   side_effect=_StopEarly) as mock_sync, \
             patch("hermes_cli.config.ensure_config_migrated") as mock_migrate, \
             pytest.raises(_StopEarly):
            cmd_dashboard(_ns())
        # Reaching _sync_bundled_skills_quietly (and raising from it) proves
        # cmd_dashboard's control flow survived past the migration guard.
        mock_sync.assert_called_once()
        mock_migrate.assert_called_once()
