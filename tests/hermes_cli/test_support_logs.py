from __future__ import annotations

import argparse
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

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




def test_send_logs_bundles_action_logs(tmp_path, monkeypatch, capsys):
    """AIS-303: detached actions (``hermes mcp install`` & co.) only write to
    ``logs/action-*.log``; a bundle without them cannot explain a failed
    catalog install."""
    hermes_home = tmp_path / ".hermes"
    logs_dir = hermes_home / "logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / "desktop.log").write_text("desktop line\n", encoding="utf-8")
    (logs_dir / "action-mcp-install.log").write_text(
        "=== mcp-install started ===\n  git not found\n  ✗ install failed: boom token=sk-abcdefghijklmnopqrstuv\n",
        encoding="utf-8",
    )
    (logs_dir / "action-doctor.log").write_text("doctor ok\n", encoding="utf-8")
    (logs_dir / "action-empty.log").write_text("", encoding="utf-8")
    (logs_dir / "unrelated.log").write_text("not shipped\n", encoding="utf-8")

    monkeypatch.setattr(support_logs, "get_hermes_home", lambda: hermes_home)
    monkeypatch.setattr(support_logs, "display_hermes_home", lambda: "~/.hermes")
    monkeypatch.setattr(support_logs, "_support_config", lambda: {})
    monkeypatch.setattr(support_logs, "_capture_dump_text", lambda: "dump\n")

    captured = {}

    class _Resp:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"reference_id":"SUP-1"}'

    def _fake_urlopen(req, timeout=0):
        captured["data"] = req.data
        return _Resp()

    monkeypatch.setattr(support_logs.urllib.request, "urlopen", _fake_urlopen)

    assert support_logs.run_send_logs(_parse(["--json"])) == 0
    with zipfile.ZipFile(io.BytesIO(captured["data"])) as zf:
        names = set(zf.namelist())
        assert {"logs/desktop.log", "logs/action-mcp-install.log", "logs/action-doctor.log"} <= names
        assert "logs/action-empty.log" not in names
        assert "logs/unrelated.log" not in names
        install_log = zf.read("logs/action-mcp-install.log").decode("utf-8")
        assert "install failed" in install_log
        assert "sk-abcdefghijklmnopqrstuv" not in install_log
        manifest = json.loads(zf.read("manifest.json"))
        included = {f["name"] for f in manifest["included_files"]}
        assert {"desktop.log", "action-mcp-install.log", "action-doctor.log"} <= included


def test_bundle_log_names_ship_update_logs(tmp_path):
    """AIS-331: the updater's own output belongs in the bundle.

    SUP-20260914-105316 arrived with desktop.log alone — the staged updater
    had run, relaunched the same 0.7.3 and left no trace of why. update.log,
    hermes-update.log and updater-launch.log are now part of the fixed set,
    and action-*.log files (AIS-303) stay appended after it.
    """
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "action-mcp-install.log").write_text("x\n", encoding="utf-8")

    names = support_logs._bundle_log_names(logs_dir)

    assert names[:5] == ["desktop.log", "agent.log", "errors.log", "gateway.log", "gui.log"]
    assert {"update.log", "hermes-update.log", "updater-launch.log"} <= set(names)
    assert names[-1] == "action-mcp-install.log"
    assert len(names) == len(set(names))


def _fake_upload(monkeypatch, captured):
    class _Resp:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"reference_id":"SUP-2"}'

    def _fake_urlopen(req, timeout=0):
        captured["data"] = req.data
        return _Resp()

    monkeypatch.setattr(support_logs.urllib.request, "urlopen", _fake_urlopen)


