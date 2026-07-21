from __future__ import annotations

from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_LOADOUT = _ROOT / "installer" / "skills-hidden" / "aimds-loadout"


def test_inbox_skill_exists_with_frontmatter_name():
    skill_path = _LOADOUT / "skills" / "inbox" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")

    assert skill_path.exists()
    assert "name: inbox" in content
    assert "description:" in content


def test_inbox_skill_enforces_required_stage_order():
    content = (_LOADOUT / "skills" / "inbox" / "SKILL.md").read_text(encoding="utf-8")

    required_steps = [
        "Klassifizieren",
        "Bestehend prüfen",
        "Erweitern oder neu anlegen",
        "Auto-Linking",
        "Bestätigen",
    ]
    for step in required_steps:
        assert step in content


def test_workspace_agents_routes_dictation_to_inbox_skill():
    agents = (_LOADOUT / "workspace" / "AGENTS.md").read_text(encoding="utf-8")

    assert "Process dictation / incoming voice note / inbound message into workspace inbox workflow" in agents
    assert "| `inbox` |" in agents


def test_skills_readme_lists_inbox_skill():
    skills_readme = (_LOADOUT / "skills" / "README.md").read_text(encoding="utf-8")

    assert "| `inbox` |" in skills_readme
    assert "Diktate/Nachrichten als Inbox-Workflow" in skills_readme
