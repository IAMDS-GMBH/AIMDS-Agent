"""tools/mcp_tool._inject_suite_ntfy_env (AIS-232): the catalog ntfy MCP gets
server, token and private topic from the active AIMDS-Suite provider."""

from hermes_cli.iamds_suite import SuiteNtfy
from tools import mcp_tool

SUITE = SuiteNtfy(provider_id="aimds-suite-dev", server_url="https://dev.suite.iamds.com/ntfy", token="sk-dev-key-1234", user_id="u-42", topic="private-u-42")


def test_injects_for_ntfy_mcp_only(monkeypatch):
    monkeypatch.setattr("hermes_cli.iamds_suite.resolve_suite_ntfy", lambda **kw: SUITE)
    env = mcp_tool._inject_suite_ntfy_env("NtfyMCP", None)
    assert env == {"NTFY_SERVER_URL": "https://dev.suite.iamds.com/ntfy", "NTFY_AUTH_TOKEN": "sk-dev-key-1234", "NTFY_DEFAULT_TOPIC": "private-u-42"}
    assert mcp_tool._inject_suite_ntfy_env("MSOffice365MCP", {"M365_TENANT_ID": "x"}) == {"M365_TENANT_ID": "x"}


def test_explicit_server_url_and_topic_win(monkeypatch):
    monkeypatch.setattr("hermes_cli.iamds_suite.resolve_suite_ntfy", lambda **kw: SUITE)
    explicit = {"NTFY_SERVER_URL": "https://ntfy.sh", "NTFY_DEFAULT_TOPIC": "mine"}
    assert mcp_tool._inject_suite_ntfy_env("NtfyMCP", explicit) == explicit
    partial = mcp_tool._inject_suite_ntfy_env("ntfy", {"NTFY_DEFAULT_TOPIC": "mine", "NTFY_AUTH_TOKEN": "tok"})
    assert partial == {"NTFY_DEFAULT_TOPIC": "mine", "NTFY_AUTH_TOKEN": "tok", "NTFY_SERVER_URL": "https://dev.suite.iamds.com/ntfy"}


def test_untouched_without_suite(monkeypatch):
    monkeypatch.setattr("hermes_cli.iamds_suite.resolve_suite_ntfy", lambda **kw: None)
    assert mcp_tool._inject_suite_ntfy_env("NtfyMCP", None) is None
    monkeypatch.setattr("hermes_cli.iamds_suite.resolve_suite_ntfy", lambda **kw: (_ for _ in ()).throw(RuntimeError("x")))
    assert mcp_tool._inject_suite_ntfy_env("NtfyMCP", {"A": "1"}) == {"A": "1"}
