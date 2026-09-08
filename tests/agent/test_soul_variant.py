"""load_soul_md identity variants (AIS-309).

Resolution for ``variant="dev"``: the user's ``HERMES_HOME/SOUL.dev.md`` →
the packaged loadout ``SOUL.dev.md`` → plain ``SOUL.md``. No variant keeps
the historical behaviour (SOUL.md only).
"""

from pathlib import Path

import pytest

from agent import prompt_builder as pb
from hermes_constants import get_hermes_home

_LOADOUT_DEV = Path(pb.__file__).resolve().parent.parent / "installer/skills-hidden/aimds-loadout/identity/SOUL.dev.md"


@pytest.fixture
def home():
    h = get_hermes_home()
    h.mkdir(parents=True, exist_ok=True)
    return h


def test_packaged_dev_identity_exists_and_is_lean():
    text = _LOADOUT_DEV.read_text(encoding="utf-8")
    assert text.startswith("# SOUL — AIMDS Co-Developer")
    assert len(text) < 4000
    assert "user's language" in text
    for coworker_only in ("office_word", "Chief of Staff", "AIMDS-Suite-Vault"):
        assert coworker_only not in text


def test_user_override_wins_over_packaged(home):
    (home / "SOUL.dev.md").write_text("# my dev soul\n", encoding="utf-8")
    assert pb.load_soul_md(variant="dev") == "# my dev soul"


def test_packaged_variant_used_when_no_override(home):
    (home / "SOUL.md").write_text("# co-worker soul\n", encoding="utf-8")
    out = pb.load_soul_md(variant="dev")
    assert out.startswith("# SOUL — AIMDS Co-Developer")
    # …and the plain call still returns the co-worker identity.
    assert pb.load_soul_md() == "# co-worker soul"


def test_falls_back_to_soul_md_when_variant_missing(home, monkeypatch):
    (home / "SOUL.md").write_text("# co-worker soul\n", encoding="utf-8")
    monkeypatch.setattr(pb, "_LOADOUT_IDENTITY_DIR", home / "no-such-dir")
    assert pb.load_soul_md(variant="dev") == "# co-worker soul"


def test_variant_matches_seeded_default_when_nothing_else_exists(home, monkeypatch):
    # ensure_hermes_home() seeds the upstream default SOUL.md on first use,
    # so the variant lookup lands on exactly what the plain call returns.
    monkeypatch.setattr(pb, "_LOADOUT_IDENTITY_DIR", home / "no-such-dir")
    assert pb.load_soul_md(variant="dev") == pb.load_soul_md()
    assert pb.load_soul_md(variant="dev")
