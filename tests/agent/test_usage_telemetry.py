"""Agent usage counters in the client telemetry (AIS-796): chat vs. subagent
tool calls and compressions per hour — counters only, via a snapshot file both
telemetry senders read."""

from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace

from agent import usage_telemetry
from hermes_state import SessionDB


def _db(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("chat-1", source="tui")
    db.append_message("chat-1", "assistant", tool_calls=[{"id": "a", "function": {"name": "x", "arguments": "{}"}}])
    db.record_agent_run("chat-1", child_session_id="child-1", definition="apply", tool_calls=31, status="completed")
    return db


def test_snapshot_round_trip_holds_numbers_only(tmp_path):
    db = _db(tmp_path)
    path = tmp_path / "home" / "telemetry" / "agent-usage.json"
    written = usage_telemetry.write_snapshot(db, path=path)
    assert usage_telemetry.read_snapshot(path) == written
    assert written["version"] == 1 and written["tool_calls_main"] == 1 and written["tool_calls_child"] == 31
    assert written["agent_runs_by_definition"] == {"apply": 1}

    def leaves(value):
        if isinstance(value, dict):
            for key, inner in value.items():
                yield key
                yield from leaves(inner)
        else:
            yield value

    assert all(isinstance(v, (int, float, str)) for v in leaves(written))
    assert not any(isinstance(v, str) and "chat-1" in v for v in leaves(written))
    assert usage_telemetry.read_snapshot(tmp_path / "missing.json") == {}
    db.close()


def test_refresh_is_throttled_and_main_chats_only(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    db = _db(tmp_path)
    chat = SimpleNamespace(_session_db=db, _delegate_depth=0)
    assert usage_telemetry.maybe_refresh_snapshot(chat) is True
    path = usage_telemetry.snapshot_path()
    assert path.is_file()
    assert usage_telemetry.maybe_refresh_snapshot(chat) is False  # written just now
    old = time.time() - usage_telemetry.SNAPSHOT_MIN_INTERVAL_S - 5
    os.utime(path, (old, old))
    assert usage_telemetry.maybe_refresh_snapshot(chat) is True
    os.utime(path, (old, old))
    assert usage_telemetry.maybe_refresh_snapshot(SimpleNamespace(_session_db=db, _delegate_depth=1)) is False
    assert usage_telemetry.maybe_refresh_snapshot(SimpleNamespace(_session_db=None, _delegate_depth=0)) is False
    db.close()


def test_python_telemetry_sends_the_counters(tmp_path, monkeypatch):
    from hermes_cli import support_logs

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    db = _db(tmp_path)
    usage_telemetry.write_snapshot(db)
    db.close()
    captured = {}

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"{}"

    def _urlopen(req, timeout=0):
        captured["data"] = json.loads(req.data.decode("utf-8"))
        return _Resp()

    monkeypatch.setattr(support_logs.urllib.request, "urlopen", _urlopen)
    monkeypatch.setattr(support_logs, "_support_config", lambda: {"upload_url": "https://support.example.test/api/v1/upload"})
    assert support_logs.send_client_telemetry()["ok"] is True
    usage = captured["data"]["agent_usage"]
    assert usage["tool_calls_child"] == 31 and usage["agent_runs"] == 1
