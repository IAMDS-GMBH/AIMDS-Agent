"""cli.py first-run profile-build onboarding (mirrors gateway/run.py).

The gateway offers the profile build on the very first message ever
(gateway/run.py first-message onboarding); the interactive CLI had no
equivalent. ``_first_run_onboarding_note`` is the CLI's hook: it returns
the directive exactly once per install and marks the flag seen.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml


@pytest.fixture()
def cli_mod(monkeypatch, tmp_path):
    import cli as mod

    monkeypatch.setattr(mod, "_hermes_home", tmp_path)
    return mod


def _db(count: int):
    return SimpleNamespace(session_count=lambda **kw: count)


class TestFirstRunOnboardingNote:
    def test_fresh_install_gets_the_directive_once(self, cli_mod, tmp_path):
        config: dict = {}
        note = cli_mod._first_run_onboarding_note(_db(1), has_history=False, config=config)
        assert note and "workdays(action='configure'" in note
        # flag persisted to config.yaml AND the in-memory config
        from agent.onboarding import PROFILE_BUILD_FLAG, is_seen

        assert is_seen(config, PROFILE_BUILD_FLAG)
        on_disk = yaml.safe_load((tmp_path / "config.yaml").read_text())
        assert is_seen(on_disk, PROFILE_BUILD_FLAG)
        # second message: never again
        assert cli_mod._first_run_onboarding_note(_db(1), has_history=False, config=config) is None

    def test_prior_sessions_suppress_the_offer(self, cli_mod):
        assert cli_mod._first_run_onboarding_note(_db(2), has_history=False, config={}) is None

    def test_history_or_missing_db_suppress_the_offer(self, cli_mod):
        assert cli_mod._first_run_onboarding_note(_db(1), has_history=True, config={}) is None
        assert cli_mod._first_run_onboarding_note(None, has_history=False, config={}) is None

    def test_mode_off_suppresses_the_offer(self, cli_mod):
        config = {"onboarding": {"profile_build": "off"}}
        assert cli_mod._first_run_onboarding_note(_db(1), has_history=False, config=config) is None
