"""AIS-479: one OpenProject tool set per domain, Suite preferred, auto-link."""

import json
import time

import pytest

from agent import openproject_suite as ops

LOCAL_CFG = {
    "OpenProjectMCP": {
        "command": "/x/.venv/bin/python",
        "args": ["/x/optional-mcps/OpenProjectMCP/server.py"],
        "env": {"OPENPROJECT_BASE_URL": "https://OP.example.com/api/v3/", "OPENPROJECT_API_TOKEN": "local-tok"},
    },
    "OpenProjectMCP-other": {
        "command": "/x/.venv/bin/python",
        "args": ["/x/optional-mcps/OpenProjectMCP/server.py"],
        "env": {"OPENPROJECT_BASE_URL": "https://other.example.com", "OPENPROJECT_API_TOKEN": "t2"},
    },
    "Unrelated": {"command": "npx", "args": ["something"]},
    "AIMDSSuiteMCP": {"url": "https://suite.example.com/litellm/mcp/"},
}

LINK_URL = "https://suite.example.com/connect/openproject/#one-time"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import tools.mcp_tool as mcp_tool

    monkeypatch.setattr(mcp_tool, "_load_mcp_config", lambda: LOCAL_CFG)
    monkeypatch.setattr(ops, "suite_root", lambda: "https://suite.example.com")
    monkeypatch.setattr(ops, "_find_suite_tool", lambda suffix: f"mcp_AIMDSSuiteMCP_mcp_openproject-{suffix}")
    ops._invalidate_cache()
    calls = {"tool": [], "post": []}

    def set_suite(payload):
        def call(tool, args):
            calls["tool"].append(tool)
            return json.dumps({"result": json.dumps(payload)})

        monkeypatch.setattr(ops, "_call_suite_tool", call)

    def set_post(responses):
        def post(url, body):
            calls["post"].append((url, dict(body)))
            return responses[url.rsplit("/", 1)[-1]]

        monkeypatch.setattr(ops, "_post_json", post)

    return calls, set_suite, set_post


def test_instance_key_normalizes():
    assert ops.instance_key("https://OP.example.com:443/api/v3/") == "https://op.example.com"
    assert ops.instance_key("op.example.com") == "https://op.example.com"
    assert ops.instance_key("http://localhost:8080/") == "http://localhost:8080"
    assert ops.instance_key("") == ""


def test_pm_tool_detection():
    assert ops.is_pm_tool("pm_list_projects")
    assert ops.is_pm_tool("mcp_openproject-pm_list_projects")
    assert not ops.is_pm_tool("memory_search")
    assert not ops.is_pm_tool("npm_install")


def test_local_servers_only_configured_openproject():
    servers = ops.local_servers(
        {
            **LOCAL_CFG,
            "OpenProjectMCP-unset": {
                "command": "py",
                "args": ["optional-mcps/OpenProjectMCP/server.py"],
                "env": {"OPENPROJECT_BASE_URL": "${OPENPROJECT_BASE_URL}"},
            },
            "OpenProjectMCP-off": {**LOCAL_CFG["OpenProjectMCP"], "enabled": False},
        }
    )
    assert [(s["name"], s["instance"]) for s in servers] == [
        ("OpenProjectMCP", "https://op.example.com"),
        ("OpenProjectMCP-other", "https://other.example.com"),
    ]


def test_linked_same_domain_hides_only_that_local_server(env):
    calls, set_suite, _ = env
    set_suite({"linked": True, "instance": "https://op.example.com", "op_login": "jh"})

    state = ops.run_once()

    assert state["hide_local"] == ["OpenProjectMCP"]
    assert state["hide_suite"] is False
    assert ops.tool_hidden("OpenProjectMCP", "pm_list_projects")
    assert not ops.tool_hidden("OpenProjectMCP-other", "pm_list_projects")
    assert not ops.tool_hidden("AIMDSSuiteMCP", "mcp_openproject-pm_list_projects")
    # Non-pm tools of the same server are never touched.
    assert not ops.tool_hidden("OpenProjectMCP", "something_else")
    assert ops.status()["suite_login"] == "jh"


