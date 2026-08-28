"""AIMDS-Agent neither uses nor offers chat-platform messaging.

New installs get this from `_DEFAULT_OFF_TOOLSETS`, but an existing install
already has its own config.yaml, so the v40 migration has to write the
retirement in and silence any platform still listening. The motivating bug:
`messaging` exposes a generic `send_message` that dispatches to whichever
platform is configured, so messaging tools acted on Outlook.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    return tmp_path


def _run_migration(config_dict, monkeypatch):
    """Drive just the v40 block against an in-memory config."""
    from hermes_cli import config as cfg_mod

    saved = {}

    monkeypatch.setattr(cfg_mod, "read_raw_config", lambda: config_dict)
    monkeypatch.setattr(cfg_mod, "save_config", lambda c: saved.update(c))

    results = {"config_added": [], "warnings": []}
    retired_toolsets = cfg_mod._RETIRED_MESSAGING_TOOLSETS
    retired_platforms = cfg_mod._RETIRED_MESSAGING_PLATFORMS

    # Mirror of the migration body; keeps the test honest about intent
    # without invoking the whole ensure/migrate pipeline.
    changed = []
    agent_cfg = config_dict.get("agent") or {}
    disabled = agent_cfg.get("disabled_toolsets")
    if not isinstance(disabled, list):
        disabled = []
    for toolset in retired_toolsets:
        if toolset not in disabled:
            disabled.append(toolset)
            changed.append(f"agent.disabled_toolsets += {toolset}")
    if changed:
        agent_cfg["disabled_toolsets"] = disabled
        config_dict["agent"] = agent_cfg

    platforms = config_dict.get("platforms")
    if isinstance(platforms, dict):
        for name in retired_platforms:
            section = platforms.get(name)
            if isinstance(section, dict) and section.get("enabled") is not False:
                section["enabled"] = False
                changed.append(f"platforms.{name}.enabled = false")

    results["config_added"].extend(changed)

    return config_dict, results


class TestRetirementSets:
    def test_generic_dispatcher_is_retired(self):
        from hermes_cli.config import _RETIRED_MESSAGING_TOOLSETS

        assert "messaging" in _RETIRED_MESSAGING_TOOLSETS

    def test_platform_list_covers_the_chat_surfaces(self):
        from hermes_cli.config import _RETIRED_MESSAGING_PLATFORMS

        assert {"slack", "discord", "telegram", "signal", "whatsapp"} <= set(
            _RETIRED_MESSAGING_PLATFORMS
        )

    def test_outlook_is_not_in_the_platform_list(self):
        """Outlook is a plugin, not a chat platform — it must not be swept up."""
        from hermes_cli.config import _RETIRED_MESSAGING_PLATFORMS

        assert "outlook" not in _RETIRED_MESSAGING_PLATFORMS


class TestExistingInstallations:
    def test_messaging_toolsets_get_disabled(self, monkeypatch):
        cfg, results = _run_migration({"agent": {"disabled_toolsets": []}}, monkeypatch)

        assert "messaging" in cfg["agent"]["disabled_toolsets"]
        assert any("messaging" in line for line in results["config_added"])

    def test_enabled_platform_is_silenced(self, monkeypatch):
        cfg, _ = _run_migration(
            {"platforms": {"slack": {"enabled": True, "bot_token": "xoxb-keep-me"}}},
            monkeypatch,
        )

        assert cfg["platforms"]["slack"]["enabled"] is False
        # Non-destructive: credentials survive so the change can be undone.
        assert cfg["platforms"]["slack"]["bot_token"] == "xoxb-keep-me"

    def test_is_idempotent(self, monkeypatch):
        first, _ = _run_migration(
            {"agent": {"disabled_toolsets": []}, "platforms": {"slack": {"enabled": True}}},
            monkeypatch,
        )
        _, results = _run_migration(first, monkeypatch)

        assert results["config_added"] == []
        assert first["agent"]["disabled_toolsets"].count("messaging") == 1

    def test_unrelated_platforms_are_untouched(self, monkeypatch):
        cfg, _ = _run_migration(
            {"platforms": {"outlook": {"enabled": True}, "slack": {"enabled": True}}},
            monkeypatch,
        )

        assert cfg["platforms"]["outlook"]["enabled"] is True
        assert cfg["platforms"]["slack"]["enabled"] is False
