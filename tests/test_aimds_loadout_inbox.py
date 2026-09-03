"""The AIMDS inbox skill (dictations / inbound messages → workspace inbox workflow).

The skill moved from the hidden loadout bundle to ``skills/aimds_custom/inbox``
and its body is English now (46ba95a63); the loadout README still lists it and
the workspace template's AGENTS.md routes ``_inbox/`` items to it.
"""

from __future__ import annotations

from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_SKILL = _ROOT / "skills" / "aimds_custom" / "inbox" / "SKILL.md"
_LOADOUT = _ROOT / "installer" / "skills-hidden" / "aimds-loadout"
_WORKSPACE_AGENTS = _ROOT / "installer" / "workspace-template" / "AGENTS.md"


def test_inbox_skill_exists_with_frontmatter_name():
    content = _SKILL.read_text(encoding="utf-8")

    assert content.startswith("---")
    assert "name: inbox" in content
    assert "description:" in content


def test_inbox_skill_enforces_required_stage_order():
    content = _SKILL.read_text(encoding="utf-8")

    required_steps = [
        "Set idempotency marker",
        "Classify",
        "Check existing",
        "Extend or create",
        "Auto-linking",
        "Archive",
        "Confirm",
    ]
    positions = [content.find(step) for step in required_steps]
    assert all(pos >= 0 for pos in positions), dict(zip(required_steps, positions))
    assert positions == sorted(positions), "inbox phases must keep their mandatory order"


def test_workspace_agents_routes_inbox_items_to_inbox_skill():
    agents = _WORKSPACE_AGENTS.read_text(encoding="utf-8")

    assert "_inbox/" in agents
    assert "inbox skill" in agents.lower()


def test_loadout_readme_lists_inbox_skill():
    readme = (_LOADOUT / "README.md").read_text(encoding="utf-8")

    assert "inbox/SKILL.md" in readme