def _focused_home(tmp_path, monkeypatch):
    """AIS-344 fixture: a home whose logs carry today's restart port race."""
    hermes_home = tmp_path / ".hermes"
    logs_dir = hermes_home / "logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / "desktop.log").write_text(
        "[hermes] [boot] Resolving Hermes backend\n"
        "[hermes] ERROR: [Errno 48] address already in use 127.0.0.1:9120\n"
        "[hermes] Hermes backend exited before it became ready (1).\n"
        "[hermes] [boot] Resolving Hermes backend\n"
        "[hermes] Hermes backend is ready\n",
        encoding="utf-8",
    )
    # Timestamps relative to "now" (not a fixed past date): AIS-384's
    # session/time-window scoping in _read_last_lines applies a wall-clock
    # filter by default, so a hardcoded old date would make every line here
    # fall outside the window and vanish from the bundle.
    base = datetime.now(timezone.utc) - timedelta(minutes=5)
    (logs_dir / "agent.log").write_text(
        "".join(
            f"{(base + timedelta(seconds=i)).strftime('%Y-%m-%d %H:%M:%S')},000 INFO agent line {i}\n"
            for i in range(300)
        ),
        encoding="utf-8",
    )
    (logs_dir / "gateway.log").write_text("gateway noise token=sk-abcdefghijklmnopqrstuv\n" * 5, encoding="utf-8")
    (logs_dir / "bootstrap-installer.log").write_text("[updater] Web UI build failed — serving stale dist as fallback\n", encoding="utf-8")
    (logs_dir / "mcp-stderr.log").write_text("MCP server MSOffice365MCP failed to start: boom\n", encoding="utf-8")
    monkeypatch.setattr(support_logs, "get_hermes_home", lambda: hermes_home)
    monkeypatch.setattr(support_logs, "display_hermes_home", lambda: "~/.hermes")
    monkeypatch.setattr(support_logs, "_support_config", lambda: {})
    monkeypatch.setattr(support_logs, "_capture_dump_text", lambda: "dump\n")
    return hermes_home


