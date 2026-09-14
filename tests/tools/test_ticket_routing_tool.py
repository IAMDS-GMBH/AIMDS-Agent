"""tools/ticket_routing_tool.py — Jira vs. OpenProject per project (AIS-327)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import ticket_routing_tool as tr


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))


def _run(**args):
    return json.loads(tr.execute_ticket_routing(args))


class TestGetSetDefault:
    def test_unknown_project_asks_instead_of_guessing(self):
        out = _run(action="get", project="WSA")
        assert out["ask"] is True
        assert out["system"] is None and out["source"] is None
        assert out["clarify_choices"] == tr.CLARIFY_CHOICES
        assert "clarify" in out["instruction"] and "Do not guess" in out["instruction"]

    def test_set_then_get_uses_the_project_mapping(self):
        assert _run(action="set", project="WSA", system="openproject", note="migrated")["stored"] is True
        out = _run(action="get", project="wsa")
        assert out["ask"] is False
        assert out["system"] == "openproject" and out["source"] == "project"
        assert out["note"] == "migrated"

    def test_default_applies_when_no_project_mapping_exists(self):
        _run(action="set_default", system="openproject")
        out = _run(action="get", project="NEWPROJ")
        assert out["ask"] is False
        assert out["system"] == "openproject" and out["source"] == "default"

    def test_project_mapping_beats_the_default(self):
        """Jira stays the system for an external customer project after the
        default moved to OpenProject."""
        _run(action="set_default", system="openproject")
        _run(action="set", project="EXT", system="jira", note="external customer")
        assert _run(action="get", project="EXT")["system"] == "jira"
        assert _run(action="get", project="OTHER")["system"] == "openproject"

    def test_system_aliases_and_validation(self):
        assert _run(action="set", project="A", system="Atlassian")["system"] == "jira"
        assert _run(action="set", project="B", system="OP")["system"] == "openproject"
        assert "error" in _run(action="set", project="C", system="linear")
        assert "error" in _run(action="set", project="", system="jira")
        assert "error" in _run(action="nope")

    def test_unset_and_clear_default(self):
        _run(action="set", project="AIS", system="jira")
        _run(action="set_default", system="openproject")
        assert _run(action="unset", project="ais")["removed"] is True
        assert _run(action="get", project="AIS")["source"] == "default"
        assert _run(action="clear_default")["removed"] is True
        assert _run(action="get", project="AIS")["ask"] is True

    def test_list_reports_projects_and_default(self):
        _run(action="set", project="AIS", system="jira")
        _run(action="set", project="Kunde Müller", system="openproject")
        _run(action="set_default", system="openproject")
        out = _run(action="list")
        assert out["default"] == "openproject"
        keys = {p["project_key"]: p["system"] for p in out["projects"]}
        assert keys == {"AIS": "jira", "kunde müller": "openproject"}

    def test_normalization(self):
        assert tr.normalize_project(" ais ") == "AIS"
        assert tr.normalize_project("iamds-suite") == "iamds-suite"
        assert tr.normalize_project("Kunde   Müller") == "kunde müller"
        assert tr.normalize_project("") == ""


class TestAvailability:
    def test_requires_two_ticket_families(self):
        none = {"mcp_servers": {}}
        jira_only = {"mcp_servers": {"AtlassianMCP": {"command": "uvx"}, "TempoMCP": {"command": "npx"}}}
        both = {"mcp_servers": {"AtlassianMCP": {"command": "uvx"}, "OpenProjectMCP": {"command": "uvx", "tool_prefix": "op"}}}
        disabled = {"mcp_servers": {"AtlassianMCP": {"command": "uvx"}, "OpenProjectMCP": {"enabled": False}}}
        assert tr.configured_ticket_families(none) == []
        assert tr.configured_ticket_families(jira_only) == ["jira"]
        assert sorted(tr.configured_ticket_families(both)) == ["jira", "openproject"]
        assert tr.configured_ticket_families(disabled) == ["jira"]

    def test_family_detected_from_command_args(self):
        cfg = {"mcp_servers": {"jira": {"command": "uvx", "args": ["mcp-atlassian==0.23.1"]},
                               "op": {"command": "uvx", "args": ["openproject-ce-mcp==0.4.0"]}}}
        assert sorted(tr.configured_ticket_families(cfg)) == ["jira", "openproject"]

    def test_check_fn_reads_config(self, monkeypatch):
        monkeypatch.setattr(tr, "configured_ticket_families", lambda config=None: ["jira", "openproject"])
        assert tr.check_ticket_routing_requirements() is True
        monkeypatch.setattr(tr, "configured_ticket_families", lambda config=None: ["jira"])
        assert tr.check_ticket_routing_requirements() is False

    def test_registered_as_core_tool(self):
        from toolsets import _HERMES_CORE_TOOLS, TOOLSETS
        from tools.registry import registry

        assert "ticket_routing" in _HERMES_CORE_TOOLS
        assert TOOLSETS["ticket_routing"]["tools"] == ["ticket_routing"]
        assert registry.get_entry("ticket_routing") is not None


def test_explicit_db_path_is_honoured(tmp_path: Path):
    db = tmp_path / "custom.db"
    tr.execute_ticket_routing({"action": "set", "project": "X", "system": "jira"}, db_path=db)
    assert db.exists()
    assert json.loads(tr.execute_ticket_routing({"action": "get", "project": "X"}, db_path=db))["system"] == "jira"
