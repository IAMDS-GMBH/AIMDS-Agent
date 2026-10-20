"""AIS-545: a ``main`` install takes a newer (pre-)release's signed desktop app."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hermes_cli import desktop_asset as da
from hermes_cli import main as cli_main
from hermes_cli import release_update as ru

KEY = "mac-arm64"


@pytest.fixture
def tree(tmp_path, monkeypatch):
    root = tmp_path / "hermes-agent"
    exe = root / "apps" / "desktop" / "release" / "mac-arm64" / "Hermes.app" / "Contents" / "MacOS" / "Hermes"
    exe.parent.mkdir(parents=True)
    exe.write_text("old build")
    (root / "apps" / "desktop" / "package.json").write_text("{}")
    monkeypatch.setattr(cli_main, "PROJECT_ROOT", root)
    monkeypatch.setattr(da, "desktop_platform_key", lambda: KEY)
    monkeypatch.setattr(da, "desktop_os_unsupported", lambda: None)
    # The jump needs an existing desktop app; the lookup is OS-specific, the
    # test runs on Linux CI too.
    monkeypatch.setattr(cli_main, "_desktop_packaged_executable", lambda d: exe)
    return root


def _git(branch="main", reachable=True, desktop_same=True):
    calls = []

    def fake(*args):
        calls.append(args)
        if args[0] == "rev-parse":
            return subprocess.CompletedProcess(args, 0, stdout=f"{branch}\n")
        if args[0] == "merge-base":
            return subprocess.CompletedProcess(args, 0 if reachable else 1)
        if args[0] == "diff":
            return subprocess.CompletedProcess(args, 0 if desktop_same else 1)
        raise AssertionError(args)

    fake.calls = calls
    return fake


def _feed(tag="v0.7.12-rc.1"):
    return SimpleNamespace(tag=tag, version=tag[1:], commit_sha="a" * 40, desktop={KEY: object()})


def _run(tree, monkeypatch, *, git=None, feed=None, marker=None, release_marker=None, install=None):
    monkeypatch.setattr(cli_main, "_git_ok", git or _git())
    installed = tree / "apps" / "desktop" / "release" / "mac-arm64"
    with patch.object(ru, "fetch_release_feed", side_effect=feed if isinstance(feed, Exception) else None, return_value=feed or _feed()) as fetch, \
         patch.object(da, "read_prebuilt_marker", return_value=marker), \
         patch("hermes_cli.release_marker.read_release_marker", return_value=release_marker), \
         patch.object(da, "install_desktop_asset", side_effect=install, return_value=installed) as inst, \
         patch.object(cli_main, "_sign_desktop_app_locally", return_value=True) as sign, \
         patch.object(cli_main, "_write_desktop_build_stamp") as stamp, \
         patch.object(cli_main, "_report_update_incident") as incident:
        jumped = cli_main._jump_to_prebuilt_desktop_on_main(tree / "apps" / "desktop")
    return jumped, SimpleNamespace(fetch=fetch, install=inst, sign=sign, stamp=stamp, incident=incident)


def test_jumps_to_a_newer_release_and_signs_locally(tree, monkeypatch):
    jumped, m = _run(tree, monkeypatch, marker={"tag": "v0.7.11"})
    assert jumped
    m.fetch.assert_called_once_with("preview")
    assert m.install.call_args.kwargs["signed"] == "local"
    signed_app = m.sign.call_args.args[0]
    assert signed_app.name == "Hermes.app" and signed_app.parent.name == "mac-arm64"
    m.stamp.assert_called_once()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"marker": {"tag": "v0.7.12-rc.1"}},  # already took this release's app
        {"marker": {"tag": "v0.7.12"}},  # took a higher one
        {"git": _git(branch="feature/x")},
        {"git": _git(reachable=False)},
        {"git": _git(desktop_same=False)},  # desktop changed since the release
        {"release_marker": {"tag": "v0.7.11"}},  # release installs keep their channel's app
        {"feed": ru.ReleaseFeedError("offline")},
    ],
)
def test_builds_locally_otherwise(tree, monkeypatch, kwargs):
    jumped, m = _run(tree, monkeypatch, **kwargs)
    assert not jumped
    m.install.assert_not_called()
    m.stamp.assert_not_called()


def test_failed_install_reports_and_builds_locally(tree, monkeypatch):
    jumped, m = _run(tree, monkeypatch, install=ru.ReleaseFeedError("sha256 mismatch"))
    assert not jumped
    m.incident.assert_called_once()
    assert m.incident.call_args.args[0] == "update-desktop-asset-failed"
    m.sign.assert_not_called()


def test_windows_keeps_the_pipeline_signature(tree, monkeypatch):
    monkeypatch.setattr(da, "desktop_platform_key", lambda: "win-x64")
    win = tree / "apps" / "desktop" / "release" / "win-unpacked"
    win.mkdir(parents=True)
    (win / "Hermes.exe").write_text("x")
    feed = SimpleNamespace(tag="v0.7.12-rc.1", version="0.7.12-rc.1", commit_sha="a" * 40, desktop={"win-x64": object()})
    monkeypatch.setattr(cli_main, "_git_ok", _git())
    with patch.object(ru, "fetch_release_feed", return_value=feed), \
         patch.object(da, "read_prebuilt_marker", return_value=None), \
         patch("hermes_cli.release_marker.read_release_marker", return_value=None), \
         patch.object(da, "install_desktop_asset", return_value=win) as inst, \
         patch.object(cli_main, "_sign_desktop_app_locally") as sign, \
         patch.object(cli_main, "_write_desktop_build_stamp"):
        assert cli_main._jump_to_prebuilt_desktop_on_main(tree / "apps" / "desktop")
    assert inst.call_args.kwargs["signed"] == "pipeline"
    sign.assert_not_called()


def test_build_only_skips_npm_after_a_jump(tree, monkeypatch, capsys):
    monkeypatch.setattr(cli_main.sys, "platform", "darwin")
    with patch.object(cli_main, "_jump_to_prebuilt_desktop_on_main", return_value=True) as jump, \
         patch.object(cli_main.shutil, "which", return_value=None):  # npm missing must not matter
        from tests.hermes_cli.test_gui_command import _ns

        cli_main.cmd_gui(_ns(build_only=True))
    jump.assert_called_once()
    assert "not launching; --build-only" in capsys.readouterr().out


def test_interactive_launch_never_asks_the_release_repo(tree, monkeypatch):
    with patch.object(cli_main, "_jump_to_prebuilt_desktop_on_main") as jump, \
         patch.object(cli_main, "_desktop_build_needed", return_value=False), \
         patch.object(cli_main.shutil, "which", return_value="/usr/bin/npm"), \
         patch.object(cli_main.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), \
         patch.object(cli_main, "_desktop_linux_sandbox_fixup", return_value=True):
        from tests.hermes_cli.test_gui_command import _ns

        try:
            cli_main.cmd_gui(_ns())
        except SystemExit:
            pass
    jump.assert_not_called()


def test_marker_records_commit_and_signer(tmp_path):
    import hashlib
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Hermes.exe", b"exe")
    data = buf.getvalue()
    asset_path = tmp_path / "Hermes-1.2.3-win-x64.zip"
    asset_path.write_bytes(data)
    feed = ru.ReleaseFeed(
        version="1.2.3", tag="v1.2.3", commit_sha="b" * 40, package_url="", sha256="", size=0, build_id="", channel="preview",
        desktop={"win-x64": ru.DesktopAsset(platform="win-x64", name=asset_path.name, sha256=hashlib.sha256(data).hexdigest(), size=len(data), url=asset_path.as_uri())},
    )
    root = tmp_path / "root"
    da.install_desktop_asset(feed, root, platform_key="win-x64", signed="local")
    marker = da.read_prebuilt_marker(root)
    assert marker["commit_sha"] == "b" * 40 and marker["signed"] == "local"
