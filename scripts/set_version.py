#!/usr/bin/env python3
"""Sync the project version from a git tag (or explicit argument).

Usage:
    python scripts/set_version.py 0.17.0
    python scripts/set_version.py          # auto-detects from `git describe`

Updates:
    pyproject.toml  — project.version
    hermes_cli/__init__.py — fallback __version__ / __release_date__ strings
    apps/bootstrap-installer/package.json — version
    apps/bootstrap-installer/src-tauri/tauri.conf.json — version
    apps/bootstrap-installer/src-tauri/Cargo.toml — version

Called by the GitHub release workflow before building/installing the package so
that `importlib.metadata.version("hermes-agent")` returns the release tag
version at runtime.
"""

import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _git_version() -> str:
    """Return the current version from `git describe --tags --abbrev=0`."""
    try:
        raw = subprocess.check_output(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=ROOT, stderr=subprocess.DEVNULL,
        ).decode().strip()
        return raw.lstrip("v")
    except subprocess.CalledProcessError:
        sys.exit("No git tags found and no version argument given.")


def _validate(version: str) -> str:
    if not re.fullmatch(r"\d+\.\d+(\.\d+)?(-[\w.]+)?", version):
        sys.exit(f"Invalid semver: {version!r}")
    return version


def _patch_pyproject(version: str) -> None:
    path = ROOT / "pyproject.toml"
    text = path.read_text()
    new_text = re.sub(
        r'^(version\s*=\s*")[^"]*(")',
        lambda m: f'{m.group(1)}{version}{m.group(2)}',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if new_text == text:
        print(f"  pyproject.toml: version already {version!r}, no change")
    else:
        path.write_text(new_text)
        print(f"  pyproject.toml: version -> {version!r}")


def _patch_init(version: str, release_date: str) -> None:
    path = ROOT / "hermes_cli" / "__init__.py"
    text = path.read_text()
    # Update fallback __version__
    new_text = re.sub(
        r'(__version__\s*=\s*")[^"]*(")',
        lambda m: f'{m.group(1)}{version}{m.group(2)}',
        text,
    )
    # Update fallback __release_date__
    new_text = re.sub(
        r'(__release_date__\s*=\s*")[^"]*(")',
        lambda m: f'{m.group(1)}{release_date}{m.group(2)}',
        new_text,
    )
    if new_text == text:
        print(f"  hermes_cli/__init__.py: already up to date")
    else:
        path.write_text(new_text)
        print(f"  hermes_cli/__init__.py: version -> {version!r}, date -> {release_date!r}")


def _patch_installer_package_json(version: str) -> None:
    path = ROOT / "apps" / "bootstrap-installer" / "package.json"
    data = json.loads(path.read_text())
    if data.get("version") == version:
        print(f"  bootstrap-installer/package.json: already {version!r}, no change")
        return
    data["version"] = version
    path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"  bootstrap-installer/package.json: version -> {version!r}")


def _patch_installer_tauri_conf(version: str) -> None:
    path = ROOT / "apps" / "bootstrap-installer" / "src-tauri" / "tauri.conf.json"
    data = json.loads(path.read_text())
    if data.get("version") == version:
        print(f"  bootstrap-installer/tauri.conf.json: already {version!r}, no change")
        return
    data["version"] = version
    path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"  bootstrap-installer/tauri.conf.json: version -> {version!r}")


def _patch_installer_cargo_toml(version: str) -> None:
    path = ROOT / "apps" / "bootstrap-installer" / "src-tauri" / "Cargo.toml"
    text = path.read_text()
    new_text = re.sub(
        r'^(version\s*=\s*")[^"]*(")',
        lambda m: f'{m.group(1)}{version}{m.group(2)}',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if new_text == text:
        print(f"  bootstrap-installer/Cargo.toml: already {version!r}, no change")
    else:
        path.write_text(new_text)
        print(f"  bootstrap-installer/Cargo.toml: version -> {version!r}")


def main() -> None:
    version = _validate(sys.argv[1].lstrip("v") if len(sys.argv) > 1 else _git_version())
    release_date = date.today().strftime("%Y.%-m.%-d") if sys.platform != "win32" else date.today().strftime("%Y.%m.%d").lstrip("0").replace(".0", ".")
    print(f"Setting version to {version!r} (release date: {release_date})")
    _patch_pyproject(version)
    _patch_init(version, release_date)
    _patch_installer_package_json(version)
    _patch_installer_tauri_conf(version)
    _patch_installer_cargo_toml(version)
    print("Done.")


if __name__ == "__main__":
    main()