def test_bundle_log_names_include_installer_and_mcp_stderr(tmp_path):
    """AIS-344: SUP-20260915-101006 arrived without the updater's own log."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    names = support_logs._bundle_log_names(logs_dir)
    assert {"bootstrap-installer.log", "mcp-stderr.log"} <= set(names)


def test_focused_bundle_ships_digest_signals_and_category_relevant_tails(tmp_path, monkeypatch, capsys):
    """AIS-344 (B2): reduce before sending — digest + metadata + dump +
    200-line tails of the logs that matter for the category; the rest stays
    home because their hits are already in the digest."""
    _focused_home(tmp_path, monkeypatch)
    captured = {}
    _fake_upload(monkeypatch, captured)

    assert support_logs.run_send_logs(_parse(["--json", "--category", "installation_update"])) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["log_scope"] == "focused"
    assert [s["id"] for s in payload["signals"]][:2] == ["boot.port_in_use", "boot.backend_exited_before_ready"]

    with zipfile.ZipFile(io.BytesIO(captured["data"])) as zf:
        names = set(zf.namelist())
        assert "incident-digest.txt" in names
        # Update category → update logs + desktop.log ship; agent/gateway do not.
        assert {"logs/desktop.log", "logs/bootstrap-installer.log"} <= names
        assert "logs/agent.log" not in names
        assert "logs/gateway.log" not in names
        digest = zf.read("incident-digest.txt").decode("utf-8")
        assert "boot.port_in_use ×1 [high]" in digest
        assert "update.web_build_failed ×1" in digest
        assert "mcp.server_start_failed ×1" in digest
        assert "sk-abcdefghijklmnopqrstuv" not in digest
        metadata = json.loads(zf.read("metadata.json"))
        details = metadata["issue_details"]
        assert details["digest_file"] == "incident-digest.txt"
        assert details["log_scope"] == "focused"
        assert details["signals"][0]["id"] == "boot.port_in_use"
        assert details["signals"][0]["count"] == 1
        assert {f["path"] for f in metadata["files"]} >= {"incident-digest.txt", "logs/desktop.log"}
        manifest = json.loads(zf.read("manifest.json"))
        roles = {f["name"]: f.get("role") for f in manifest["included_files"]}
        assert roles["incident-digest.txt"] == "digest"
        assert roles["desktop.log"] == "focused"


def test_focused_bundle_caps_relevant_tails_at_200_lines_and_mcp_category_ships_mcp_stderr(tmp_path, monkeypatch, capsys):
    _focused_home(tmp_path, monkeypatch)
    captured = {}
    _fake_upload(monkeypatch, captured)
    assert support_logs.run_send_logs(_parse(["--json", "--category", "mcp_tools"])) == 0
    capsys.readouterr()
    with zipfile.ZipFile(io.BytesIO(captured["data"])) as zf:
        names = set(zf.namelist())
        assert {"logs/agent.log", "logs/mcp-stderr.log"} <= names
        assert "logs/desktop.log" not in names
        agent_log = zf.read("logs/agent.log").decode("utf-8")
        assert agent_log.startswith("# hermes support: logs/agent.log -- scope=")
        header, _, body = agent_log.partition("\n")
        assert body.count("\n") == 200
        assert "agent line 299" in body and "agent line 50" not in body


def test_context_type_wins_over_category_for_log_selection():
    assert support_logs._relevant_log_names("other", "boot_error")[0] == "desktop.log"
    assert support_logs._relevant_log_names("mcp_tools", "") == support_logs._CATEGORY_LOGS["mcp_tools"]
    assert support_logs._relevant_log_names("unknown-category", "manual") == support_logs._DEFAULT_CATEGORY_LOGS


def test_full_logs_flag_restores_every_log_tail(tmp_path, monkeypatch, capsys):
    _focused_home(tmp_path, monkeypatch)
    captured = {}
    _fake_upload(monkeypatch, captured)
    assert support_logs.run_send_logs(_parse(["--json", "--category", "installation_update", "--full-logs"])) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["log_scope"] == "full"
    with zipfile.ZipFile(io.BytesIO(captured["data"])) as zf:
        names = set(zf.namelist())
        assert {"logs/desktop.log", "logs/agent.log", "logs/gateway.log", "logs/mcp-stderr.log", "incident-digest.txt"} <= names
        assert zf.read("logs/agent.log").decode("utf-8").count("\n") == 300
        gateway = zf.read("logs/gateway.log").decode("utf-8")
        assert "sk-abcdefghijklmnopqrstuv" not in gateway
        metadata = json.loads(zf.read("metadata.json"))
        assert metadata["issue_details"]["log_scope"] == "full"


def test_dry_run_previews_signals_and_files_without_uploading(tmp_path, monkeypatch, capsys):
    _focused_home(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(support_logs.urllib.request, "urlopen", lambda *a, **k: calls.append(a))
    out_path = tmp_path / "preview.zip"
    assert support_logs.run_send_logs(_parse(["--json", "--dry-run", "--category", "installation_update", "--output", str(out_path)])) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["dry_run"] is True
    assert calls == []
    assert payload["signals"][0]["id"] == "boot.port_in_use"
    assert any(f["path"] == "incident-digest.txt" for f in payload["files"])
    assert all("size_bytes" in f for f in payload["files"])
    assert out_path.exists() and payload["bundle_bytes"] == out_path.stat().st_size


# --------------------------------------------------------------------------
# AIS-384 / SUP-20260918-131539: precision fallback ladder in
# _read_last_lines (session-scoped tail, wall-clock fallback, plain-tail
# degrade) -- unit-level, exercising the ladder directly.
# --------------------------------------------------------------------------


def _write_log(path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_read_last_lines_plain_call_matches_old_blind_tail(tmp_path):
    """No session_id / now: byte-for-byte the pre-AIS-384 behavior."""
    path = tmp_path / "agent.log"
    _write_log(path, [f"line {i}" for i in range(50)])
    lines, scope = support_logs._read_last_lines(path, 10)
    assert scope == "tail"
    assert lines == support_logs._tail_lines(path, 10)


def test_read_last_lines_session_scoped_keeps_only_that_session(tmp_path):
    path = tmp_path / "agent.log"
    now = datetime.now(timezone.utc)
    ts = lambda i: (now - timedelta(seconds=50 - i)).strftime("%Y-%m-%d %H:%M:%S") + ",000"
    _write_log(
        path,
        [
            f"{ts(0)} INFO [sess-B] cron: tick",
            f"{ts(1)} INFO [sess-A] agent.turn_context: start",
            f"{ts(2)} ERROR [sess-A] agent.x: boom",
            "Traceback (most recent call last):",
            f"{ts(3)} INFO [sess-B] cron: tick2",
            f"{ts(4)} INFO [sess-A] agent.turn_context: end",
        ]
        * 6,  # 4 sess-A-owned lines per block x 6 = 24 >= _MIN_SESSION_LINES
    )
    lines, scope = support_logs._read_last_lines(path, 100, session_id="sess-A", now=now)
    assert scope == "session"
    joined = "".join(lines)
    assert "sess-B" not in joined
    assert "sess-A" in joined and "Traceback" in joined


def test_read_last_lines_stale_near_zero_tag_file_uses_time_window(tmp_path):
    """Direct regression test for the measured gateway.log bug: a file with
    (near-)zero session tagging whose blind tail would otherwise ship weeks
    of stale content now drops it via the wall-clock fallback tier."""
    path = tmp_path / "gateway.log"
    now = datetime.now(timezone.utc)
    stale = now - timedelta(days=30)
    lines = [f"{(stale + timedelta(seconds=i)).strftime('%Y-%m-%d %H:%M:%S')},000 INFO gateway: old {i}" for i in range(50)]
    lines += [f"{(now - timedelta(minutes=5, seconds=-i)).strftime('%Y-%m-%d %H:%M:%S')},000 INFO gateway: fresh {i}" for i in range(50)]
    _write_log(path, lines)
    kept, scope = support_logs._read_last_lines(path, 200, session_id="", now=now)
    assert scope == "time_window"
    joined = "".join(kept)
    assert "old " not in joined
    assert "fresh " in joined


def test_read_last_lines_brand_new_session_falls_back_to_time_window(tmp_path):
    path = tmp_path / "agent.log"
    now = datetime.now(timezone.utc)
    lines = [f"{(now - timedelta(seconds=60 - i)).strftime('%Y-%m-%d %H:%M:%S')},000 INFO [sess-old] agent: x{i}" for i in range(30)]
    # Only 2 lines for the brand-new session -- below _MIN_SESSION_LINES.
    lines.append(f"{now.strftime('%Y-%m-%d %H:%M:%S')},000 INFO [sess-new] agent.turn_context: start")
    lines.append(f"{now.strftime('%Y-%m-%d %H:%M:%S')},000 INFO [sess-new] agent.turn_context: end")
    _write_log(path, lines)
    kept, scope = support_logs._read_last_lines(path, 100, session_id="sess-new", now=now)
    assert scope == "time_window"
    # Falls back to the wall-clock window, not an (empty-ish) session slice --
    # every line here is recent, so the older session's lines are kept too.
    assert "sess-old" in "".join(kept)


def test_read_last_lines_untimestamped_file_degrades_to_plain_tail(tmp_path):
    path = tmp_path / "mcp-stderr.log"
    _write_log(path, ["no timestamp here"] * 5)
    lines, scope = support_logs._read_last_lines(path, 3, session_id="whatever", now=datetime.now(timezone.utc))
    assert scope == "tail"
    assert len(lines) == 3


def test_full_logs_bypasses_session_and_time_scoping(tmp_path, monkeypatch, capsys):
    """Extends test_full_logs_flag_restores_every_log_tail: confirm the ladder
    is not reachable at all under --full-logs, even with a stale, tagged log."""
    hermes_home = _focused_home(tmp_path, monkeypatch)
    stale_line = "2020-01-01 00:00:00,000 INFO [sess-ancient] agent: ancient line\n"
    (hermes_home / "logs" / "agent.log").write_text(stale_line * 5, encoding="utf-8")
    captured = {}
    _fake_upload(monkeypatch, captured)
    assert support_logs.run_send_logs(
        _parse(["--json", "--category", "installation_update", "--full-logs", "--session-id", "sess-ancient"])
    ) == 0
    capsys.readouterr()
    with zipfile.ZipFile(io.BytesIO(captured["data"])) as zf:
        agent_log = zf.read("logs/agent.log").decode("utf-8")
        assert "ancient line" in agent_log
        assert not agent_log.startswith("# hermes support:")
