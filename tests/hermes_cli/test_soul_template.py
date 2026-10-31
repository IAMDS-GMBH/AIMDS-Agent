"""One SOUL source, seeding and hash-gated updates (AIS-795).

New profiles get the shipped template. A SOUL.md that still equals a text we
shipped follows the current template; an edited one is never overwritten and
only reports that a newer template exists, with the diff, until dismissed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_cli import soul_template as st

_REPO = Path(__file__).resolve().parents[2]
_IDENTITY = _REPO / "installer" / "skills-hidden" / "aimds-loadout" / "identity"

# The upstream default SOUL that new profiles got before AIS-795
# (hermes_cli/default_soul.py, first version) — a shipped text.
_UPSTREAM_DEFAULT = (
    "You are Hermes Agent, an intelligent AI assistant created by Nous Research. "
    "You are helpful, knowledgeable, and direct. You assist users with a wide "
    "range of tasks including answering questions, writing and editing code, "
    "analyzing information, creative work, and executing actions via your tools. "
    "You communicate clearly, admit uncertainty when appropriate, and prioritize "
    "being genuinely useful over being verbose unless otherwise directed below. "
    "Be targeted and efficient in your exploration and investigations."
)


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "hermes"
    h.mkdir()
    return h


def _template(name="SOUL.md") -> str:
    return (_IDENTITY / name).read_text(encoding="utf-8")


# ── one source ───────────────────────────────────────────────────────────


def test_docker_soul_is_identical_to_the_loadout_source():
    assert (_REPO / "docker" / "SOUL.md").read_text(encoding="utf-8") == _template()


def test_every_shipped_template_version_is_registered():
    """Editing SOUL.md / SOUL.dev.md means adding the new hash, so the next
    change can update profiles that still carry this version."""
    data = json.loads((_IDENTITY / "shipped-hashes.json").read_text(encoding="utf-8"))
    for name in ("SOUL.md", "SOUL.dev.md"):
        digest = st.normalized_hash(_template(name))
        hashes = {entry["sha256"] for entry in data[name]}
        assert digest in hashes, f"add {digest} to shipped-hashes.json under {name}"
    assert st.normalized_hash(_UPSTREAM_DEFAULT) in {e["sha256"] for e in data["SOUL.md"]}


def test_template_is_shorter_and_has_no_upstream_identity():
    text = _template()
    assert len(text.encode("utf-8")) < 3_100
    for upstream in ("Nous Research", "nousresearch", "Hermes Agent"):
        assert upstream not in text
    assert "numbered follow-up choices" not in text


def test_upstream_default_soul_module_is_gone():
    assert not (_REPO / "hermes_cli" / "default_soul.py").exists()


# ── seeding ──────────────────────────────────────────────────────────────


def test_ensure_hermes_home_seeds_the_shipped_template(home, monkeypatch):
    from hermes_cli.config import ensure_hermes_home

    monkeypatch.setenv("HERMES_HOME", str(home))
    ensure_hermes_home()
    assert (home / "SOUL.md").read_text(encoding="utf-8") == _template()


def test_new_profile_is_seeded_from_the_template(tmp_path, monkeypatch):
    from hermes_cli.profiles import create_profile

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    default_home = tmp_path / ".hermes"
    default_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(default_home))

    profile_dir = create_profile("coder", no_alias=True)
    assert (profile_dir / "SOUL.md").read_text(encoding="utf-8") == _template()
    assert st.soul_status(profile_dir / "SOUL.md") == st.STATUS_CURRENT


def test_seed_never_overwrites(home):
    (home / "SOUL.md").write_text("# mine\n", encoding="utf-8")
    assert st.seed_soul(home) is False
    assert (home / "SOUL.md").read_text(encoding="utf-8") == "# mine\n"


# ── hash migration ───────────────────────────────────────────────────────


def test_shipped_version_is_updated_in_place(home):
    (home / "SOUL.md").write_text(_UPSTREAM_DEFAULT, encoding="utf-8")
    assert st.soul_status(home / "SOUL.md") == st.STATUS_SHIPPED
    assert st.migrate_soul(home) == {"SOUL.md": "updated"}
    assert (home / "SOUL.md").read_text(encoding="utf-8") == _template()
    assert st.migrate_soul(home) == {"SOUL.md": "current"}
    assert st.template_notice(home)["notice"] is False


def test_shipped_version_with_crlf_and_bom_still_matches(home):
    (home / "SOUL.md").write_bytes(("﻿" + _UPSTREAM_DEFAULT + "\r\n").encode("utf-8"))
    assert st.migrate_soul(home) == {"SOUL.md": "updated"}


def test_edited_soul_is_untouched_and_gets_a_notice(home):
    edited = _UPSTREAM_DEFAULT + "\n\nAlways sign off with my initials."
    (home / "SOUL.md").write_text(edited, encoding="utf-8")

    assert st.migrate_soul(home) == {"SOUL.md": "edited"}
    assert (home / "SOUL.md").read_text(encoding="utf-8") == edited

    info = st.template_notice(home)
    assert info["notice"] is True
    assert info["template"] == _template()
    assert "-Always sign off with my initials." in info["diff"]
    assert "+# SOUL — AIMDS Executive Principal & Chief of Staff" in info["diff"]

    # Dismissed for this template version …
    st.acknowledge_template(home)
    assert st.template_notice(home)["notice"] is False
    assert (home / "SOUL.md").read_text(encoding="utf-8") == edited
    # … and back once a different template ships.
    (home / st.ACK_FILE).write_text("an-older-template\n", encoding="utf-8")
    assert st.template_notice(home)["notice"] is True


def test_dev_variant_copy_follows_its_own_template(home):
    old_dev = "# SOUL — AIMDS Co-Developer\n\nold text\n"
    (home / "SOUL.md").write_text(_template(), encoding="utf-8")
    (home / "SOUL.dev.md").write_text(old_dev, encoding="utf-8")
    # Not a shipped dev text → edited, kept.
    assert st.migrate_soul(home) == {"SOUL.md": "current", "SOUL.dev.md": "edited"}
    assert (home / "SOUL.dev.md").read_text(encoding="utf-8") == old_dev


def test_migrate_all_covers_named_profiles(home):
    (home / "SOUL.md").write_text(_UPSTREAM_DEFAULT, encoding="utf-8")
    work = home / "profiles" / "work"
    work.mkdir(parents=True)
    (work / "SOUL.md").write_text("# my work persona\n", encoding="utf-8")
    stray = home / "profiles" / "backup"
    stray.mkdir()

    results = st.migrate_all(home)
    assert results[str(home)] == {"SOUL.md": "updated"}
    assert results[str(work)] == {"SOUL.md": "edited"}
    assert str(stray) not in results
    assert (work / "SOUL.md").read_text(encoding="utf-8") == "# my work persona\n"
    lines = st.summarize(results)
    assert any("updated to the new template" in line for line in lines)
    assert any("customised, kept" in line for line in lines)


def test_migrate_all_seeds_a_missing_default(home):
    assert st.migrate_all(home)[str(home)] == {"SOUL.md": "seeded"}
    assert (home / "SOUL.md").read_text(encoding="utf-8") == _template()


def test_update_sync_keeps_an_edited_soul(home, monkeypatch, capsys):
    from hermes_cli import main as hermes_main

    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr("hermes_constants.get_default_hermes_root", lambda: home)
    (home / "SOUL.md").write_text("# edited by the owner\n", encoding="utf-8")

    hermes_main._sync_canonical_soul_after_update()
    assert (home / "SOUL.md").read_text(encoding="utf-8") == "# edited by the owner\n"
    assert "a new template is available" in capsys.readouterr().out


def test_cli_entry_point_fails_without_a_template(home, monkeypatch):
    monkeypatch.setattr(st, "IDENTITY_DIR", home / "no-loadout")
    assert st.main([str(home)]) == 1
    assert not (home / "SOUL.md").exists()


def test_cli_entry_point_seeds_and_migrates(home, capsys):
    assert st.main([str(home)]) == 0
    assert (home / "SOUL.md").read_text(encoding="utf-8") == _template()
    assert "created from the template" in capsys.readouterr().out
