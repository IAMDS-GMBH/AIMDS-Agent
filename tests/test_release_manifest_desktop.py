"""scripts/release_manifest_desktop.py — desktop assets in hermes-release.json (AIS-353)."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "release_manifest_desktop.py"


def _manifest(tmp_path: Path, version: str = "1.2.3") -> Path:
    path = tmp_path / "hermes-release.json"
    path.write_text(
        json.dumps(
            {
                "format": "hermes-release-v1",
                "version": version,
                "tag": f"v{version}",
                "commit_sha": "0" * 40,
                "source_archive": f"hermes-source-{version}.zip",
                "sha256": "a" * 64,
                "size": 1,
                "built_at": "2026-09-16T10:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    return path


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


def test_records_every_asset_with_sha256_size_and_sidecar(tmp_path):
    manifest = _manifest(tmp_path)
    mac = tmp_path / "Hermes-1.2.3-mac-arm64.zip"
    win = tmp_path / "Hermes-1.2.3-win-x64.zip"
    mac.write_bytes(b"mac payload")
    win.write_bytes(b"windows payload!!")

    result = _run(str(manifest), str(win), str(mac))
    assert result.returncode == 0, result.stderr

    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert list(data["desktop"]) == ["mac-arm64", "win-x64"]
    assert data["desktop"]["mac-arm64"] == {
        "name": mac.name,
        "sha256": hashlib.sha256(b"mac payload").hexdigest(),
        "size": len(b"mac payload"),
    }
    assert data["desktop"]["win-x64"]["size"] == len(b"windows payload!!")
    # The other manifest fields are untouched.
    assert data["source_archive"] == "hermes-source-1.2.3.zip" and data["format"] == "hermes-release-v1"
    sidecar = (tmp_path / (mac.name + ".sha256")).read_text(encoding="utf-8")
    assert sidecar == f"{hashlib.sha256(b'mac payload').hexdigest()}  {mac.name}\n"


def test_rejects_a_foreign_version_or_name(tmp_path):
    manifest = _manifest(tmp_path)
    wrong_version = tmp_path / "Hermes-9.9.9-mac-arm64.zip"
    wrong_version.write_bytes(b"x")
    result = _run(str(manifest), str(wrong_version))
    assert result.returncode == 1 and "carries version 9.9.9" in result.stderr

    odd_name = tmp_path / "HermesSetup.dmg"
    odd_name.write_bytes(b"x")
    result = _run(str(manifest), str(odd_name))
    assert result.returncode == 1 and "not a Hermes-<version>-<os>-<arch>.zip" in result.stderr
    assert "desktop" not in json.loads(manifest.read_text(encoding="utf-8"))
