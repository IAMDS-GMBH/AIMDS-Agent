"""scripts/promote_release_assets.py — re-stamp candidate assets for stable (AIS-396)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "promote_release_assets.py"


def _candidate_dir(tmp_path: Path, version: str = "1.2.3-rc.4", *, desktop: bool = True) -> Path:
    directory = tmp_path / "dist"
    directory.mkdir()

    archive = directory / f"hermes-source-{version}.zip"
    archive.write_bytes(b"source payload")
    (directory / f"{archive.name}.sha256").write_text(f"{'a' * 64}  {archive.name}\n", encoding="utf-8")

    manifest = {
        "format": "hermes-release-v1",
        "version": version,
        "tag": f"v{version}",
        "commit_sha": "c" * 40,
        "source_archive": archive.name,
        "sha256": "a" * 64,
        "size": archive.stat().st_size,
        "built_at": "2026-09-21T15:40:57+00:00",
    }

    if desktop:
        manifest["desktop"] = {}
        for platform in ("mac-arm64", "win-x64"):
            asset = directory / f"Hermes-{version}-{platform}.zip"
            asset.write_bytes(f"{platform} payload".encode())
            (directory / f"{asset.name}.sha256").write_text(f"{'b' * 64}  {asset.name}\n", encoding="utf-8")
            manifest["desktop"][platform] = {"name": asset.name, "sha256": "b" * 64, "size": asset.stat().st_size}

    # Unversioned installers ride along untouched.
    (directory / "HermesSetup.dmg").write_bytes(b"installer")

    (directory / "hermes-release.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return directory


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


def _manifest_of(directory: Path) -> dict:
    return json.loads((directory / "hermes-release.json").read_text(encoding="utf-8"))


def test_renames_assets_and_restamps_the_manifest(tmp_path):
    directory = _candidate_dir(tmp_path)

    result = _run(str(directory), "1.2.3")

    assert result.returncode == 0, result.stderr
    manifest = _manifest_of(directory)
    assert manifest["version"] == "1.2.3"
    assert manifest["tag"] == "v1.2.3"
    assert manifest["source_archive"] == "hermes-source-1.2.3.zip"
    assert manifest["desktop"]["mac-arm64"]["name"] == "Hermes-1.2.3-mac-arm64.zip"
    assert manifest["desktop"]["win-x64"]["name"] == "Hermes-1.2.3-win-x64.zip"

    assert (directory / "hermes-source-1.2.3.zip").is_file()
    assert not (directory / "hermes-source-1.2.3-rc.4.zip").exists()
    assert (directory / "Hermes-1.2.3-mac-arm64.zip").is_file()
    assert (directory / "HermesSetup.dmg").is_file()


def test_keeps_payload_facts_so_the_build_stays_the_promoted_one(tmp_path):
    directory = _candidate_dir(tmp_path)
    before = _manifest_of(directory)

    assert _run(str(directory), "1.2.3").returncode == 0

    after = _manifest_of(directory)
    # Renaming does not change bytes, and promotion does not rebuild: the
    # digests, sizes, commit and build time must survive untouched.
    assert after["commit_sha"] == before["commit_sha"]
    assert after["sha256"] == before["sha256"]
    assert after["size"] == before["size"]
    assert after["built_at"] == before["built_at"]
    assert after["desktop"]["mac-arm64"]["sha256"] == before["desktop"]["mac-arm64"]["sha256"]


def test_sidecars_follow_the_rename(tmp_path):
    directory = _candidate_dir(tmp_path)

    assert _run(str(directory), "1.2.3").returncode == 0

    sidecar = directory / "hermes-source-1.2.3.zip.sha256"
    assert sidecar.is_file()
    # sha256sum -c reads "<digest>  <filename>" — the name has to follow.
    assert sidecar.read_text(encoding="utf-8") == f"{'a' * 64}  hermes-source-1.2.3.zip\n"
    assert not (directory / "hermes-source-1.2.3-rc.4.zip.sha256").exists()
    assert (directory / "Hermes-1.2.3-win-x64.zip.sha256").read_text(
        encoding="utf-8"
    ) == f"{'b' * 64}  Hermes-1.2.3-win-x64.zip\n"


def test_is_idempotent_so_a_redispatched_promote_does_not_fail(tmp_path):
    directory = _candidate_dir(tmp_path)

    assert _run(str(directory), "1.2.3").returncode == 0
    second = _run(str(directory), "1.2.3")

    assert second.returncode == 0
    assert "already carries" in second.stdout
    assert _manifest_of(directory)["version"] == "1.2.3"


def test_works_without_desktop_assets(tmp_path):
    directory = _candidate_dir(tmp_path, desktop=False)

    assert _run(str(directory), "1.2.3").returncode == 0
    assert _manifest_of(directory)["source_archive"] == "hermes-source-1.2.3.zip"


def test_refuses_a_candidate_of_another_version(tmp_path):
    directory = _candidate_dir(tmp_path, version="1.2.3-rc.4")

    result = _run(str(directory), "9.9.9")

    assert result.returncode == 1
    assert "belongs to 1.2.3" in result.stderr
    # Nothing may have been renamed on a refusal.
    assert (directory / "hermes-source-1.2.3-rc.4.zip").is_file()


def test_rejects_a_non_stable_target(tmp_path):
    directory = _candidate_dir(tmp_path)

    result = _run(str(directory), "1.2.3-rc.5")

    assert result.returncode == 1
    assert "is not a stable version" in result.stderr


def test_reports_an_empty_sidecar_instead_of_crashing(tmp_path):
    directory = _candidate_dir(tmp_path)
    (directory / "hermes-source-1.2.3-rc.4.zip.sha256").write_text("", encoding="utf-8")

    result = _run(str(directory), "1.2.3")

    assert result.returncode == 1
    assert "is empty" in result.stderr
    assert "Traceback" not in result.stderr


def test_reports_an_asset_named_in_the_manifest_but_missing(tmp_path):
    directory = _candidate_dir(tmp_path)
    (directory / "Hermes-1.2.3-rc.4-win-x64.zip").unlink()

    result = _run(str(directory), "1.2.3")

    assert result.returncode == 1
    assert "missing from" in result.stderr
