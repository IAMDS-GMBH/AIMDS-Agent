"""AIS-445: a gateway started from a test as a real subprocess must not run.

Such gateways detached from the test run, kept a cron ticker going against
the test's temporary HERMES_HOME and restarted themselves on every checkout
change, so developer machines collected one per test run.
"""

import os
import subprocess
import sys

import pytest

from hermes_cli import gateway


def test_the_pytest_process_itself_is_not_refused(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/x.py::test_y (call)")
    assert "pytest" in sys.modules
    assert gateway._spawned_by_a_test() is False


def test_a_child_that_inherited_the_test_env_is_refused(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/x.py::test_y (call)")
    monkeypatch.delitem(sys.modules, "pytest")
    assert gateway._spawned_by_a_test() is True


def test_without_the_test_env_nothing_changes(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delitem(sys.modules, "pytest")
    assert gateway._spawned_by_a_test() is False


@pytest.mark.live_system_guard_bypass
def test_real_gateway_run_subprocess_exits_immediately(tmp_path):
    env = dict(os.environ)
    env["HERMES_HOME"] = str(tmp_path / "home")
    env["PYTEST_CURRENT_TEST"] = "tests/x.py::test_y (call)"
    result = subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", "gateway", "run", "--replace"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1
    assert "Refusing to run a gateway spawned by a test" in result.stderr
