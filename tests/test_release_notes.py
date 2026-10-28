"""scripts/release_notes.py — curated end-user release notes (AIS-750)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release_notes.py"
sys.path.insert(0, str(ROOT / "scripts"))

import release_notes  # noqa: E402


def _write(notes_dir: Path, version: str, notes: list, **extra) -> Path:
    notes_dir.mkdir(parents=True, exist_ok=True)
    path = notes_dir / f"{version}.json"
    path.write_text(json.dumps({"version": version, "notes": notes, **extra}), encoding="utf-8")
    return path


def _entry(group: str = "fixed", text: str = "Something works again", **extra) -> dict:
    return {"group": group, "de": f"{text} (de)", "en": f"{text} (en)", **extra}


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


def test_shipped_notes_are_valid():
    """Every file in release-notes/ passes the same rules the build enforces."""
    notes = release_notes.all_notes(ROOT / "release-notes")
    assert "0.7.13" in notes
    for entries in notes.values():
        assert entries


@pytest.mark.parametrize(
    "entry, reason",
    [
        ({"group": "other", "de": "x", "en": "x"}, "group must be"),
        ({"group": "new"}, "needs a 'de' or 'en'"),
        ({"group": "new", "de": "x", "en": ""}, "'en' must be"),
        ({"group": "new", "de": "x", "en": "y" * 400}, "'en' must be"),
        ({"group": "new", "de": "Behoben (#232)", "en": "Fixed"}, "ticket or PR number"),
        ({"group": "new", "de": "AIS-728 behoben", "en": "Fixed"}, "ticket or PR number"),
        ({"group": "new", "de": "x", "en": "y", "channels": ["nightly"]}, "'channels'"),
        ({"group": "new", "de": "x", "en": "y", "tag": "z"}, "unknown keys"),
    ],
)
def test_invalid_entries_are_rejected(tmp_path, entry, reason):
    path = _write(tmp_path, "1.2.3", [entry])
    with pytest.raises(release_notes.NotesError, match=reason):
        release_notes.load_notes(path)


def test_version_must_match_file_name(tmp_path):
    path = _write(tmp_path, "1.2.3", [_entry()])
    path.write_text(json.dumps({"version": "1.2.4", "notes": [_entry()]}), encoding="utf-8")
    with pytest.raises(release_notes.NotesError, match="expected '1.2.3'"):
        release_notes.load_notes(path)


def test_manifest_notes_cover_the_release_and_older_ones_newest_first(tmp_path):
    for version in ("1.2.1", "1.2.2", "1.2.3", "1.3.0"):
        _write(tmp_path, version, [_entry(text=version)])
    notes = release_notes.manifest_notes("1.2.3-rc.2", tmp_path, limit=2)
    assert list(notes) == ["1.2.3", "1.2.2"]
    assert notes["1.2.3"] == [{"group": "fixed", "de": "1.2.3 (de)", "en": "1.2.3 (en)"}]


def test_manifest_command_adds_notes_and_keeps_the_manifest(tmp_path):
    notes_dir = tmp_path / "notes"
    _write(notes_dir, "1.2.3", [_entry("new", "Teams", channels=["preview"])])
    manifest = tmp_path / "hermes-release.json"
    manifest.write_text(json.dumps({"format": "hermes-release-v1", "version": "1.2.3-rc.1"}), encoding="utf-8")

    result = _run("manifest", str(manifest), str(notes_dir))

    assert result.returncode == 0, result.stderr
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["format"] == "hermes-release-v1"
    assert data["notes"] == {
        "1.2.3": [{"group": "new", "de": "Teams (de)", "en": "Teams (en)", "channels": ["preview"]}]
    }


def test_manifest_command_warns_on_missing_notes_and_fails_on_broken_ones(tmp_path):
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    manifest = tmp_path / "hermes-release.json"
    manifest.write_text(json.dumps({"format": "hermes-release-v1", "version": "1.2.3"}), encoding="utf-8")

    missing = _run("manifest", str(manifest), str(notes_dir))
    assert missing.returncode == 0
    assert "1.2.3.json is missing" in missing.stderr
    assert "notes" not in json.loads(manifest.read_text(encoding="utf-8"))

    (notes_dir / "1.2.3.json").write_text("{not json", encoding="utf-8")
    broken = _run("manifest", str(manifest), str(notes_dir))
    assert broken.returncode == 1
    assert "error:" in broken.stderr


def test_one_language_is_enough(tmp_path):
    path = _write(tmp_path, "1.2.3", [{"group": "fixed", "de": "Nur deutsch"}])
    assert release_notes.load_notes(path) == [{"group": "fixed", "de": "Nur deutsch"}]


def test_check_command(tmp_path):
    _write(tmp_path, "1.2.3", [_entry()])
    assert _run("check", str(tmp_path)).returncode == 0
    _write(tmp_path, "1.2.4", [{"group": "nope"}])
    assert _run("check", str(tmp_path)).returncode == 1
