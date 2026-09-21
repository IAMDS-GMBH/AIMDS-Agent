"""AIS-323: automatic support cases for update / installer fallbacks."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from hermes_cli import incident_report as ir


@pytest.fixture
def home(tmp_path, monkeypatch):
    hh = tmp_path / "home"
    (hh / "logs").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(hh))
    monkeypatch.setattr(ir, "_hermes_home", lambda: hh)
    monkeypatch.setattr(ir, "_support_config", lambda: {"auto_report": True})
    # pytest sets PYTEST_CURRENT_TEST → the gate would refuse; force it on explicitly.
    monkeypatch.setenv("HERMES_SUPPORT_AUTO_REPORT", "1")
    return hh


def _uploader(calls, *, result=None, exc=None):
    def _upload(args: SimpleNamespace):
        calls.append(args)
        if exc is not None:
            raise exc
        return result if result is not None else {"reference_id": "SUP-20260911-120000", "support_case_id": "SUP-20260911-120000"}

    return _upload


def test_gate_defaults_off_under_pytest_and_honours_env_and_config(monkeypatch, home):
    monkeypatch.delenv("HERMES_SUPPORT_AUTO_REPORT", raising=False)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    assert ir.auto_report_enabled() is False
    monkeypatch.setenv("HERMES_SUPPORT_AUTO_REPORT", "0")
    assert ir.auto_report_enabled() is False
    monkeypatch.setenv("HERMES_SUPPORT_AUTO_REPORT", "1")
    assert ir.auto_report_enabled() is True
    monkeypatch.delenv("HERMES_SUPPORT_AUTO_REPORT")
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    monkeypatch.setattr(ir, "_support_config", lambda: {"auto_report": False})
    assert ir.auto_report_enabled() is False
    monkeypatch.setattr(ir, "_support_config", lambda: {"auto_report": "yes"})
    assert ir.auto_report_enabled() is True
    monkeypatch.setattr(ir, "_support_config", lambda: {})
    assert ir.auto_report_enabled() is True


def test_report_uploads_once_per_kind_per_day(monkeypatch, home, caplog, capsys):
    calls: list[SimpleNamespace] = []
    monkeypatch.setattr(ir, "_upload", _uploader(calls))
    caplog.set_level("INFO", logger="hermes.incident")

    case = ir.report_incident("update-fallback-git", "release repository unavailable", "channel=stable")
    assert case == "SUP-20260911-120000"
    assert len(calls) == 1
    args = calls[0]
    assert args.reason == "update-fallback-git"
    assert args.category == ir.CATEGORY_INSTALLATION_UPDATE
    assert args.context_type == "update_failure" and args.install_type == "update"
    assert args.client_type == "hermes-cli" and args.summary == "release repository unavailable"
    assert "Reported to support as SUP-20260911-120000" in capsys.readouterr().err

    state = json.loads(ir.state_path().read_text(encoding="utf-8"))
    assert state["update-fallback-git"]["case_id"] == "SUP-20260911-120000"

    # Same kind again → logged, not uploaded.
    assert ir.report_incident("update-fallback-git", "again") is None
    assert len(calls) == 1
    assert any("already reported" in r.getMessage() for r in caplog.records)

    # A different kind is independent; an expired entry reports again.
    assert ir.report_incident("update-no-release-tag", "no tag") == "SUP-20260911-120000"
    assert len(calls) == 2
    state = json.loads(ir.state_path().read_text(encoding="utf-8"))
    state["update-fallback-git"]["reported_at"] = time.time() - ir.DEFAULT_WINDOW_SECONDS - 1
    ir.state_path().write_text(json.dumps(state), encoding="utf-8")
    assert ir.report_incident("update-fallback-git", "third") == "SUP-20260911-120000"
    assert len(calls) == 3


def test_report_is_logged_but_not_uploaded_when_disabled(monkeypatch, home, caplog):
    calls: list = []
    monkeypatch.setattr(ir, "_upload", _uploader(calls))
    monkeypatch.setenv("HERMES_SUPPORT_AUTO_REPORT", "0")
    caplog.set_level("INFO", logger="hermes.incident")
    assert ir.report_incident("installer-failure", "repository stage failed", severity="high") is None
    assert calls == []
    messages = [r.getMessage() for r in caplog.records]
    assert any("[incident] installer-failure: repository stage failed" in m for m in messages)
    assert any("auto_report is off" in m for m in messages)
    assert not ir.state_path().exists()


def test_upload_errors_never_propagate(monkeypatch, home, caplog):
    monkeypatch.setattr(ir, "_upload", _uploader([], exc=OSError("network down")))
    caplog.set_level("WARNING", logger="hermes.incident")
    assert ir.report_incident("update-fallback-git", "x") is None
    assert any("could not be reported" in r.getMessage() for r in caplog.records)
    # Not remembered — the next occurrence may try again.
    assert not ir.recently_reported("update-fallback-git")


def test_corrupt_state_file_is_ignored(monkeypatch, home):
    ir.state_path().write_text("{not json", encoding="utf-8")
    assert ir.recently_reported("anything") is False
    monkeypatch.setattr(ir, "_upload", _uploader([]))
    assert ir.report_incident("k", "s") == "SUP-20260911-120000"
    assert json.loads(ir.state_path().read_text(encoding="utf-8"))["k"]["case_id"]


def test_category_is_derived_from_context_type(monkeypatch, home):
    """AIS-384: category used to default unconditionally to
    installation_update -- a boot failure reported via context_type=
    "boot_error" without an explicit category must be filed as boot_error,
    not silently mis-categorized as an update issue."""
    calls: list[SimpleNamespace] = []
    monkeypatch.setattr(ir, "_upload", _uploader(calls))

    ir.report_incident("boot-failure-x", "backend exited", context_type="boot_error")
    assert calls[-1].category == "boot_error"

    ir.report_incident("install-failure-x", "install failed", context_type="install_failure")
    assert calls[-1].category == "install_failure"

    # Explicit category always wins over the context_type-derived default.
    ir.report_incident("weird-x", "custom", context_type="boot_error", category="other")
    assert calls[-1].category == "other"

    # Unrecognised context_type falls back to the old default.
    ir.report_incident("fallback-x", "unknown", context_type="something-new")
    assert calls[-1].category == ir.CATEGORY_INSTALLATION_UPDATE


def test_rate_limited_repeats_are_counted_for_the_next_report(monkeypatch, home, caplog):
    """AIS-344: a boot failure that repeats within the 24 h window is not
    lost — the state counts it and the count is readable for the next case."""
    calls: list = []
    monkeypatch.setattr(ir, "_upload", _uploader(calls))
    caplog.set_level("INFO", logger="hermes.incident")
    assert ir.report_incident("boot-failure-port-in-use", "desktop boot failed") == "SUP-20260911-120000"
    assert ir.occurrences_since_report("boot-failure-port-in-use") == 0
    assert ir.report_incident("boot-failure-port-in-use", "again") is None
    assert ir.report_incident("boot-failure-port-in-use", "and again") is None
    assert ir.occurrences_since_report("boot-failure-port-in-use") == 2
    assert len(calls) == 1
    assert any("(2 since)" in r.getMessage() for r in caplog.records)
    state = json.loads(ir.state_path().read_text(encoding="utf-8"))
    assert state["boot-failure-port-in-use"]["occurrences_since_report"] == 2
    # A fresh report resets the counter.
    state["boot-failure-port-in-use"]["reported_at"] = time.time() - ir.DEFAULT_WINDOW_SECONDS - 1
    ir.state_path().write_text(json.dumps(state), encoding="utf-8")
    assert ir.report_incident("boot-failure-port-in-use", "next day") == "SUP-20260911-120000"
    assert ir.occurrences_since_report("boot-failure-port-in-use") == 0
    assert ir.occurrences_since_report("never-seen") == 0
