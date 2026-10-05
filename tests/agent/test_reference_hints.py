"""AIS-485: ticket references are recognised deterministically."""

import pytest

from agent import reference_hints as rh

SUITE = "mcp_AIMDSSuiteMCP_mcp_openproject_"
LOCAL = "mcp_op_"


@pytest.mark.parametrize(
    "text, refs",
    [
        ("PRO-6 Ticket müssen wir uns als nächste ansehen", ["PRO-6"]),
        ("schau dir AIS-469 und EXT-95 an", ["AIS-469", "EXT-95"]),
        ("siehe https://openproject.iamds.com/projects/ais/work_packages/17806/activity", ["#17806"]),
        ("https://acme.atlassian.net/browse/OPS-12 bitte prüfen", ["OPS-12"]),
        ("GPT-5 vs UTF-8, ISO-27001 und CVE-2026-1234", []),
        ("Dateiname report-2026-10.pdf und v0.7.10-rc.2", []),
        ("", []),
    ],
)
def test_find_ticket_references(text, refs):
    assert rh.find_ticket_references(text) == refs


class _Agent:
    valid_tool_names = set()


@pytest.fixture
def reachable(monkeypatch):
    names = []
    import agent.deferred_tools as dt

    monkeypatch.setattr(dt, "guidance_tool_names", lambda agent: set(names))
    monkeypatch.setattr(dt, "tool_search_active", lambda agent: False)
    monkeypatch.setattr("tools.openproject_names.is_hidden", lambda name: False)
    return names


def test_hint_names_the_suite_first_and_the_local_fallback(reachable, monkeypatch):
    reachable += [f"{SUITE}pm_get_work_package", f"{SUITE}pm_search_work_packages", f"{LOCAL}pm_get_work_package",
                  f"{LOCAL}pm_search_work_packages", "mcp_AIMDSSuiteMCP_mcp_memory_memory_search"]
    monkeypatch.setattr("agent.openproject_suite.suite_accepts_display_ids", lambda: False)

    hint = rh.ticket_reference_hint(_Agent(), "PRO-6 Ticket müssen wir uns ansehen")

    assert hint.startswith("[Ticket reference] PRO-6: look it up directly")
    assert hint.index("AIMDS Suite") < hint.index("local server")
    assert f"`{SUITE}pm_get_work_package` (numeric id only; find a key via `{SUITE}pm_search_work_packages`)" in hint
    assert f"`{LOCAL}pm_get_work_package` (the key works as id)" in hint
    assert "Jira" not in hint


def test_suite_with_display_id_support_takes_the_key(reachable, monkeypatch):
    reachable += [f"{SUITE}pm_get_work_package"]
    monkeypatch.setattr("agent.openproject_suite.suite_accepts_display_ids", lambda: True)

    assert f"`{SUITE}pm_get_work_package` (the key works as id)" in rh.ticket_reference_hint(_Agent(), "AIS-469?")


def test_no_ticket_system_says_so_instead_of_searching(reachable):
    reachable += ["mcp_AIMDSSuiteMCP_mcp_memory_memory_search", "search_files"]

    hint = rh.ticket_reference_hint(_Agent(), "Was ist mit PRO-6?")

    assert "no ticket system" in hint and "PRO-6" in hint


def test_no_reference_no_hint(reachable):
    reachable += [f"{SUITE}pm_get_work_package"]
    assert rh.ticket_reference_hint(_Agent(), "Wie wird das Wetter morgen?") == ""


def test_jira_only(reachable):
    reachable += ["mcp_AtlassianMCP_jira_get_issue", "mcp_AtlassianMCP_jira_search"]
    hint = rh.ticket_reference_hint(_Agent(), "OPS-12")
    assert "Jira: `mcp_AtlassianMCP_jira_get_issue`" in hint


def test_bridge_loads_the_tools_into_the_session(reachable, monkeypatch):
    import agent.deferred_tools as dt

    reachable += [f"{SUITE}pm_get_work_package", f"{SUITE}pm_search_work_packages"]
    loaded = []
    monkeypatch.setattr(dt, "tool_search_active", lambda agent: True)
    monkeypatch.setattr(dt, "load_deferred_tool", lambda agent, name, **kw: loaded.append((name, kw["reason"])) or name)
    monkeypatch.setattr("agent.openproject_suite.suite_accepts_display_ids", lambda: True)

    rh.ticket_reference_hint(_Agent(), "PRO-6")

    assert loaded == [(f"{SUITE}pm_get_work_package", "ticket_reference"), (f"{SUITE}pm_search_work_packages", "ticket_reference")]