def test_linked_other_domain_keeps_both(env):
    _, set_suite, _ = env
    set_suite({"linked": True, "instance": "https://elsewhere.example.com"})

    state = ops.run_once()

    assert state["hide_local"] == []
    assert state["hide_suite"] is False


def test_unlinked_same_domain_auto_links_then_hides_local(env):
    calls, set_suite, set_post = env
    set_suite({"linked": False, "link_url": LINK_URL})
    set_post(
        {
            "info": (200, {"valid": True, "instance_url": "https://op.example.com", "needs_base_url": False}),
            "submit": (200, {"ok": True, "op_login": "jh"}),
        }
    )

    state = ops.run_once()

    assert [url for url, _ in calls["post"]] == [
        "https://suite.example.com/connect/openproject/api/info",
        "https://suite.example.com/connect/openproject/api/submit",
    ]
    assert calls["post"][0][1] == {"token": "one-time"}
    assert calls["post"][1][1] == {"token": "one-time", "api_token": "local-tok"}
    assert state["suite"]["linked"] is True
    assert "link_token" not in state["suite"]
    assert state["hide_local"] == ["OpenProjectMCP"]
    assert state["hide_suite"] is False


def test_unlinked_and_rejected_token_keeps_local_hides_suite_and_stops_retrying(env):
    calls, set_suite, set_post = env
    set_suite({"linked": False, "link_url": LINK_URL})
    set_post(
        {
            "info": (200, {"valid": True, "instance_url": "https://op.example.com"}),
            "submit": (422, {"error": "token_invalid"}),
        }
    )

    state = ops.run_once()

    assert state["hide_local"] == []
    assert state["hide_suite"] is True
    assert ops.tool_hidden("AIMDSSuiteMCP", "mcp_openproject-pm_get_work_package")
    assert state["auto_link"]["rejected"] is True

    calls["post"].clear()
    ops.run_once()
    assert [url.rsplit("/", 1)[-1] for url, _ in calls["post"]] == ["info"]


def test_network_failure_backs_off(env, monkeypatch):
    _, set_suite, _ = env
    set_suite({"linked": False, "link_url": LINK_URL})

    def post(url, body):
        if url.endswith("info"):
            return 200, {"valid": True, "instance_url": "https://op.example.com"}
        raise OSError("offline")

    monkeypatch.setattr(ops, "_post_json", post)

    state = ops.run_once()

    link = state["auto_link"]
    assert link["last_error"] == "network"
    assert link["next_at"] > time.time() + ops.LINK_BACKOFF_BASE_SECONDS - 5
    assert "rejected" not in link


def test_needs_base_url_sends_local_instance(env):
    calls, set_suite, set_post = env
    set_suite({"linked": False, "link_url": LINK_URL})
    set_post(
        {
            "info": (200, {"valid": True, "instance_url": "", "needs_base_url": True}),
            "submit": (200, {"ok": True, "op_login": "jh"}),
        }
    )

    ops.run_once()

    assert calls["post"][1][1]["base_url"] == "https://OP.example.com/api/v3/"


def test_link_on_foreign_host_is_never_redeemed(env, monkeypatch):
    calls, set_suite, set_post = env
    set_suite({"linked": False, "link_url": "https://evil.example.net/connect/openproject/#tok"})
    set_post({})

    state = ops.run_once()

    assert calls["post"] == []
    assert state["hide_local"] == [] and state["hide_suite"] is False


def test_plain_http_link_only_on_localhost(env, monkeypatch):
    monkeypatch.setattr(ops, "suite_root", lambda: "http://suite.example.com")
    assert ops._link_page_base("http://suite.example.com/connect/openproject/#t", "http://suite.example.com") is None
    assert (
        ops._link_page_base("http://localhost:8443/connect/openproject/#t", "http://localhost:8443")
        == "http://localhost:8443/connect/openproject/"
    )


