from __future__ import annotations

import argparse
import io
import json
import zipfile

from hermes_cli import support_logs
from hermes_cli.subcommands.support import build_support_parser


def _parse(argv: list[str]):
    parser = argparse.ArgumentParser(prog="hermes")
    subparsers = parser.add_subparsers(dest="command")
    build_support_parser(subparsers, cmd_support=lambda args: None)
    return parser.parse_args(["support", "send-logs", *argv])


def test_send_logs_defaults_upload_url_and_anonymous_auth(tmp_path, monkeypatch, capsys):
    hermes_home = tmp_path / ".hermes"
    logs_dir = hermes_home / "logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / "desktop.log").write_text("sample log line\n", encoding="utf-8")

    monkeypatch.setattr(support_logs, "get_hermes_home", lambda: hermes_home)
    monkeypatch.setattr(support_logs, "display_hermes_home", lambda: "~/.hermes")
    monkeypatch.setattr(support_logs, "_support_config", lambda: {})
    monkeypatch.setattr(support_logs, "_capture_dump_text", lambda: "dump info\n")

    captured = {}

    class _Resp:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"job_id":"job-123","reference_id":"SUP-2026-001"}'

    def _fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["data"] = req.data
        return _Resp()

    monkeypatch.setattr(support_logs.urllib.request, "urlopen", _fake_urlopen)

    args = _parse(["--json"])
    code = support_logs.run_send_logs(args)
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert captured["url"] == "https://suite-support.iamds.com/api/v1/upload"
    assert captured["headers"]["Authorization"] == "Bearer anonymous"


def test_send_logs_uses_custom_url_and_api_key(tmp_path, monkeypatch, capsys):
    hermes_home = tmp_path / ".hermes"
    logs_dir = hermes_home / "logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / "desktop.log").write_text("sample log line\n", encoding="utf-8")

    monkeypatch.setattr(support_logs, "get_hermes_home", lambda: hermes_home)
    monkeypatch.setattr(support_logs, "display_hermes_home", lambda: "~/.hermes")
    monkeypatch.setattr(
        support_logs,
        "_support_config",
        lambda: {"upload_url": "https://custom-support.example.com", "api_key": "my-key"},
    )
    monkeypatch.setattr(support_logs, "_capture_dump_text", lambda: "dump info\n")

    captured = {}

    class _Resp:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"job_id":"job-456"}'

    def _fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        return _Resp()

    monkeypatch.setattr(support_logs.urllib.request, "urlopen", _fake_urlopen)

    args = _parse(["--json"])
    code = support_logs.run_send_logs(args)
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert captured["url"] == "https://custom-support.example.com/api/v1/upload"
    assert captured["headers"]["Authorization"] == "Bearer my-key"


def test_send_logs_uploads_redacted_bundle(tmp_path, monkeypatch, capsys):
    hermes_home = tmp_path / ".hermes"
    logs_dir = hermes_home / "logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / "desktop.log").write_text("token=sk-abcdefghijklmnopqrstuv\n", encoding="utf-8")

    monkeypatch.setattr(support_logs, "get_hermes_home", lambda: hermes_home)
    monkeypatch.setattr(support_logs, "display_hermes_home", lambda: "~/.hermes")
    monkeypatch.setattr(
        support_logs,
        "_support_config",
        lambda: {"upload_url": "https://support.example/upload", "api_key": "secret", "timeout_seconds": 10},
    )
    monkeypatch.setattr(support_logs, "_capture_dump_text", lambda: "OPENAI_API_KEY=sk-abcdefghijklmnopqrstuv\n")

    captured = {}

    class _Resp:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"reference_id":"AIS-117"}'

    def _fake_urlopen(req, timeout=0):
        captured["headers"] = dict(req.header_items())
        captured["data"] = req.data
        captured["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(support_logs.urllib.request, "urlopen", _fake_urlopen)

    args = _parse(["--json", "--reason", "on_demand"])
    code = support_logs.run_send_logs(args)
    assert code == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["reference_id"] == "AIS-117"
    assert payload["status_code"] == 202

    bundle_bytes = io.BytesIO(captured["data"])
    with zipfile.ZipFile(bundle_bytes) as zf:
        desktop_log = zf.read("logs/desktop.log").decode("utf-8")
        dump_text = zf.read("dump.txt").decode("utf-8")
        assert "sk-abcdefghijklmnopqrstuv" not in desktop_log
        assert "sk-abcdefghijklmnopqrstuv" not in dump_text
        assert "..." in desktop_log
        assert "***" in dump_text


def test_normalize_telemetry_url():
    from hermes_cli.support_logs import normalize_telemetry_url

    assert normalize_telemetry_url("") == "https://suite-support.iamds.com/api/v1/telemetry"
    assert normalize_telemetry_url("https://suite-support.iamds.com/api/v1/upload") == "https://suite-support.iamds.com/api/v1/telemetry"
    assert normalize_telemetry_url("https://custom.example.com/upload") == "https://custom.example.com/telemetry"
    assert normalize_telemetry_url("https://custom.example.com") == "https://custom.example.com/api/v1/telemetry"


def test_send_client_telemetry(monkeypatch):
    captured = {}

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"status":"ok"}'

    def _fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["data"] = json.loads(req.data.decode("utf-8"))
        return _Resp()

    monkeypatch.setattr(support_logs.urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr(support_logs, "_support_config", lambda: {"upload_url": "https://suite-support.iamds.com/api/v1/upload"})

    res = support_logs.send_client_telemetry()
    assert res["ok"] is True
    assert captured["url"] == "https://suite-support.iamds.com/api/v1/telemetry"
    assert "client_id" in captured["data"]
    assert "version" in captured["data"]
    assert "channel" in captured["data"]


def test_send_logs_with_attachment(tmp_path, monkeypatch, capsys):
    hermes_home = tmp_path / ".hermes"
    logs_dir = hermes_home / "logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / "desktop.log").write_text("log line\n", encoding="utf-8")

    att_file = tmp_path / "screenshot.png"
    att_file.write_bytes(b"\x89PNG\r\n\x1a\nfake-image-bytes")

    monkeypatch.setattr(support_logs, "get_hermes_home", lambda: hermes_home)
    monkeypatch.setattr(support_logs, "display_hermes_home", lambda: "~/.hermes")
    monkeypatch.setattr(support_logs, "_support_config", lambda: {})
    monkeypatch.setattr(support_logs, "_capture_dump_text", lambda: "dump info\n")

    captured = {}

    class _Resp:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"job_id":"job-att","reference_id":"SUP-ATT"}'

    def _fake_urlopen(req, timeout=0):
        captured["data"] = req.data
        return _Resp()

    monkeypatch.setattr(support_logs.urllib.request, "urlopen", _fake_urlopen)

    args = _parse(["--json", "--attachment", str(att_file)])
    code = support_logs.run_send_logs(args)
    assert code == 0

    bundle_bytes = io.BytesIO(captured["data"])
    with zipfile.ZipFile(bundle_bytes) as zf:
        assert "attachments/screenshot.png" in zf.namelist()
        assert zf.read("attachments/screenshot.png") == b"\x89PNG\r\n\x1a\nfake-image-bytes"
        meta = json.loads(zf.read("metadata.json").decode("utf-8"))
        att_manifest = next(f for f in meta["files"] if f["path"] == "attachments/screenshot.png")
        assert att_manifest["content_category"] == "screenshot"
        assert att_manifest["mime_type"] == "image/png"


