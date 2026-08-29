"""hermes_cli/workspace_template.py — installed workspaces follow the shipped template."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from hermes_cli import workspace_template as wt  # noqa: E402

TEMPLATE = Path(_REPO_ROOT) / "installer" / "workspace-template"


def _seed_v1_workspace(root: Path) -> None:
    """What an installation from template v1 looks like: AGENTS.md identical to
    v2, no HARNESS.md, one hub, user content in _findings.md, an edited
    _conventions.md."""
    root.mkdir(parents=True)
    (root / ".workspace-template-version").write_text("v1\n", encoding="utf-8")
    (root / "AGENTS.md").write_bytes((TEMPLATE / "AGENTS.md").read_bytes())
    (root / "_conventions.md").write_text("---\ntype: conventions\n---\n# My own conventions\n", encoding="utf-8")
    (root / "_findings.md").write_text("# Findings\n- ts=2026-08-01 | real user content\n", encoding="utf-8")
    for folder in ("projects", "knowledge", "tasks", "_inbox"):
        (root / folder).mkdir()
    (root / "projects" / "_hub.md").write_bytes((TEMPLATE / "projects" / "_hub.md").read_bytes())
    (root / "knowledge" / "aimds-suite.md").write_text("user note\n", encoding="utf-8")
    (root / "tasks" / "thisweek.md").write_text("- [ ] ship the sql toolset\n", encoding="utf-8")


def test_template_dir_and_version_are_found():
    assert wt.template_dir() == TEMPLATE
    assert wt.template_version() == "v2"


def test_v1_workspace_gains_what_it_lacks_and_keeps_what_the_user_wrote(tmp_path):
    root = tmp_path / "vault"
    _seed_v1_workspace(root)

    out = wt.upgrade_workspace(root)

    assert out["version"] == "v2"
    assert (root / "HARNESS.md").is_file()
    assert (root / "knowledge" / "_hub.md").is_file()
    assert (root / "_templates" / "project.md").is_file()
    assert "HARNESS.md" in out["added"] and "knowledge/_hub.md" in out["added"]
    assert sum(1 for p in root.rglob("_hub.md")) == sum(1 for p in TEMPLATE.rglob("_hub.md"))
    # user content untouched
    assert (root / "_findings.md").read_text(encoding="utf-8").endswith("real user content\n")
    assert (root / "tasks" / "thisweek.md").read_text(encoding="utf-8") == "- [ ] ship the sql toolset\n"
    assert (root / "knowledge" / "aimds-suite.md").read_text(encoding="utf-8") == "user note\n"
    # edited managed file kept, new text alongside
    assert "My own conventions" in (root / "_conventions.md").read_text(encoding="utf-8")
    assert (root / "_conventions.md.template-new").is_file()
    assert out["conflicts"] == ["_conventions.md"]
    assert not list(root.rglob(".gitkeep"))
    assert wt.workspace_version(root) == "v2"


def test_upgrade_is_idempotent(tmp_path):
    root = tmp_path / "vault"
    _seed_v1_workspace(root)
    wt.upgrade_workspace(root)
    snapshot = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    again = wt.upgrade_workspace(root)

    assert again["skipped"] == ["already current"]
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == snapshot


def test_dry_run_changes_nothing(tmp_path):
    root = tmp_path / "vault"
    _seed_v1_workspace(root)
    out = wt.upgrade_workspace(root, dry_run=True)
    assert "HARNESS.md" in out["added"]
    assert not (root / "HARNESS.md").exists()
    assert wt.workspace_version(root) == "v1"


def test_unseeded_directory_is_left_to_the_installer(tmp_path, monkeypatch):
    monkeypatch.setattr("hermes_cli.config._resolve_workspace_dir", lambda: tmp_path)
    assert wt.upgrade_configured_workspace() is None
    assert not (tmp_path / "AGENTS.md").exists()
