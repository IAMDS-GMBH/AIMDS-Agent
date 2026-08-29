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
    assert wt.template_version() == "v3"


def test_v1_workspace_gains_what_it_lacks_and_keeps_what_the_user_wrote(tmp_path):
    root = tmp_path / "vault"
    _seed_v1_workspace(root)

    out = wt.upgrade_workspace(root)

    assert out["version"] == "v3"
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
    assert wt.workspace_version(root) == "v3"


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


# ---------------------------------------------------------------------------
# v3: unedited old template files are replaced; frontmatter normalised once
# ---------------------------------------------------------------------------

JSON_NOTE = '''---
{
  "slug": "decision-worklog-rule",
  "title": "Worklog rule (29.08.2026)",
  "type": "decision",
  "scope": "project",
  "tags": ["decision", "worklog"],
  "updated_at": 1788030792,
  "confidence": null
}
---
The rule body.
'''


def _seed_v2_workspace(root: Path) -> None:
    """A vault created from template v2 (unedited copies) plus legacy notes."""
    root.mkdir(parents=True)
    (root / ".workspace-template-version").write_text("v2\n", encoding="utf-8")
    import subprocess

    for rel in ("AGENTS.md", "HARNESS.md", "_conventions.md", "projects/_hub.md", "_templates/note.md"):
        blob = subprocess.run(["git", "show", f"HEAD:installer/workspace-template/{rel}"], capture_output=True, cwd=_REPO_ROOT).stdout
        if not blob:
            blob = (TEMPLATE / rel).read_bytes()
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(blob)
    (root / "projects" / "decision-worklog-rule.md").write_text(JSON_NOTE, encoding="utf-8")
    (root / "projects" / "2026-urlaub-status.md").write_text('---\n{"slug": "x", "title": "", "type": "notes", "updated_at": 1787584429}\n---\n{"a": 1}\n', encoding="utf-8")
    (root / "knowledge").mkdir(exist_ok=True)
    (root / "knowledge" / "bare.md").write_text("# No frontmatter\n\ntext\n", encoding="utf-8")
    (root / "knowledge" / "fine.md").write_text("---\ntype: knowledge\ntitle: Fine\ncreated: 2026-08-01\nupdated: 2026-08-02\ntags: [it]\n---\n\nok\n", encoding="utf-8")
    (root / "journal").mkdir(exist_ok=True)
    (root / "journal" / "2026-08-29-arbeitszeit-aufstellung.md").write_text("---\ntitle: Aufstellung\ndate: 2026-08-29\nupdated: 2026-08-29T21:05:00+02:00\ntype: journal\ntags: [arbeitszeit]\n---\n\n# Aufstellung\n", encoding="utf-8")


def test_v3_replaces_unedited_v2_files_and_normalises_frontmatter(tmp_path):
    import yaml

    root = tmp_path / "vault"
    _seed_v2_workspace(root)
    # an edited managed file must still get a sibling, not be overwritten
    (root / "AGENTS.md").write_text((root / "AGENTS.md").read_text(encoding="utf-8") + "\n## my addition\n", encoding="utf-8")

    out = wt.upgrade_workspace(root)

    assert out["version"] == "v3"
    assert "projects/_hub.md" in out["replaced"] and "_templates/note.md" in out["replaced"]
    assert "AGENTS.md" in out["conflicts"] and (root / "AGENTS.md.template-new").is_file()
    assert (root / ".archive" / "template-v2" / "projects" / "_hub.md").is_file()
    assert 'aliases: ["Projects hub"]' in (root / "projects" / "_hub.md").read_text(encoding="utf-8")
    assert (root / ".obsidian" / "templates.json").is_file() and (root / "reports" / "_hub.md").is_file()
    assert (root / "_templates" / "report.md").is_file() and (root / "reports" / "reports.base").is_file()

    normalized = set(out["normalized"])
    assert {"projects/decision-worklog-rule.md", "projects/2026-urlaub-status.md", "journal/2026-08-29-arbeitszeit-aufstellung.md"} <= normalized
    assert "knowledge/fine.md" not in normalized
    assert "knowledge/bare.md" not in normalized  # no frontmatter = user content, left alone

    text = (root / "projects" / "decision-worklog-rule.md").read_text(encoding="utf-8")
    fm = {k: (str(v) if not isinstance(v, list) else v) for k, v in yaml.safe_load(text.split("---")[1]).items()}
    assert fm == {"type": "decision", "title": "Worklog rule (29.08.2026)", "created": "2026-08-29", "updated": "2026-08-29", "tags": ["decision", "worklog"], "source": "memory:decision-worklog-rule"}
    assert text.rstrip().endswith("The rule body.")
    assert (root / ".archive" / "frontmatter-v2" / "projects" / "decision-worklog-rule.md").read_text(encoding="utf-8") == JSON_NOTE

    urlaub = (root / "projects" / "2026-urlaub-status.md").read_text(encoding="utf-8")
    fm2 = yaml.safe_load(urlaub.split("---")[1])
    assert fm2["type"] == "note" and fm2["title"] == "2026 urlaub status" and "```json" in urlaub

    assert (root / "knowledge" / "bare.md").read_text(encoding="utf-8") == "# No frontmatter\n\ntext\n"

    j = yaml.safe_load((root / "journal" / "2026-08-29-arbeitszeit-aufstellung.md").read_text(encoding="utf-8").split("---")[1])
    assert str(j["updated"]) == "2026-08-29" and j["created"] and j["type"] == "journal"

    # idempotent
    again = wt.upgrade_workspace(root)
    assert again["skipped"] == ["already current"]


def test_dry_run_normalisation_changes_nothing(tmp_path):
    root = tmp_path / "vault"
    _seed_v2_workspace(root)
    before = (root / "projects" / "decision-worklog-rule.md").read_text(encoding="utf-8")
    out = wt.upgrade_workspace(root, dry_run=True)
    assert "projects/decision-worklog-rule.md" in out["normalized"]
    assert (root / "projects" / "decision-worklog-rule.md").read_text(encoding="utf-8") == before
    assert not (root / ".archive").exists()


def test_normalize_note_leaves_conforming_yaml_alone():
    good = "---\ntype: note\ntitle: T\ncreated: 2026-01-01\nupdated: 2026-01-02\n---\n\nbody\n"
    assert wt.normalize_note(good, stem="t", mtime_date="2026-08-29") is None
    assert wt.normalize_note("---\nnot: [valid\n---\n", stem="x", mtime_date="2026-08-29") is None


def test_ensure_hermes_home_never_upgrades_the_workspace(monkeypatch, tmp_path):
    """ensure_hermes_home() runs on every config load and module import; a
    v3 upgrade rewrites vault notes, so it must only run at gateway start
    and in `hermes update` (a stray `python -c "import …"` once normalised
    the developer's real vault)."""
    import hermes_cli.config as cfg

    calls = []
    monkeypatch.setattr(wt, "upgrade_configured_workspace", lambda: calls.append(1))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    cfg.ensure_hermes_home()
    assert calls == []
