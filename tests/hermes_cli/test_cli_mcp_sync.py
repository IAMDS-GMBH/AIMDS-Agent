"""hermes_cli.cli_mcp_sync — AIMDSSuiteMCP into other local CLIs (AIS-404)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_cli import cli_mcp_sync as sync

ENTRY = {
    "type": "http",
    "url": "https://suite.iamds.com/litellm/mcp/",
    "headers": {"Authorization": "Bearer sk-current-key"},
}


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A throwaway home, so no test can touch the real ~/.claude.json."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


@pytest.fixture
def suite(monkeypatch):
    monkeypatch.setattr(sync, "suite_mcp_entry", lambda: dict(ENTRY))


def _target(target_id: str) -> sync.CliTarget:
    return next(t for t in sync.CLI_TARGETS if t.id == target_id)


def _installed(monkeypatch, *ids: str) -> None:
    monkeypatch.setattr(sync, "find_cli", lambda t: f"/usr/bin/{t.binary}" if t.id in ids else None)


def _write(path: Path, data: dict, indent=2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=indent) + "\n", encoding="utf-8")


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestStatus:
    def test_reports_a_cli_that_is_not_installed(self, home, suite, monkeypatch):
        _installed(monkeypatch)
        st = sync.status_for(_target("gemini"))
        assert st.installed is False
        assert st.state == sync.STATE_NOT_INSTALLED

    def test_installed_without_a_config_is_not_configured(self, home, suite, monkeypatch):
        _installed(monkeypatch, "copilot")
        st = sync.status_for(_target("copilot"))
        assert st.installed is True and st.config_exists is False
        assert st.state == sync.STATE_NOT_CONFIGURED

    def test_matching_entry_is_in_sync(self, home, suite, monkeypatch):
        _installed(monkeypatch, "claude")
        _write(_target("claude").config_path, {"mcpServers": {sync.SUITE_SERVER_NAME: dict(ENTRY)}})
        st = sync.status_for(_target("claude"))
        assert st.state == sync.STATE_IN_SYNC
        assert st.url_matches is True and st.key_matches is True

    def test_trailing_slash_alone_is_not_drift(self, home, suite, monkeypatch):
        _installed(monkeypatch, "claude")
        entry = dict(ENTRY, url=ENTRY["url"].rstrip("/"))
        _write(_target("claude").config_path, {"mcpServers": {sync.SUITE_SERVER_NAME: entry}})
        assert sync.status_for(_target("claude")).state == sync.STATE_IN_SYNC

    def test_detects_a_different_key(self, home, suite, monkeypatch):
        _installed(monkeypatch, "claude")
        stale = dict(ENTRY, headers={"Authorization": "Bearer sk-old-key"})
        _write(_target("claude").config_path, {"mcpServers": {sync.SUITE_SERVER_NAME: stale}})
        st = sync.status_for(_target("claude"))
        assert st.state == sync.STATE_KEY_DRIFT
        assert st.url_matches is True and st.key_matches is False

    def test_detects_a_different_url(self, home, suite, monkeypatch):
        _installed(monkeypatch, "claude")
        stale = dict(ENTRY, url="https://staging.suite.iamds.com/litellm/mcp/")
        _write(_target("claude").config_path, {"mcpServers": {sync.SUITE_SERVER_NAME: stale}})
        assert sync.status_for(_target("claude")).state == sync.STATE_URL_DRIFT

    def test_unreadable_config_is_unknown_not_unconfigured(self, home, suite, monkeypatch):
        """Reporting "not configured" here would invite a write that destroys
        whatever the file really holds."""
        _installed(monkeypatch, "claude")
        path = _target("claude").config_path
        path.write_text("{ this is not json", encoding="utf-8")
        st = sync.status_for(_target("claude"))
        assert st.state == sync.STATE_UNKNOWN
        assert "not valid JSON" in st.error

    def test_status_never_carries_the_key(self, home, suite, monkeypatch):
        _installed(monkeypatch, "claude")
        _write(_target("claude").config_path, {"mcpServers": {sync.SUITE_SERVER_NAME: dict(ENTRY)}})
        payload = json.dumps(sync.all_statuses())
        assert "sk-current-key" not in payload and "Authorization" not in payload


class TestSuiteEntry:
    def test_rejects_an_uninterpolated_placeholder(self, monkeypatch):
        """Handing "${IAMDS_LITELLM_API_KEY}" to a foreign CLI would look
        configured and fail on every call."""
        monkeypatch.setattr(
            "tools.mcp_tool._load_mcp_config",
            lambda: {
                sync.SUITE_SERVER_NAME: {
                    "url": "https://suite.iamds.com/litellm/mcp/",
                    "headers": {"Authorization": "Bearer ${IAMDS_LITELLM_API_KEY}"},
                }
            },
        )
        assert sync.suite_mcp_entry() is None

    def test_builds_the_http_entry_from_the_hermes_config(self, monkeypatch):
        monkeypatch.setattr(
            "tools.mcp_tool._load_mcp_config",
            lambda: {
                sync.SUITE_SERVER_NAME: {
                    "url": "https://suite.iamds.com/litellm/mcp/",
                    "headers": {"authorization": "Bearer sk-real"},
                    "timeout": 180,
                }
            },
        )
        entry = sync.suite_mcp_entry()
        assert entry == {
            "type": "http",
            "url": "https://suite.iamds.com/litellm/mcp/",
            "headers": {"Authorization": "Bearer sk-real"},
        }


class TestApply:
    def test_writes_into_a_missing_config(self, home, suite, monkeypatch):
        _installed(monkeypatch, "copilot")
        report = sync.apply_to_target("copilot")
        assert report.ok and report.outcome == "written" and report.created_config
        data = _read(_target("copilot").config_path)
        assert data["mcpServers"][sync.SUITE_SERVER_NAME] == ENTRY

    def test_leaves_every_other_key_untouched(self, home, suite, monkeypatch):
        """~/.claude.json holds a lot of unrelated state — this is the promise
        that matters most."""
        _installed(monkeypatch, "claude")
        path = _target("claude").config_path
        original = {
            "autoUpdates": True,
            "cachedChangelog": "x" * 50,
            "mcpServers": {
                "playwright": {"type": "stdio", "command": "npx", "args": ["@playwright/mcp"]}
            },
            "numStartups": 42,
        }
        _write(path, original)

        assert sync.apply_to_target("claude").ok

        data = _read(path)
        assert data["autoUpdates"] is True
        assert data["cachedChangelog"] == "x" * 50
        assert data["numStartups"] == 42
        assert data["mcpServers"]["playwright"] == original["mcpServers"]["playwright"]
        assert data["mcpServers"][sync.SUITE_SERVER_NAME] == ENTRY

    def test_backs_the_file_up_before_touching_it(self, home, suite, monkeypatch):
        _installed(monkeypatch, "claude")
        path = _target("claude").config_path
        _write(path, {"numStartups": 7})

        report = sync.apply_to_target("claude")

        backup = Path(report.backup_path)
        assert backup.is_file()
        assert _read(backup) == {"numStartups": 7}

    def test_a_different_key_needs_confirmation(self, home, suite, monkeypatch):
        _installed(monkeypatch, "claude")
        path = _target("claude").config_path
        stale = dict(ENTRY, headers={"Authorization": "Bearer sk-someone-elses"})
        _write(path, {"mcpServers": {sync.SUITE_SERVER_NAME: stale}})

        report = sync.apply_to_target("claude")

        assert report.outcome == "needs_confirmation" and not report.ok
        # Untouched until confirmed.
        assert _read(path)["mcpServers"][sync.SUITE_SERVER_NAME] == stale

    def test_confirmed_key_replacement_writes(self, home, suite, monkeypatch):
        _installed(monkeypatch, "claude")
        path = _target("claude").config_path
        stale = dict(ENTRY, headers={"Authorization": "Bearer sk-someone-elses"})
        _write(path, {"mcpServers": {sync.SUITE_SERVER_NAME: stale}})

        report = sync.apply_to_target("claude", replace_key=True)

        assert report.ok and report.replaced_key
        assert _read(path)["mcpServers"][sync.SUITE_SERVER_NAME] == ENTRY

    def test_a_drifted_url_is_corrected_without_confirmation(self, home, suite, monkeypatch):
        """A wrong host is not a credential someone chose — it is a stale entry."""
        _installed(monkeypatch, "claude")
        path = _target("claude").config_path
        stale = dict(ENTRY, url="https://staging.suite.iamds.com/litellm/mcp/")
        _write(path, {"mcpServers": {sync.SUITE_SERVER_NAME: stale}})

        report = sync.apply_to_target("claude")

        assert report.ok and report.outcome == "written"
        assert _read(path)["mcpServers"][sync.SUITE_SERVER_NAME]["url"] == ENTRY["url"]

    def test_an_already_matching_entry_is_left_alone(self, home, suite, monkeypatch):
        _installed(monkeypatch, "claude")
        path = _target("claude").config_path
        _write(path, {"mcpServers": {sync.SUITE_SERVER_NAME: dict(ENTRY)}})
        mtime = path.stat().st_mtime_ns

        report = sync.apply_to_target("claude")

        assert report.ok and report.outcome == "unchanged"
        assert path.stat().st_mtime_ns == mtime

    def test_refuses_when_the_cli_is_not_installed(self, home, suite, monkeypatch):
        _installed(monkeypatch)
        report = sync.apply_to_target("gemini")
        assert not report.ok and "not installed" in report.error

    def test_refuses_to_edit_a_config_it_cannot_parse(self, home, suite, monkeypatch):
        _installed(monkeypatch, "claude")
        path = _target("claude").config_path
        path.write_text("{ broken", encoding="utf-8")

        report = sync.apply_to_target("claude")

        assert not report.ok and "not valid JSON" in report.error
        assert path.read_text(encoding="utf-8") == "{ broken"

    def test_refuses_without_a_suite_key(self, home, monkeypatch):
        _installed(monkeypatch, "claude")
        monkeypatch.setattr(sync, "suite_mcp_entry", lambda: None)
        report = sync.apply_to_target("claude")
        assert not report.ok and "no usable AIMDS-Suite" in report.error

    def test_unknown_target(self, home, suite):
        assert "unknown CLI" in sync.apply_to_target("emacs").error

    @pytest.mark.parametrize("indent", [2, 4, "\t"])
    def test_keeps_the_files_own_indentation(self, home, suite, monkeypatch, indent):
        """Reflowing an 80 KB config into another style would bury the one real
        change in a diff nobody can review."""
        _installed(monkeypatch, "claude")
        path = _target("claude").config_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"numStartups": 1}, indent=indent) + "\n", encoding="utf-8")

        assert sync.apply_to_target("claude").ok

        second_line = path.read_text(encoding="utf-8").splitlines()[1]
        expected = "\t" if indent == "\t" else " " * indent
        assert second_line.startswith(expected) and not second_line.startswith(expected + " ")
