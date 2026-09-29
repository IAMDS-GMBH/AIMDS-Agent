from __future__ import annotations

import argparse
import json

from hermes_cli import install_identity, support_logs


def test_install_id_is_created_once_and_reused(tmp_path):
    first = install_identity.get_install_id(tmp_path)
    second = install_identity.get_install_id(tmp_path)

    assert first.startswith("inst-")
    assert first == second
    assert (tmp_path / ".install-id").read_text(encoding="utf-8").strip() == first


def test_install_id_keeps_an_id_another_process_wrote(tmp_path):
    # The desktop (electron/install-id.cjs) writes the same file.
    (tmp_path / ".install-id").write_text("inst-0123456789abcdef0123456789abcdef\n", encoding="utf-8")

    assert install_identity.get_install_id(tmp_path) == "inst-0123456789abcdef0123456789abcdef"


def test_install_id_replaces_garbage_and_never_creates_when_asked_not_to(tmp_path):
    assert install_identity.get_install_id(tmp_path, create=False) == ""
    assert not (tmp_path / ".install-id").exists()

    (tmp_path / ".install-id").write_text("not an id", encoding="utf-8")
    assert install_identity.get_install_id(tmp_path, create=False) == ""


def test_install_id_uses_the_hermes_root_not_a_profile(tmp_path, monkeypatch):
    root = tmp_path / "data"
    monkeypatch.setenv("HERMES_HOME", str(root / "profiles" / "coder"))

    assert install_identity.install_id_path() == root / ".install-id"


def _capture_telemetry(monkeypatch):
    captured = {}

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"{}"

    def _fake_urlopen(req, timeout=0):
        captured.update(json.loads(req.data.decode("utf-8")))
        return _Resp()

    monkeypatch.setattr(support_logs.urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr(support_logs, "_support_config", lambda: {})
    return captured


def test_telemetry_reports_the_install_id_and_the_hermes_version(monkeypatch):
    # AIS-449: was hostname-user and the pyproject version (0.7.4).
    monkeypatch.delenv("HERMES_VERSION", raising=False)
    monkeypatch.setattr(install_identity, "get_install_id", lambda *a, **k: "inst-feedfacefeedfacefeedfacefeedface")
    monkeypatch.setattr(install_identity, "legacy_client_id", lambda: "MacBook-Pro.fritz.box-jane")
    monkeypatch.setattr(install_identity, "hermes_version", lambda: "0.7.8-rc.4")
    captured = _capture_telemetry(monkeypatch)

    assert support_logs.send_client_telemetry()["ok"] is True
    assert captured["client_id"] == "inst-feedfacefeedfacefeedfacefeedface"
    assert captured["legacy_client_id"] == "MacBook-Pro.fritz.box-jane"
    assert captured["version"] == "0.7.8-rc.4"


def test_telemetry_falls_back_to_the_legacy_id_when_the_install_id_cannot_be_written(monkeypatch):
    monkeypatch.setattr(install_identity, "get_install_id", lambda *a, **k: "")
    monkeypatch.setattr(install_identity, "legacy_client_id", lambda: "host-jane")
    captured = _capture_telemetry(monkeypatch)

    support_logs.send_client_telemetry()
    assert captured["client_id"] == "host-jane"
    assert captured["legacy_client_id"] == ""


def test_case_client_info_reports_the_hermes_version_and_keeps_the_app_version(monkeypatch):
    # The desktop passed app.getVersion() (0.7.4); the Python default was "v1.0.75".
    monkeypatch.setattr(install_identity, "hermes_version", lambda: "0.7.8-rc.3")
    monkeypatch.setattr(install_identity, "get_install_id", lambda *a, **k: "inst-feedfacefeedfacefeedfacefeedface")
    monkeypatch.setattr(support_logs, "_install_channel_and_patch", lambda version: ("preview", "fda924cdb4", 0))

    info = support_logs._case_client_info(argparse.Namespace(client_type="hermes-desktop", client_version="0.7.4"), "jane")

    assert info["client_version"] == "0.7.8-rc.3"
    assert info["app_version"] == "0.7.4"
    assert info["install_id"] == "inst-feedfacefeedfacefeedfacefeedface"
    assert (info["channel"], info["patch_level"]) == ("preview", "fda924cdb4")

    plain = support_logs._case_client_info(argparse.Namespace(client_version=""), "jane")
    assert plain["client_version"] == "0.7.8-rc.3"
    assert "app_version" not in plain


def test_channel_and_patch_come_from_the_release_marker(tmp_path, monkeypatch):
    from hermes_cli.release_marker import MARKER_FILENAME, MARKER_FORMAT

    root = tmp_path / "hermes-agent"
    root.mkdir()
    (root / MARKER_FILENAME).write_text(
        json.dumps(
            {
                "format": MARKER_FORMAT,
                "channel": "stable",
                "tag": "v0.7.7",
                "version": "0.7.7",
                "commit_sha": "2286edd0a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(support_logs, "get_hermes_home", lambda: tmp_path)

    assert support_logs._install_channel_and_patch("0.7.7") == ("stable", "2286edd0a0", 0)
