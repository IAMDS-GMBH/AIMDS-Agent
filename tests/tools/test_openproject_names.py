"""AIS-483: OpenProject pm_* tools under the Suite's and the bundled server's names."""

from __future__ import annotations

from tools.openproject_names import find_openproject_tool, openproject_kind, openproject_tools, pm_suffix, server_for

SUITE = "mcp_AIMDSSuiteMCP_mcp_openproject-pm_list_time_entries"
LOCAL = "mcp_op_pm_list_time_entries"


def test_name_forms():
    assert pm_suffix(SUITE) == pm_suffix(LOCAL) == pm_suffix("pm_list_time_entries") == "pm_list_time_entries"
    assert pm_suffix("mcp_TempoMCP_retrieveWorklogs") == ""
    assert openproject_kind(SUITE) == "suite"
    assert openproject_kind("mcp_openproject-pm_list_projects") == "suite"  # raw gateway name
    assert openproject_kind(LOCAL) == "local"
    assert openproject_kind("mcp_OpenProjectMCP_pm_get_work_package") == "local"
    assert openproject_kind("mcp_Other_pm_list_projects") == ""
    assert openproject_kind("mcp_AIMDSSuiteMCP_mcp_memory-memory_search") == ""


def test_registry_owner_decides_for_custom_prefixes(monkeypatch):
    import tools.mcp_tool as mt

    monkeypatch.setattr(mt, "get_mcp_server_for_tool", lambda name: "OpenProjectMCP2" if name.startswith("mcp_op2_") else None)
    assert openproject_kind("mcp_op2_pm_list_projects") == "local"
    assert server_for("mcp_op2_pm_list_projects") == "OpenProjectMCP2"
    assert server_for(SUITE) == "AIMDSSuiteMCP"
    assert server_for(LOCAL) == "OpenProjectMCP"


def test_suite_preferred_and_hidden_tools_skipped(monkeypatch):
    import agent.openproject_suite as ops

    names = {LOCAL, SUITE, "mcp_AtlassianMCP_jira_search"}
    assert openproject_tools(names, "pm_list_time_entries") == [SUITE, LOCAL]
    assert find_openproject_tool(names, "pm_list_time_entries") == SUITE
    assert find_openproject_tool(names, "pm_create_time_entry") is None
    monkeypatch.setattr(ops, "tool_hidden", lambda server, tool: server == "AIMDSSuiteMCP")
    assert find_openproject_tool(names, "pm_list_time_entries", skip_hidden=True) == LOCAL
    assert find_openproject_tool(names, "pm_list_time_entries") == SUITE


def test_registered_suite_names_use_an_underscore():
    """The gateway's raw `mcp_openproject-pm_x` registers as
    `mcp_AIMDSSuiteMCP_mcp_openproject_pm_x` (seen in session 20261005_085337_68f41b)."""
    from tools.openproject_names import openproject_kind

    assert openproject_kind("mcp_AIMDSSuiteMCP_mcp_openproject_pm_get_work_package") == "suite"
    assert openproject_kind("mcp_AIMDSSuiteMCP_mcp_openproject-pm_get_work_package") == "suite"
    assert openproject_kind("mcp_op_pm_get_work_package") == "local"
