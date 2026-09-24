#!/usr/bin/env python3
"""Re-stamp a candidate's release assets for the stable version (AIS-396).

Usage: promote_release_assets.py <asset-dir> <stable-version>

``./createTag.sh promote stable`` republishes the candidate's artifacts under
the stable tag — same commit, same build, nothing rebuilt. Until AIS-396 the
job uploaded them byte-for-byte *and name-for-name*, so the stable release
carried ``hermes-release.json`` declaring ``0.7.6-rc.11`` plus assets called
``hermes-source-0.7.6-rc.11.zip``. Clients validate the manifest against the
release they fetched it from and against the channel, so every stable client
rejected it ("manifest tag ... does not match release ...", "tag ... is not a
stable release"), fell back to the source repository and filed an incident.

This script renames the versioned assets and rewrites the manifest for the
stable version. It deliberately does *not* touch any payload:

  * ``sha256``/``size`` stay as they are — renaming a file does not change its
    bytes, so the recorded digests remain correct.
  * ``commit_sha`` stays — clients treat the commit as authoritative and must
    keep seeing "candidate and stable are the same commit, nothing to install"
    (apps/desktop/electron/update-channels.cjs, resolveReleaseStatus).
  * ``built_at`` stays — it records when the build happened, not when it was
    promoted.

The archive's internal root folder keeps the candidate's name, which is fine:
the updater resolves the root without checking its name (hermes_cli/
release_update.py, _resolve_archive_root).

Idempotent: re-running with an already-stable manifest is a no-op, so a
re-dispatched promote job does not fail.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

STABLE_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
CANDIDATE_VERSION_RE = re.compile(r"^(?P<base>\d+\.\d+\.\d+)-rc\.\d+$")


def fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def rename_with_sidecar(directory: Path, old_name: str, new_name: str) -> None:
    """Rename ``old_name`` to ``new_name`` and rewrite its ``.sha256`` sidecar.

    The sidecar is in ``sha256sum -c`` format (``<digest>  <filename>``), so the
    filename inside it has to follow the rename or verification fails.
    """
    source = directory / old_name
    if not source.is_file():
        raise FileNotFoundError(f"{old_name} is named in the manifest but missing from {directory}")
    source.rename(directory / new_name)

    sidecar = directory / f"{old_name}.sha256"
    if sidecar.is_file():
        fields = sidecar.read_text(encoding="utf-8").split()
        if not fields:
            raise ValueError(f"{sidecar.name} is empty — cannot carry its digest over to {new_name}")
        sidecar.unlink()
        (directory / f"{new_name}.sha256").write_text(f"{fields[0]}  {new_name}\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2

    directory = Path(argv[1])
    stable_version = argv[2].lstrip("v")

    if not directory.is_dir():
        return fail(f"{directory} is not a directory")
    if not STABLE_VERSION_RE.match(stable_version):
        return fail(f"{stable_version!r} is not a stable version (X.Y.Z)")

    manifest_path = directory / "hermes-release.json"
    if not manifest_path.is_file():
        return fail(f"no hermes-release.json in {directory}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "hermes-release-v1":
        return fail(f"{manifest_path} is not a hermes-release-v1 manifest")

    current = str(manifest.get("version") or "")
    if current == stable_version:
        print(f"{manifest_path.name} already carries {stable_version} — nothing to re-stamp.")
        return 0

    candidate = CANDIDATE_VERSION_RE.match(current)
    if not candidate:
        return fail(f"manifest version {current!r} is neither {stable_version} nor a candidate of it")
    if candidate.group("base") != stable_version:
        return fail(
            f"manifest version {current!r} belongs to {candidate.group('base')}, "
            f"refusing to promote it to {stable_version}"
        )

    try:
        source_archive = str(manifest.get("source_archive") or "")
        expected_source = f"hermes-source-{current}.zip"
        if source_archive != expected_source:
            return fail(f"source_archive {source_archive!r} is not the expected {expected_source!r}")
        new_source = f"hermes-source-{stable_version}.zip"
        rename_with_sidecar(directory, source_archive, new_source)
        manifest["source_archive"] = new_source
        print(f"  {source_archive} -> {new_source}")

        desktop = manifest.get("desktop")
        if isinstance(desktop, dict):
            for platform, entry in desktop.items():
                if not isinstance(entry, dict):
                    return fail(f"desktop entry for {platform} is not an object")
                old_name = str(entry.get("name") or "")
                new_name = old_name.replace(current, stable_version, 1)
                if new_name == old_name:
                    return fail(f"desktop asset {old_name!r} does not carry version {current}")
                rename_with_sidecar(directory, old_name, new_name)
                entry["name"] = new_name
                print(f"  {old_name} -> {new_name}")
    except (FileNotFoundError, ValueError) as exc:
        return fail(str(exc))

    manifest["version"] = stable_version
    manifest["tag"] = f"v{stable_version}"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"re-stamped {manifest_path.name}: {current} -> {stable_version} (commit and digests unchanged)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
