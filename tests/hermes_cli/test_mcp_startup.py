"""Regression tests for bounded/lazy CLI MCP startup."""

from __future__ import annotations

from argparse import Namespace
import sys
import threading
import time
import types

import pytest

import cli as cli_mod
from hermes_cli import main as main_mod
from hermes_cli import mcp_startup


@pytest.fixture(autouse=True)
def _reset_mcp_startup_state():
    saved_started = mcp_startup._mcp_discovery_started
    saved_thread = mcp_startup._mcp_discovery_thread
    try:
        mcp_startup._mcp_discovery_started = False
        mcp_startup._mcp_discovery_thread = None
        yield
    finally:
        thread = mcp_startup._mcp_discovery_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        mcp_startup._mcp_discovery_started = saved_started
        mcp_startup._mcp_discovery_thread = saved_thread


def _agent_args(**overrides) -> Namespace:
    base = {
        "accept_hooks": False,
        "command": "chat",
        "cron_command": None,
        "gateway_command": None,
        "mcp_action": None,
        "tui": False,
    }
    base.update(overrides)
    return Namespace(**base)


def test_prepare_agent_startup_backgrounds_blocking_mcp_for_chat(monkeypatch):
    stop = threading.Event()
    calls = {"mcp": 0}

    def _blocking_discover():
        calls["mcp"] += 1
        stop.wait()

    monkeypatch.setitem(
        sys.modules,
        "hermes_cli.plugins",
        types.SimpleNamespace(discover_plugins=lambda: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "hermes_cli.config",
        types.SimpleNamespace(
            read_raw_config=lambda: {"mcp_servers": {"demo": {"transport": "stdio"}}},
            load_config=lambda: {},
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "agent.shell_hooks",
        types.SimpleNamespace(register_from_config=lambda *_a, **_k: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "tools.mcp_tool",
        types.SimpleNamespace(discover_mcp_tools=_blocking_discover),
    )

    try:
        start = time.monotonic()
        main_mod._prepare_agent_startup(_agent_args())
        elapsed = time.monotonic() - start
        assert elapsed < 0.2
        assert calls["mcp"] == 1
        assert mcp_startup._mcp_discovery_thread is not None
        assert mcp_startup._mcp_discovery_thread.is_alive()
    finally:
        stop.set()


def test_prepare_agent_startup_skips_mcp_bootstrap_for_tui_chat(monkeypatch):
    calls = {"mcp": 0}

    monkeypatch.setitem(
        sys.modules,
        "hermes_cli.plugins",
        types.SimpleNamespace(discover_plugins=lambda: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "hermes_cli.config",
        types.SimpleNamespace(load_config=lambda: {}),
    )
    monkeypatch.setitem(
        sys.modules,
        "agent.shell_hooks",
        types.SimpleNamespace(register_from_config=lambda *_a, **_k: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "tools.mcp_tool",
        types.SimpleNamespace(
            discover_mcp_tools=lambda: calls.__setitem__("mcp", calls["mcp"] + 1)
        ),
    )

    main_mod._prepare_agent_startup(_agent_args(tui=True))

    assert calls["mcp"] == 0
    assert mcp_startup._mcp_discovery_thread is None


def test_cli_get_tool_definitions_briefly_waits_for_fast_mcp_thread(monkeypatch):
    thread = threading.Thread(target=lambda: time.sleep(0.05), daemon=True)
    thread.start()
    mcp_startup._mcp_discovery_thread = thread

    monkeypatch.setitem(
        sys.modules,
        "model_tools",
        types.SimpleNamespace(get_tool_definitions=lambda *_a, **_k: ["ok"]),
    )

    start = time.monotonic()
    result = cli_mod.get_tool_definitions(enabled_toolsets=["web"], quiet_mode=True)
    elapsed = time.monotonic() - start

    assert result == ["ok"]
    assert elapsed >= 0.04
    assert not thread.is_alive()


def test_init_agent_waits_for_mcp_discovery_before_agent_build(monkeypatch):
    waited = {"done": False}

    cli = cli_mod.HermesCLI(compact=True)
    cli._session_db = object()
    cli._resumed = False
    cli.conversation_history = []
    cli._install_tool_callbacks = lambda: None
    cli._ensure_tirith_security = lambda: None
    cli._ensure_runtime_credentials = lambda: True

    monkeypatch.setattr(
        mcp_startup,
        "wait_for_mcp_discovery",
        lambda timeout=0.75: waited.__setitem__("done", True),
    )

    def _fake_agent(*_a, **_k):
        assert waited["done"] is True
        return types.SimpleNamespace()

    monkeypatch.setattr(cli_mod, "AIAgent", _fake_agent)

    assert cli._init_agent() is True


class TestDiscoveryBudget:
    """Stdio servers used to be capped at the 0.75s default.

    Only *remote* entries extended the wait, so a cold `npx -y <pkg>` could not
    answer in time and its tools were absent for the whole session — while the
    desktop, which reads config rather than the live registry, still listed
    them. That is the "TempoMCP is configured but not there" report.
    """

    def test_package_fetching_launcher_gets_a_cold_start_budget(self):
        from hermes_cli.mcp_startup import _discovery_budget

        budget = _discovery_budget(
            {"TempoMCP": {"command": "npx", "args": ["-y", "@ivelin-web/tempo-mcp-server@1.8.0"]}}
        )

        assert budget >= 8.0

    def test_local_binary_gets_a_shorter_budget(self):
        from hermes_cli.mcp_startup import _discovery_budget

        budget = _discovery_budget({"M365": {"command": "/opt/app/.venv/bin/python", "args": ["server.py"]}})

        assert 0.75 < budget <= 3.0

    def test_explicit_connect_timeout_wins(self):
        from hermes_cli.mcp_startup import _discovery_budget

        assert _discovery_budget({"S": {"command": "npx", "connect_timeout": 12}}) == 12.0

    def test_budget_is_capped(self):
        from hermes_cli.mcp_startup import _DISCOVERY_BUDGET_CAP, _discovery_budget

        assert _discovery_budget({"S": {"url": "https://x/mcp", "connect_timeout": 900}}) == _DISCOVERY_BUDGET_CAP

    def test_disabled_servers_are_ignored(self, monkeypatch):
        from hermes_cli import mcp_startup

        monkeypatch.setattr(
            mcp_startup,
            "read_raw_config",
            lambda: None,
            raising=False,
        )
        monkeypatch.setattr(
            "hermes_cli.config.read_raw_config",
            lambda: {"mcp_servers": {"on": {"command": "npx"}, "off": {"command": "npx", "enabled": False}}},
        )

        assert set(mcp_startup._enabled_mcp_server_specs()) == {"on"}


class TestWaitReturnsEarly:
    """Warm starts must stay fast and dead servers must not cost the budget."""

    @staticmethod
    def _live_thread():
        import threading

        stop = threading.Event()
        thread = threading.Thread(target=stop.wait, daemon=True)
        thread.start()

        return thread, stop

    def test_returns_as_soon_as_every_server_is_registered(self, monkeypatch):
        import time

        from hermes_cli import mcp_startup

        thread, stop = self._live_thread()
        monkeypatch.setattr(mcp_startup, "_mcp_discovery_thread", thread)
        monkeypatch.setattr(
            "hermes_cli.config.read_raw_config",
            lambda: {"mcp_servers": {"a": {"command": "npx"}, "b": {"command": "npx"}}},
        )
        monkeypatch.setattr(mcp_startup, "_discovered_server_names", lambda: {"a", "b"})

        started = time.monotonic()
        try:
            mcp_startup.wait_for_mcp_discovery()
        finally:
            stop.set()

        # Budget would be 8s; everything is present, so this must be instant.
        assert time.monotonic() - started < 1.0

    def test_gives_up_once_servers_stop_arriving(self, monkeypatch):
        import time

        from hermes_cli import mcp_startup

        thread, stop = self._live_thread()
        monkeypatch.setattr(mcp_startup, "_mcp_discovery_thread", thread)
        monkeypatch.setattr(mcp_startup, "_DISCOVERY_STALL_GRACE", 0.2)
        monkeypatch.setattr(
            "hermes_cli.config.read_raw_config",
            lambda: {"mcp_servers": {"a": {"command": "npx"}, "dead": {"command": "npx"}}},
        )
        # "a" is up, "dead" never registers.
        monkeypatch.setattr(mcp_startup, "_discovered_server_names", lambda: {"a"})

        started = time.monotonic()
        try:
            mcp_startup.wait_for_mcp_discovery()
        finally:
            stop.set()

        elapsed = time.monotonic() - started
        assert elapsed < 3.0, f"a dead server must not cost the full budget (took {elapsed:.1f}s)"

    def test_env_override_still_wins(self, monkeypatch):
        import time

        from hermes_cli import mcp_startup

        thread, stop = self._live_thread()
        monkeypatch.setattr(mcp_startup, "_mcp_discovery_thread", thread)
        monkeypatch.setenv("HERMES_MCP_DISCOVERY_WAIT_SECONDS", "0")
        monkeypatch.setattr(
            "hermes_cli.config.read_raw_config",
            lambda: {"mcp_servers": {"a": {"command": "npx"}}},
        )
        monkeypatch.setattr(mcp_startup, "_discovered_server_names", lambda: set())

        started = time.monotonic()
        try:
            mcp_startup.wait_for_mcp_discovery()
        finally:
            stop.set()

        assert time.monotonic() - started < 0.5
