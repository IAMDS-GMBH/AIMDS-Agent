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


def test_send_logs_requires_upload_url(monkeypatch, capsys):
    monkeypatch.setattr(support_logs, "_support_config", lambda: {"api_key": "abc"})
    args = _parse(["--json"])

    code = support_logs.run_send_logs(args)
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "support.upload_url" in payload["error"]


def test_send_logs_requires_api_key(monkeypatch, capsys):
    monkeypatch.setattr(support_logs, "_support_config", lambda: {"upload_url": "https://support.example/upload"})
    args = _parse(["--json"])

    code = support_logs.run_send_logs(args)
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "support.api_key" in payload["error"]


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
