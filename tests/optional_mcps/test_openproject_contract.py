"""The bundled OpenProject server is a superset of the Suite's go-mcp-openproject contract (AIS-479).

The snapshot is vendored from AIMDS-Suite (scripts/sync_openproject_contract.py).
Every Suite tool, parameter and required field must exist locally with a
compatible type; the local server may add optional parameters and tools,
but must never require more than the Suite does.
"""

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
SERVER_PATH = ROOT / "optional-mcps" / "OpenProjectMCP" / "server.py"
SNAPSHOT_PATH = ROOT / "optional-mcps" / "OpenProjectMCP" / "contract" / "go-mcp-openproject.tools.json"

OLD_TOOL_NAMES = {
    "list_projects", "project_context", "search_work_packages", "get_work_package", "list_time_entries",
    "create_work_package", "update_work_package", "delete_work_package", "add_comment", "manage_relation", "log_time",
}


def _load_server(monkeypatch, write=""):
    # No credentials: listing tools must not touch the network.
    monkeypatch.delenv("OPENPROJECT_BASE_URL", raising=False)
    monkeypatch.delenv("OPENPROJECT_API_TOKEN", raising=False)
    monkeypatch.setenv("OPENPROJECT_WRITE_PROJECTS", write)
    spec = importlib.util.spec_from_file_location(f"openproject_contract_{write or 'ro'}", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot():
    data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert data["source"] == "IAMDS-GMBH/AIMDS-Suite container/go-mcp-openproject"
    assert data["commit"]
    return {t["name"]: t for t in data["tools"]}


@pytest.fixture(params=["", "*"], ids=["read-only", "writable"])
def local_tools(request, monkeypatch):
    server = _load_server(monkeypatch, write=request.param)
    tools = asyncio.run(server.list_tools())
    return {t.name: {"name": t.name, "description": t.description, "inputSchema": t.inputSchema} for t in tools}


def _types(schema):
    kind = schema.get("type")
    if isinstance(kind, list):
        return set(kind)
    if kind:
        return {kind}
    found = set()
    for option in schema.get("anyOf", []) + schema.get("oneOf", []):
        found |= _types(option)
    return found


def _assert_compatible(tool, prop, suite, local):
    suite_types, local_types = _types(suite), _types(local)
    assert suite_types <= local_types or ("number" in local_types and suite_types == {"integer"}), (
        f"{tool}.{prop}: suite type {suite_types} not accepted by local {local_types}"
    )
    if "array" in suite_types:
        _assert_compatible(tool, f"{prop}[]", suite.get("items") or {}, local.get("items") or {})
    if "enum" in suite:
        assert "enum" not in local or set(suite["enum"]) <= set(local["enum"]), (
            f"{tool}.{prop}: local enum {local.get('enum')} lacks {set(suite['enum']) - set(local['enum'])}"
        )
    elif "enum" in local:
        pytest.fail(f"{tool}.{prop}: local adds an enum the suite does not restrict")


def test_snapshot_has_the_26_suite_tools():
    tools = _snapshot()
    assert len(tools) == 26
    assert all(name.startswith("pm_") for name in tools)


def test_every_suite_tool_exists_with_a_compatible_schema(local_tools):
    for name, suite in _snapshot().items():
        assert name in local_tools, f"suite tool {name} missing locally"
        local_schema = local_tools[name]["inputSchema"]
        suite_schema = suite["inputSchema"]
        assert local_schema.get("type") == "object"
        assert local_tools[name]["description"], f"{name} has no description"
        local_props = local_schema.get("properties") or {}
        for prop, spec in (suite_schema.get("properties") or {}).items():
            assert prop in local_props, f"{name}: parameter {prop} missing locally"
            _assert_compatible(name, prop, spec, local_props[prop])
        suite_required = set(suite_schema.get("required") or [])
        local_required = set(local_schema.get("required") or [])
        assert local_required <= suite_required, f"{name}: local requires more than the suite: {local_required - suite_required}"
        assert suite_required <= local_required, f"{name}: suite-required {suite_required - local_required} optional locally"


def test_local_extras_are_optional_and_prefixed(local_tools):
    suite = _snapshot()
    for name, tool in local_tools.items():
        assert name.startswith("pm_"), name
        if name not in suite:
            continue
        extra = set(tool["inputSchema"].get("properties") or {}) - set(suite[name]["inputSchema"].get("properties") or {})
        assert not extra & set(tool["inputSchema"].get("required") or []), f"{name}: extra params must be optional"


def test_all_tools_are_registered_even_read_only(monkeypatch):
    """Contract parity: the tool list never depends on OPENPROJECT_WRITE_PROJECTS."""
    read_only = {t.name for t in asyncio.run(_load_server(monkeypatch, write="").list_tools())}
    writable = {t.name for t in asyncio.run(_load_server(monkeypatch, write="*").list_tools())}
    assert read_only == writable
    assert set(_snapshot()) <= read_only
    assert read_only - set(_snapshot()) == {"pm_delete_work_package"}


def test_no_leftover_pre_contract_tool_names(local_tools):
    assert not OLD_TOOL_NAMES & set(local_tools)


def test_shared_descriptions_follow_the_suite(local_tools):
    """Work package, board, meeting and time entry descriptions are the
    Suite's text (identity tools and a few local hints differ on purpose)."""
    suite = _snapshot()
    verbatim = {"pm_list_work_packages", "pm_search_work_packages", "pm_comment_work_package", "pm_list_boards",
                "pm_get_board", "pm_list_meetings", "pm_get_meeting", "pm_create_meeting",
                "pm_add_meeting_agenda_item", "pm_delete_time_entry", "pm_list_reference_data", "pm_list_users",
                "pm_get_work_package", "pm_list_work_package_activity"}
    for name in verbatim:
        assert local_tools[name]["description"] == suite[name]["description"], name
    for name in set(suite) - verbatim - {"pm_link_account", "pm_link_status", "pm_unlink_account", "pm_list_time_entries"}:
        assert local_tools[name]["description"].startswith(suite[name]["description"].split(". ")[0]), name


def test_work_package_filter_descriptions_follow_the_suite(local_tools):
    """The filter semantics (strict names, open/closed, open_only, groups) are
    the Suite's since AIS-494, so their parameter texts are too."""
    suite = _snapshot()
    for name in ("pm_list_work_packages", "pm_search_work_packages"):
        local_props = local_tools[name]["inputSchema"]["properties"]
        for prop, spec in suite[name]["inputSchema"]["properties"].items():
            if "description" in spec:
                assert local_props[prop].get("description") == spec["description"], (name, prop)


def test_manifest_enables_exactly_the_server_tools(monkeypatch):
    import yaml

    manifest = yaml.safe_load((ROOT / "optional-mcps" / "OpenProjectMCP" / "manifest.yaml").read_text(encoding="utf-8"))
    names = {t.name for t in asyncio.run(_load_server(monkeypatch, write="").list_tools())}
    assert set(manifest["tools"]["default_enabled"]) == names
    assert manifest["tool_prefix"] == "op"
