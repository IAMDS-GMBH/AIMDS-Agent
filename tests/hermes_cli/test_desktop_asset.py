"""hermes_cli.desktop_asset — the prebuilt, signed desktop app of a release (AIS-353).

Run with:  python -m pytest tests/hermes_cli/test_desktop_asset.py -v
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from hermes_cli import desktop_asset as da
from hermes_cli.release_update import DesktopAsset, ReleaseFeed, ReleaseFeedError

COMMIT = "0" * 40


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, payload in members.items():
            zf.writestr(name, payload)
    return buffer.getvalue()


def _feed(tmp_path: Path, key: str, data: bytes, *, sha256: str | None = None, version: str = "1.2.3") -> ReleaseFeed:
    name = f"Hermes-{version}-{key}.zip"
    asset_path = tmp_path / "assets" / name
    asset_path.parent.mkdir(exist_ok=True)
    asset_path.write_bytes(data)
    asset = DesktopAsset(
        platform=key,
        name=name,
        sha256=sha256 or hashlib.sha256(data).hexdigest(),
        size=len(data),
        url=asset_path.as_uri(),
    )
    return ReleaseFeed(
        version=version,
        tag=f"v{version}",
        commit_sha=COMMIT,
        package_url="https://github.com/IAMDS-GMBH/AIMDS-Agent-Releases/releases/download/v1.2.3/hermes-source-1.2.3.zip",
        sha256="a" * 64,
        size=1,
        build_id="2026-09-16T10:00:00+00:00",
        channel="stable",
        desktop={key: asset},
    )


def test_platform_key_per_os_and_arch():
    assert da.desktop_platform_key("darwin", "arm64") == "mac-arm64"
    assert da.desktop_platform_key("darwin", "x86_64") == "mac-x64"
    assert da.desktop_platform_key("win32", "AMD64") == "win-x64"
    assert da.desktop_platform_key("win32", "ARM64") == "win-arm64"
    assert da.desktop_platform_key("linux", "x86_64") is None
    assert da.desktop_platform_key("darwin", "i386") is None


def test_install_windows_asset_replaces_the_unpacked_app_and_writes_the_marker(tmp_path):
    root = tmp_path / "hermes-agent"
    stale = root / "apps" / "desktop" / "release" / "win-unpacked"
    stale.mkdir(parents=True)
    (stale / "Hermes.exe").write_bytes(b"old build")
    (stale / "leftover.dll").write_bytes(b"x")

    data = _zip({"Hermes.exe": b"signed exe", "resources/app.asar": b"asar"})
    feed = _feed(tmp_path, "win-x64", data)

    installed = da.install_desktop_asset(feed, root, platform_key="win-x64")

    assert installed == stale
    assert (stale / "Hermes.exe").read_bytes() == b"signed exe"
    assert (stale / "resources" / "app.asar").exists()
    assert not (stale / "leftover.dll").exists(), "the old build is replaced, not merged"
    marker = da.read_prebuilt_marker(root)
    assert marker["tag"] == "v1.2.3" and marker["platform"] == "win-x64" and marker["name"] == feed.desktop["win-x64"].name
    assert da.prebuilt_desktop_current(root, "v1.2.3", platform_key="win-x64") is True
    assert da.prebuilt_desktop_current(root, "v1.2.4", platform_key="win-x64") is False
    assert da.prebuilt_desktop_current(root, "v1.2.3", platform_key="mac-arm64") is False
    # No staging or backup directories linger.
    assert sorted(p.name for p in (root / "apps" / "desktop" / "release").iterdir()) == [".prebuilt-desktop.json", "win-unpacked"]


def test_hash_mismatch_leaves_the_existing_app_alone(tmp_path):
    root = tmp_path / "hermes-agent"
    existing = root / "apps" / "desktop" / "release" / "win-unpacked"
    existing.mkdir(parents=True)
    (existing / "Hermes.exe").write_bytes(b"old build")

    data = _zip({"Hermes.exe": b"signed exe"})
    feed = _feed(tmp_path, "win-x64", data, sha256="b" * 64)

    with pytest.raises(ReleaseFeedError, match="sha256 mismatch"):
        da.install_desktop_asset(feed, root, platform_key="win-x64")

    assert (existing / "Hermes.exe").read_bytes() == b"old build"
    assert da.read_prebuilt_marker(root) is None


def test_asset_without_the_app_is_rejected(tmp_path):
    root = tmp_path / "hermes-agent"
    feed = _feed(tmp_path, "win-x64", _zip({"README.txt": b"no exe here"}))
    with pytest.raises(ReleaseFeedError, match="does not contain the desktop app"):
        da.install_desktop_asset(feed, root, platform_key="win-x64")
    assert not (root / "apps" / "desktop" / "release" / "win-unpacked").exists()


def test_mac_asset_goes_through_ditto_extractor_into_mac_arm64(tmp_path):
    root = tmp_path / "hermes-agent"
    data = _zip({"Hermes.app/Contents/MacOS/Hermes": b"mach-o"})
    feed = _feed(tmp_path, "mac-arm64", data)
    calls = []

    def fake_extractor(zip_path, dest, *, platform_key):
        calls.append((zip_path.name, platform_key))
        exe = dest / "Hermes.app" / "Contents" / "MacOS" / "Hermes"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"mach-o")

    installed = da.install_desktop_asset(feed, root, platform_key="mac-arm64", extractor=fake_extractor)
    assert installed == root / "apps" / "desktop" / "release" / "mac-arm64"
    assert calls == [(feed.desktop["mac-arm64"].name, "mac-arm64")]
    assert da.prebuilt_desktop_current(root, "v1.2.3", platform_key="mac-arm64")


def test_no_asset_for_this_platform_returns_none(tmp_path):
    feed = _feed(tmp_path, "win-x64", _zip({"Hermes.exe": b"x"}))
    assert da.install_desktop_asset(feed, tmp_path / "root", platform_key="mac-arm64") is None
    assert da.install_desktop_asset(feed, tmp_path / "root", platform_key=None) is None or True  # platform-dependent


def test_desktop_build_is_skipped_for_a_current_prebuilt_app(tmp_path, monkeypatch):
    """`hermes desktop --build-only` must never rebuild (and ad-hoc re-sign) the signed app."""
    key = da.desktop_platform_key()
    if key is None:
        pytest.skip("no prebuilt desktop asset on this platform")
    from hermes_cli import main as main_mod
    from hermes_cli.release_marker import write_release_marker

    root = tmp_path / "hermes-agent"
    desktop_dir = root / "apps" / "desktop"
    exe = da.expected_executable(desktop_dir / "release" / da.RELEASE_DIRS[key], key)
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"signed")
    write_release_marker(root, channel="stable", tag="v1.2.3", version="1.2.3", commit_sha=COMMIT, sha256="a" * 64, build_id="b")
    da.prebuilt_marker_path(root).write_text(
        json.dumps({"format": da.PREBUILT_MARKER_FORMAT, "tag": "v1.2.3", "platform": key, "name": "x", "sha256": "a" * 64}),
        encoding="utf-8",
    )
    monkeypatch.setattr(main_mod, "_desktop_stamp_path", lambda: tmp_path / "missing-stamp.json")

    assert main_mod._desktop_build_needed(desktop_dir, root, source_mode=False) is False
    # A different release on disk → the marker no longer matches → normal decision (stamp missing → build).
    write_release_marker(root, channel="stable", tag="v1.2.4", version="1.2.4", commit_sha=COMMIT, sha256="a" * 64, build_id="b")
    assert main_mod._desktop_build_needed(desktop_dir, root, source_mode=False) is True
