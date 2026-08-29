"""skill_manage: bundled skills are read-only for background review / the curator."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tools import skill_provenance
from tools.skill_manager_tool import skill_manage


@pytest.fixture
def shipped_skill(tmp_path, monkeypatch):
    home = tmp_path / "home"
    skills = home / "skills"
    skill = skills / "aimds_custom" / "shipped"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: shipped\ndescription: shipped skill\n---\n# Shipped\n", encoding="utf-8")
    (skills / ".bundled_manifest").write_text("shipped:abc\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr("tools.skill_usage._prune_builtins_enabled", lambda: False)
    with patch("tools.skill_manager_tool.SKILLS_DIR", skills), \
            patch("agent.skill_utils.get_all_skills_dirs", return_value=[skills]):
        yield skill


def _as_background_review():
    return skill_provenance.set_current_write_origin(skill_provenance.BACKGROUND_REVIEW)


def test_background_review_cannot_delete_a_bundled_skill(shipped_skill):
    token = _as_background_review()
    try:
        out = json.loads(skill_manage(action="delete", name="shipped", absorbed_into=""))
    finally:
        skill_provenance.reset_current_write_origin(token)
    assert out.get("success") is False
    assert "read-only" in out["error"]
    assert (shipped_skill / "SKILL.md").exists()


def test_background_review_cannot_patch_or_write_into_a_bundled_skill(shipped_skill):
    token = _as_background_review()
    try:
        patched = json.loads(skill_manage(action="patch", name="shipped", old_string="# Shipped", new_string="# Absorbed"))
        written = json.loads(skill_manage(action="write_file", name="shipped", file_path="references/x.md", file_content="x"))
    finally:
        skill_provenance.reset_current_write_origin(token)
    assert "read-only" in patched["error"] and "read-only" in written["error"]
    assert "# Shipped" in (shipped_skill / "SKILL.md").read_text(encoding="utf-8")
    assert not (shipped_skill / "references").exists()


def test_background_review_may_still_touch_agent_created_skills(shipped_skill):
    own = shipped_skill.parent.parent / "mine"
    own.mkdir()
    (own / "SKILL.md").write_text("---\nname: mine\ndescription: mine\n---\n# Mine\n", encoding="utf-8")
    token = _as_background_review()
    try:
        out = json.loads(skill_manage(action="delete", name="mine", absorbed_into=""))
    finally:
        skill_provenance.reset_current_write_origin(token)
    assert out.get("success") is not False, out
    assert not own.exists()


def test_prune_builtins_on_lifts_the_guard(shipped_skill, monkeypatch):
    monkeypatch.setattr("tools.skill_usage._prune_builtins_enabled", lambda: True)
    token = _as_background_review()
    try:
        out = json.loads(skill_manage(action="delete", name="shipped", absorbed_into=""))
    finally:
        skill_provenance.reset_current_write_origin(token)
    assert "read-only" not in str(out.get("error", ""))
    assert not shipped_skill.exists()


def test_foreground_delete_of_a_bundled_skill_still_works(shipped_skill):
    out = json.loads(skill_manage(action="delete", name="shipped", absorbed_into=""))
    assert "read-only" not in str(out.get("error", ""))
    assert not shipped_skill.exists()