def test_no_suite_tool_shows_everything(env, monkeypatch):
    calls, _, _ = env
    monkeypatch.setattr(ops, "_find_suite_tool", lambda suffix: None)

    state = ops.run_once()

    assert state["suite"] == {"available": False}
    assert not ops.tool_hidden("OpenProjectMCP", "pm_list_projects")
    assert calls["tool"] == []


def test_stale_decision_is_ignored(env):
    _, set_suite, _ = env
    set_suite({"linked": True, "instance": "https://op.example.com"})
    state = ops.run_once(now=time.time() - ops.STATE_TTL_SECONDS - 10)
    ops.save_state(state)

    assert not ops.tool_hidden("OpenProjectMCP", "pm_list_projects")


def test_maybe_run_respects_schedule_and_switch(env, monkeypatch):
    calls, set_suite, _ = env
    set_suite({"linked": True, "instance": "https://op.example.com"})

    assert ops.maybe_run(config={}) is not None
    assert ops.maybe_run(config={}) is None  # next check not due yet
    assert len(calls["tool"]) == 1

    # Switched off: any earlier hiding is lifted.
    assert ops.maybe_run(config={"openproject": {"prefer_suite": False}}) is None
    assert not ops.tool_hidden("OpenProjectMCP", "pm_list_projects")


def test_no_local_server_never_calls_the_suite(env, monkeypatch):
    calls, set_suite, _ = env
    set_suite({"linked": False, "link_url": LINK_URL})
    monkeypatch.setattr(ops, "local_servers", lambda mcp_config=None: [])

    ops.run_once()

    # pm_link_status on an unlinked account creates a one-time link: skip it.
    assert calls["tool"] == []


def test_mcp_check_fn_consults_the_decision(monkeypatch):
    import tools.mcp_tool as mcp_tool

    class _Server:
        session = object()

    monkeypatch.setitem(mcp_tool._servers, "OpenProjectMCP", _Server())
    hidden = {"value": True}
    monkeypatch.setattr(ops, "tool_hidden", lambda server, tool: hidden["value"])

    assert mcp_tool._make_check_fn("OpenProjectMCP", "pm_list_projects")() is False
    hidden["value"] = False
    assert mcp_tool._make_check_fn("OpenProjectMCP", "pm_list_projects")() is True
    # Without a tool name (legacy callers) only the session counts.
    hidden["value"] = True
    assert mcp_tool._make_check_fn("OpenProjectMCP")() is True


def test_status_endpoint_reports_the_decision(env):
    from starlette.testclient import TestClient

    from hermes_cli import web_server

    _, set_suite, _ = env
    set_suite({"linked": True, "instance": "https://op.example.com", "op_login": "jh"})
    ops.run_once()

    prev = getattr(web_server.app.state, "auth_required", None)
    web_server.app.state.auth_required = False
    try:
        client = TestClient(web_server.app)
        client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
        body = client.get("/api/openproject/suite-status").json()
    finally:
        if prev is None:
            delattr(web_server.app.state, "auth_required")
        else:
            web_server.app.state.auth_required = prev

    assert body["suite_linked"] is True
    assert body["hide_local"] == ["OpenProjectMCP"]
    assert body["suite_login"] == "jh"
    assert {"name": "OpenProjectMCP", "instance": "https://op.example.com"} in body["locals"]


def test_tool_definition_cache_follows_the_decision(env):
    """AIS-479: get_tool_definitions memoises the check_fn-filtered result;
    its key must change when the OpenProject decision does."""
    import model_tools

    _, set_suite, _ = env
    assert model_tools._openproject_decision_fingerprint() is None

    set_suite({"linked": True, "instance": "https://op.example.com"})
    ops.run_once()
    ops._invalidate_cache()
    assert model_tools._openproject_decision_fingerprint() == (("OpenProjectMCP",), False)

    set_suite({"linked": True, "instance": "https://elsewhere.example.com"})
    ops.run_once()
    ops._invalidate_cache()
    assert model_tools._openproject_decision_fingerprint() is None
