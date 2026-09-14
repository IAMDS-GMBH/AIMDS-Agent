"""AIS-332 / SUP-20260914-063903: the desktop cron ticker waits before its
first tick and skips ticks while the provider host does not resolve."""

import socket
import threading

import pytest

from hermes_cli import web_server


def test_first_tick_delay_env(monkeypatch):
    monkeypatch.delenv("HERMES_DESKTOP_CRON_FIRST_TICK_DELAY", raising=False)
    assert web_server._desktop_cron_first_tick_delay() == 90.0
    monkeypatch.setenv("HERMES_DESKTOP_CRON_FIRST_TICK_DELAY", "0")
    assert web_server._desktop_cron_first_tick_delay() == 0.0
    monkeypatch.setenv("HERMES_DESKTOP_CRON_FIRST_TICK_DELAY", "abc")
    assert web_server._desktop_cron_first_tick_delay() == 90.0


def test_unknown_or_loopback_host_never_blocks(monkeypatch):
    monkeypatch.setattr(web_server, "_active_provider_host", lambda: "")
    assert web_server._provider_host_resolvable() is True
    monkeypatch.setattr(web_server, "_active_provider_host", lambda: "localhost")
    assert web_server._provider_host_resolvable() is True


def test_unresolvable_host_is_reported(monkeypatch):
    monkeypatch.setattr(web_server, "_active_provider_host", lambda: "suite.example")

    def _fail(*_a, **_k):
        raise socket.gaierror(8, "nodename nor servname provided, or not known")

    monkeypatch.setattr(web_server.socket, "getaddrinfo", _fail)
    assert web_server._provider_host_resolvable() is False


def test_resolvable_host(monkeypatch):
    monkeypatch.setattr(web_server, "_active_provider_host", lambda: "suite.example")
    monkeypatch.setattr(web_server.socket, "getaddrinfo", lambda *a, **k: [("x",)])
    assert web_server._provider_host_resolvable() is True


def test_slow_resolver_does_not_block(monkeypatch):
    monkeypatch.setattr(web_server, "_active_provider_host", lambda: "suite.example")
    gate = threading.Event()

    def _slow(*_a, **_k):
        gate.wait(5)
        return []

    monkeypatch.setattr(web_server.socket, "getaddrinfo", _slow)
    try:
        assert web_server._provider_host_resolvable(timeout=0.1) is True
    finally:
        gate.set()


def test_ticker_skips_while_unresolvable(monkeypatch):
    monkeypatch.setenv("HERMES_DESKTOP_CRON_FIRST_TICK_DELAY", "0")
    ticks = []
    import cron.scheduler as sched

    monkeypatch.setattr(sched, "tick", lambda **kw: ticks.append(kw))
    resolvable = iter([False, True])
    monkeypatch.setattr(web_server, "_provider_host_resolvable", lambda: next(resolvable, True))
    stop = threading.Event()
    calls = {"n": 0}

    def _wait(_interval):
        calls["n"] += 1
        if calls["n"] >= 2:
            stop.set()
        return stop.is_set()

    monkeypatch.setattr(stop, "wait", _wait)
    web_server._start_desktop_cron_ticker(stop, interval=0)
    assert len(ticks) == 1
