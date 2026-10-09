"""``hermes update`` while offline (AIS-463): plain message, exit 75, no incident."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import hermes_cli.connectivity as connectivity
import hermes_cli.main as hermes_main


@pytest.fixture(autouse=True)
def _fresh_probe(monkeypatch):
    connectivity.reset_cache()
    # The developer's own system proxy settings must not decide the tests.
    monkeypatch.setattr("urllib.request.getproxies", lambda: {})
    yield
    connectivity.reset_cache()


@pytest.mark.real_connectivity
class TestIsOffline:
    def test_offline_when_no_github_host_answers(self, monkeypatch):
        calls = []

        def refuse(address, timeout=None):
            calls.append(address)
            raise OSError("network unreachable")

        monkeypatch.setattr(connectivity.socket, "create_connection", refuse)
        assert connectivity.is_offline(timeout=0.1, use_cache=False) is True
        assert len(calls) == 3

    def test_online_as_soon_as_one_host_answers(self, monkeypatch):
        class _Conn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(connectivity.socket, "create_connection", lambda address, timeout=None: _Conn())
        assert connectivity.is_offline(timeout=0.1, use_cache=False) is False

    def test_never_offline_behind_a_proxy(self, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
        monkeypatch.setattr(
            connectivity.socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(OSError("blocked"))
        )
        assert connectivity.is_offline(timeout=0.1, use_cache=False) is False

    def test_never_offline_behind_a_system_proxy(self, monkeypatch):
        """AIS-527: a proxy from the macOS/Windows network settings, not the
        environment — the direct probe fails there although GitHub is reachable."""
        for var in connectivity._PROXY_VARS:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setattr("urllib.request.getproxies", lambda: {"https": "http://corp-proxy:8080"})
        monkeypatch.setattr(
            connectivity.socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(OSError("blocked"))
        )
        assert connectivity.is_offline(timeout=0.1, use_cache=False) is False

    def test_result_is_cached_for_one_update_run(self, monkeypatch):
        calls = []

        def refuse(address, timeout=None):
            calls.append(address)
            raise OSError("down")

        monkeypatch.setattr(connectivity.socket, "create_connection", refuse)
        assert connectivity.is_offline(timeout=0.1) is True
        assert connectivity.is_offline(timeout=0.1) is True
        assert len(calls) == 3


def test_update_while_offline_exits_75_without_changes_or_incident(monkeypatch, capsys):
    monkeypatch.setattr(connectivity, "is_offline", lambda *a, **k: True)
    args = SimpleNamespace(branch="stable", check=False, yes=True, no_backup=True)
    with patch("hermes_cli.main._resolve_update_source") as resolve, \
         patch("hermes_cli.main._run_pre_update_backup") as backup, \
         patch("hermes_cli.incident_report.report_incident") as report:
        with pytest.raises(SystemExit) as exc:
            hermes_main.cmd_update(args)
    assert exc.value.code == hermes_main.UPDATE_EXIT_OFFLINE == 75
    resolve.assert_not_called()
    backup.assert_not_called()
    report.assert_not_called()
    assert "No internet connection" in capsys.readouterr().out


def test_no_incident_is_filed_while_offline(monkeypatch):
    monkeypatch.setattr(connectivity, "is_offline", lambda *a, **k: True)
    with patch("hermes_cli.incident_report.report_incident") as report:
        hermes_main._report_update_incident("update-origin-unreachable", "origin unreachable")
    report.assert_not_called()


def test_incidents_still_filed_when_online(monkeypatch):
    monkeypatch.setattr(connectivity, "is_offline", lambda *a, **k: False)
    with patch("hermes_cli.incident_report.report_incident") as report:
        hermes_main._report_update_incident("update-origin-unreachable", "origin unreachable")
    report.assert_called_once()
