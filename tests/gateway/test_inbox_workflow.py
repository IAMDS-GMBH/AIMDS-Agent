from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

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


def _write_agents_with_generic_inbox_route(root: Path) -> None:
    (root / "AGENTS.md").write_text(
        "\n".join([
            "| Intent | Skill | Notes |",
            "|---|---|---|",
            "| capture spoken updates into workspace log | inbox | required |",
        ]),
        encoding="utf-8",
    )


def _write_agents_with_filing_routes(root: Path) -> None:
    (root / "AGENTS.md").write_text(
        "\n".join([
            "| Category | Goes to | Example |",
            "|---|---|---|",
            "| Customer / contact | contacts/<name>.md | called Mueller, wants an offer |",
            "| Task | tasks/ | prepare offer by Friday |",
            "| Note | notes/ | quick thought on process |",
            "| Idea | ideas/ | idea for campaign |",
            "| Decision | decisions/<date>-<topic>.md | decided for vendor X |",
            "| Project | projects/<name>.md | website relaunch project |",
            "| Personal knowledge | knowledge/ | how I structure proposal |",
            "| Knowledge (company) | hand off to the KB curator | from now on we do offers like this |",
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
    body = Path(result.target_path).read_text(encoding="utf-8")
    assert "## Links" in body


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


def test_process_inbox_dictation_accepts_non_hardcoded_routing_wording(tmp_path: Path):
    _write_agents_with_generic_inbox_route(tmp_path)
    (tmp_path / "roadmap.md").write_text("Roadmap contains the phoenix budget milestones.", encoding="utf-8")

    result = process_inbox_dictation(
        transcript="Please update phoenix budget milestones in our roadmap plan.",
        workspace_root=str(tmp_path),
        source_platform="telegram",
        source_chat_id="chat-2b",
    )

    assert result.success is True
    assert result.action == "created"
    assert len(result.links) >= 1


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


def test_process_inbox_dictation_routes_with_agents_filing_table(tmp_path: Path):
    _write_agents_with_filing_routes(tmp_path)
    (tmp_path / "notes" / "offer-playbook.md").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "notes" / "offer-playbook.md").write_text(
        "prepare offer by Friday and include budget milestones",
        encoding="utf-8",
    )

    result = process_inbox_dictation(
        transcript="Please prepare offer by Friday for customer Mueller.",
        workspace_root=str(tmp_path),
        source_platform="telegram",
        source_chat_id="chat-routing",
    )

    assert result.success is True
    assert Path(result.target_path).exists()
    assert "tasks" in Path(result.target_path).as_posix()
    assert len(result.links) >= 1


def test_process_inbox_dictation_splits_mixed_input_into_multiple_entries(tmp_path: Path):
    _write_agents_with_filing_routes(tmp_path)

    result = process_inbox_dictation(
        transcript="called Mueller, wants an offer;\nprepare offer by Friday",
        workspace_root=str(tmp_path),
        source_platform="telegram",
        source_chat_id="chat-split",
    )

    assert result.success is True
    assert len(result.target_paths) >= 2
    joined = "\n".join(result.target_paths)
    assert "contacts" in joined
    assert "tasks" in joined


def test_process_inbox_dictation_parks_ambiguous_input_to_needs_triage(tmp_path: Path):
    _write_agents_with_filing_routes(tmp_path)

    result = process_inbox_dictation(
        transcript="zxqv brmt nplk uvwx",
        workspace_root=str(tmp_path),
        source_platform="telegram",
        source_chat_id="chat-triage",
    )

    assert result.success is True
    assert "_inbox/needs-triage" in Path(result.target_path).as_posix()
    body = Path(result.target_path).read_text(encoding="utf-8")
    assert "status: needs-triage" in body


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


def test_process_inbox_dictation_persists_durable_topic(tmp_path: Path):
    _write_agents_with_inbox_route(tmp_path)
    with patch("gateway.inbox_workflow.capture_durable_topic") as capture_mock:
        result = process_inbox_dictation(
            transcript="Document customer escalation lessons learned for next sprint.",
            workspace_root=str(tmp_path),
            source_platform="telegram",
            source_chat_id="chat-5",
        )

    assert result.success is True
    capture_mock.assert_called_once()
    kwargs = capture_mock.call_args.kwargs
    assert kwargs["source"] == "inbox-dictation"
    assert kwargs["confidence"] == 0.9
