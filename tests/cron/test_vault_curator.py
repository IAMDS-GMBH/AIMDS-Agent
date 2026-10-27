"""AIS-748: the vault curator's mechanical steps run without the model (SUP-20261023-100653)."""

from __future__ import annotations

import logging
import os
import time

import pytest

from cron import vault_curator as vc
from tests.cron._run_job_stubs import FakeAgent, install_run_job_stubs

CURATOR_JOB = {
    "id": "aimds-vault-curator", "name": "Vault & Memory Curator", "prompt": "Fix the broken links.",
    "deliver": "local", "origin": {"source": "aimds-default-cron", "seed_key": "vault-curator"},
}


def _age(path, days):
    old = time.time() - days * 86400
    os.utime(path, (old, old))


def _vault(tmp_path):
    vault = tmp_path / "vault"
    (vault / "users" / "daniel").mkdir(parents=True)
    (vault / "_inbox").mkdir()
    (vault / "attachments").mkdir()
    (vault / "attachments" / "diagram.png").write_bytes(b"png")
    (vault / "knowledge").mkdir()
    (vault / "knowledge" / "arbeitszeit.md").write_text("# Arbeitszeit\n", encoding="utf-8")
    (vault / "_conventions.md").write_text(
        "Use `[[wikilinks]]` like this:\n```\n[[example-note]]\n```\n", encoding="utf-8"
    )
    (vault / "users" / "daniel" / "profile.md").write_text(
        "See [[arbeitszeit]], [[knowledge/arbeitszeit|Zeit]], ![[diagram.png]] and [[arbeitszeit-profil]].\n",
        encoding="utf-8",
    )
    return vault


def test_recognises_only_the_seeded_curator():
    assert vc.is_curator_job(CURATOR_JOB)
    assert not vc.is_curator_job({**CURATOR_JOB, "origin": {"source": "user", "seed_key": "vault-curator"}})
    assert not vc.is_curator_job({"origin": {"source": "aimds-default-cron", "seed_key": "morning-brief"}})


def test_broken_wikilinks_skip_code_and_resolve_names_paths_and_files(tmp_path):
    broken, notes = vc.broken_wikilinks(_vault(tmp_path))
    assert notes == 3
    assert broken == [("users/daniel/profile.md", "arbeitszeit-profil")]


def test_old_inbox_entries_are_moved_never_deleted(tmp_path):
    vault = _vault(tmp_path)
    inbox = vault / "_inbox"
    (inbox / "old.md").write_text("old", encoding="utf-8")
    (inbox / "new.md").write_text("new", encoding="utf-8")
    (inbox / "_archive").mkdir()
    (inbox / "_archive" / "old.md").write_text("archived earlier", encoding="utf-8")
    _age(inbox / "old.md", 10)

    assert vc.archive_old_inbox(vault) == ["old.md"]
    assert (inbox / "new.md").exists() and not (inbox / "old.md").exists()
    assert (inbox / "_archive" / "old.md").read_text(encoding="utf-8") == "archived earlier"
    assert (inbox / "_archive" / "old-2.md").read_text(encoding="utf-8") == "old"


def test_render_lists_what_is_left_for_the_model():
    report = vc.CuratorReport(
        archived=["a.md"], broken_links=[("users/daniel/profile.md", "arbeitszeit-profil")],
        broken_total=1, notes_scanned=74, indexed=74,
    )
    text = vc.render(report)
    assert text.startswith("## Collected Data")
    assert "moved 1 _inbox entries" in text and "re-indexed the vault" in text
    assert "- users/daniel/profile.md → [[arbeitszeit-profil]]" in text


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    vault = _vault(tmp_path)
    monkeypatch.setenv("HERMES_DOCUMENTS_DIR", str(tmp_path / "Documents"))
    install_run_job_stubs(monkeypatch, home, vault=vault, keep_prompt_builder=True)
    monkeypatch.setattr(vc, "_vault_dir", lambda: vault)
    monkeypatch.setattr(vc, "_sync_index", lambda v: 3)
    FakeAgent.response = "- users/daniel/profile.md: link fixed"
    return vault


def test_curator_run_hands_only_the_broken_links_to_the_agent_with_tools(env, caplog):
    from cron import scheduler

    caplog.set_level(logging.INFO)
    ok, _output, _response, err = scheduler.run_job(dict(CURATOR_JOB))
    assert ok, err
    agent = FakeAgent.instances[-1]
    assert agent.enabled_toolsets != []  # moves and link fixes need the vault tools
    prompt = agent.prompt_received
    assert "## Collected Data" in prompt and "[[arbeitszeit-profil]]" in prompt
    assert "Already done by Hermes" in prompt
    assert "vault curator archived=0 broken_links=1" in caplog.text


def test_curator_run_without_broken_links_is_silent_and_free(env, caplog):
    from cron import scheduler

    (env / "users" / "daniel" / "profile.md").write_text("See [[arbeitszeit]].\n", encoding="utf-8")
    caplog.set_level(logging.INFO)
    ok, output, _response, err = scheduler.run_job(dict(CURATOR_JOB))
    assert ok, err
    assert FakeAgent.instances == []
    assert "nothing left for the agent" in output
    assert "curator=nothing-left" in caplog.text
