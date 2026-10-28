#!/usr/bin/env python3
"""Curated end-user release notes (AIS-750).

One file per release, ``release-notes/<X.Y.Z>.json``, written by hand before
the release is tagged::

    {
      "version": "0.7.13",
      "notes": [
        {"group": "new", "de": "Kanäle in Microsoft Teams lesen …", "en": "Read Microsoft Teams channels …"},
        {"group": "fixed", "de": "…", "en": "…", "channels": ["preview"]}
      ]
    }

``group`` is ``new``, ``improved`` or ``fixed``; ``de`` and ``en`` are short
end-user sentences (no tool or protocol names, no ticket or PR numbers);
``channels`` optionally limits an entry to some update channels. A release
candidate ``X.Y.Z-rc.N`` uses the notes of ``X.Y.Z``.

The desktop update dialog reads the files from the target commit (git
installs) or from the ``notes`` field of ``hermes-release.json`` (release
installs). Keep the entry rules in sync with
apps/desktop/electron/release-notes.cjs.

Usage:
  release_notes.py check [<dir>]                    validate every notes file
  release_notes.py manifest <hermes-release.json> [<dir>]
                                                    add ``notes`` to the manifest
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

GROUPS = ("new", "improved", "fixed")
MAX_TEXT = 300
MAX_ENTRIES = 40
# Versions the manifest carries: a client that skipped a few releases still
# sees what changed, the manifest stays small.
MANIFEST_VERSIONS = 8
NOTE_FILE_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)\.json$")
VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-rc\.\d+)?$")
DEFAULT_DIR = Path(__file__).resolve().parents[1] / "release-notes"


class NotesError(ValueError):
    pass


def base_version(version: str) -> tuple[int, int, int]:
    match = VERSION_RE.match(str(version).strip())
    if not match:
        raise NotesError(f"'{version}' is not X.Y.Z or X.Y.Z-rc.N")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def load_notes(path: Path) -> list[dict]:
    """Validated entries of one notes file; raises ``NotesError`` on any flaw."""
    match = NOTE_FILE_RE.match(path.name)
    if not match:
        raise NotesError(f"{path.name}: file name must be X.Y.Z.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NotesError(f"{path.name}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("notes"), list):
        raise NotesError(f"{path.name}: expected an object with a 'notes' list")
    version = path.name[: -len(".json")]
    if data.get("version") not in (None, version):
        raise NotesError(f"{path.name}: 'version' is {data.get('version')!r}, expected {version!r}")
    notes = data["notes"]
    if not notes or len(notes) > MAX_ENTRIES:
        raise NotesError(f"{path.name}: needs 1..{MAX_ENTRIES} entries, has {len(notes)}")
    entries: list[dict] = []
    for index, item in enumerate(notes):
        where = f"{path.name} entry {index}"
        if not isinstance(item, dict):
            raise NotesError(f"{where}: not an object")
        unknown = set(item) - {"group", "de", "en", "channels"}
        if unknown:
            raise NotesError(f"{where}: unknown keys {sorted(unknown)}")
        if item.get("group") not in GROUPS:
            raise NotesError(f"{where}: group must be one of {GROUPS}")
        entry = {"group": item["group"]}
        # Write both; one is accepted (the dialog falls back to the other).
        for lang in ("de", "en"):
            text = item.get(lang)
            if text is None:
                continue
            if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
                raise NotesError(f"{where}: '{lang}' must be a non-empty text of at most {MAX_TEXT} characters")
            if re.search(r"#\d+|\b(?:AIS|SUP|DEVOPS)-\d+", text):
                raise NotesError(f"{where}: '{lang}' names a ticket or PR number")
            entry[lang] = " ".join(text.split())
        if "de" not in entry and "en" not in entry:
            raise NotesError(f"{where}: needs a 'de' or 'en' text")
        if "channels" in item:
            channels = item["channels"]
            if not isinstance(channels, list) or not channels or not all(
                c in ("stable", "preview", "main") for c in channels
            ):
                raise NotesError(f"{where}: 'channels' must list stable/preview/main")
            entry["channels"] = channels
        entries.append(entry)
    return entries


def all_notes(notes_dir: Path) -> dict[str, list[dict]]:
    """Every notes file in ``notes_dir``, validated (raises on the first flaw)."""
    out: dict[str, list[dict]] = {}
    if not notes_dir.is_dir():
        return out
    for path in sorted(notes_dir.iterdir()):
        if path.suffix != ".json":
            continue
        out[path.name[: -len(".json")]] = load_notes(path)
    return out


def manifest_notes(version: str, notes_dir: Path = DEFAULT_DIR, limit: int = MANIFEST_VERSIONS) -> dict[str, list[dict]]:
    """Notes of ``version``'s release and the ``limit - 1`` releases before it, newest first."""
    ceiling = base_version(version)
    notes = all_notes(notes_dir)
    versions = sorted((v for v in notes if base_version(v) <= ceiling), key=base_version, reverse=True)
    return {v: notes[v] for v in versions[:limit]}


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in ("check", "manifest"):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        if argv[1] == "check":
            notes_dir = Path(argv[2]) if len(argv) > 2 else DEFAULT_DIR
            notes = all_notes(notes_dir)
            print(f"{len(notes)} release notes file(s) valid in {notes_dir}")
            return 0
        if len(argv) < 3:
            print(__doc__, file=sys.stderr)
            return 2
        manifest_path = Path(argv[2])
        notes_dir = Path(argv[3]) if len(argv) > 3 else DEFAULT_DIR
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        version = str(manifest.get("version") or "")
        notes = manifest_notes(version, notes_dir)
        major, minor, patch = base_version(version)
        if f"{major}.{minor}.{patch}" not in notes:
            print(
                f"warning: release-notes/{major}.{minor}.{patch}.json is missing — the update dialog shows a generic line",
                file=sys.stderr,
            )
        if notes:
            manifest["notes"] = notes
            manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"release notes in manifest: {', '.join(notes) or 'none'}")
        return 0
    except NotesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
