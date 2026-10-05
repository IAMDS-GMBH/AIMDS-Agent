"""Self-tests for the AIS-487 network / browser guard in tests/conftest.py."""

from __future__ import annotations

import socket
import subprocess
import webbrowser

import pytest

from tests import conftest as guard


@pytest.fixture
def forget_own_attempts(request):
    """Drop the attempts this test provokes on purpose, so the guard's
    teardown check does not fail it."""
    yield
    nodeid = request.node.nodeid
    with guard._net_lock:
        guard._net_state["attempts"][:] = [
            a for a in guard._net_state["attempts"] if a[0] != nodeid
        ]


@pytest.mark.parametrize(
    "host", ["localhost", "127.0.0.1", "127.8.9.10", "::1", "0.0.0.0", "foo.localhost", None]
)
def test_loopback_hosts_are_allowed(host):
    assert guard._is_local_host(host)


@pytest.mark.parametrize("host", ["api.x.ai", "models.dev", "10.0.0.1", "8.8.8.8", "2001:db8::1"])
def test_remote_hosts_are_not_local(host):
    assert not guard._is_local_host(host)


def test_dns_lookup_of_remote_host_fails_like_offline(request, forget_own_attempts):
    if guard._net_state["mode"] != "raise":
        pytest.skip("guard not in raise mode")
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo("api.x.ai", 443)
    assert (request.node.nodeid, "getaddrinfo", "api.x.ai") in guard._net_state["attempts"]


def test_connect_to_remote_ip_is_refused(forget_own_attempts):
    if guard._net_state["mode"] != "raise":
        pytest.skip("guard not in raise mode")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError):
            sock.connect(("192.0.2.1", 443))
    finally:
        sock.close()


def test_loopback_server_round_trip():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        client = socket.create_connection(server.getsockname(), timeout=2)
        client.close()
    finally:
        server.close()


def test_browser_open_is_blocked(request, forget_own_attempts):
    assert webbrowser.open("https://accounts.x.ai/sign-in") is False
    assert webbrowser.get().open_new_tab("https://example.org") is False
    kinds = {a[1] for a in guard._net_state["attempts"] if a[0] == request.node.nodeid}
    assert kinds == {"browser"}


@pytest.mark.parametrize(
    "cmd",
    [["open", "https://x.ai"], ["xdg-open", "/tmp/a.pdf"], "cmd /c start https://x.ai"],
)
def test_url_opener_subprocess_is_blocked(cmd, forget_own_attempts):
    with pytest.raises(RuntimeError, match="open a browser/app"):
        subprocess.run(cmd)


def test_live_llm_falls_back_to_a_working_target(live_llm):
    assert live_llm.base_url.endswith("/v1")
    assert live_llm.model
