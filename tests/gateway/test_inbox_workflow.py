from __future__ import annotations

from pathlib import Path

from gateway.inbox_workflow import format_inbox_confirmation, process_inbox_dictation


def _write_agents_with_inbox_route(root: Path) -> None:
    (root / "AGENTS.md").write_text(
        "\n".join([
            "| Task/Intent | Skill | Notes |",
            "|---|---|---|",
            "| dictation / voice-note / inbound-message workflow | inbox | required |",
        ]),
        encoding="utf-8",
    )


def test_process_inbox_dictation_creates_markdown_and_links(tmp_path: Path):
    _write_agents_with_inbox_route(tmp_path)
    (tmp_path / "project-roadmap.md").write_text(
        "# Project Roadmap\n\nBudget and roadmap details for Phoenix.",
        encoding="utf-8",
    )

    result = process_inbox_dictation(
        transcript="Please update the Phoenix budget and roadmap milestones.",
        workspace_root=str(tmp_path),
        source_platform="telegram",
        source_chat_id="chat-1",
    )

    assert result.success is True
    assert result.action == "created"
    assert result.classification
    assert Path(result.target_path).exists()
    assert len(result.links) >= 1


def test_process_inbox_dictation_extends_duplicate_entry(tmp_path: Path):
    _write_agents_with_inbox_route(tmp_path)

    first = process_inbox_dictation(
        transcript="Sync on customer onboarding checklist and milestones.",
        workspace_root=str(tmp_path),
        source_platform="telegram",
        source_chat_id="chat-2",
    )
    second = process_inbox_dictation(
        transcript="Sync on customer onboarding checklist and milestones.",
        workspace_root=str(tmp_path),
        source_platform="telegram",
        source_chat_id="chat-2",
    )

    assert first.success is True
    assert second.success is True
    assert second.action == "extended"
    assert first.target_path == second.target_path
    body = Path(second.target_path).read_text(encoding="utf-8")
    assert "## Update " in body


def test_process_inbox_dictation_fails_when_routing_missing(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text(
        "\n".join([
            "| Task/Intent | Skill | Notes |",
            "|---|---|---|",
            "| issue triage | triage | required |",
        ]),
        encoding="utf-8",
    )
    result = process_inbox_dictation(
        transcript="New voice note about team planning.",
        workspace_root=str(tmp_path),
        source_platform="telegram",
        source_chat_id="chat-3",
    )

    assert result.success is False
    assert result.stage == "classify"
    assert "route" in result.message.lower()


def test_format_inbox_confirmation_reports_no_links(tmp_path: Path):
    _write_agents_with_inbox_route(tmp_path)
    result = process_inbox_dictation(
        transcript="Track invoice follow-up with vendor next week.",
        workspace_root=str(tmp_path),
        source_platform="telegram",
        source_chat_id="chat-4",
    )
    summary = format_inbox_confirmation(result, str(tmp_path))
    assert "Inbox workflow completed." in summary
    assert "action:" in summary
    assert "file:" in summary
    assert "links:" in summary
