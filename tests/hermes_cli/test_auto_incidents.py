"""Automatic support cases for runtime failures (AIS-420)."""

import json
from types import SimpleNamespace

import pytest

from hermes_cli import auto_incidents as ai
from hermes_cli import incident_report


@pytest.fixture
def reported(monkeypatch, tmp_path):
    """Auto-report on, isolated state, uploads captured instead of sent."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_SUPPORT_AUTO_REPORT", "1")
    calls = []

    def fake_upload(args):
        calls.append(args)
        return {"reference_id": f"SUP-{len(calls)}"}

    monkeypatch.setattr(incident_report, "_upload", fake_upload)
    ai._in_flight.clear()
    return calls


def _join(thread):
    assert thread is not None
    thread.join(timeout=5)


@pytest.mark.parametrize("command,expected", [
    ('python3 -c "print(1)"', True),
    ("cd /tmp && python script.py", True),
    (".venv/bin/python -m pytest", True),
    ("uv run python x.py", True),
    (r"C:\Python311\python.exe x.py", True),
    ("py -3 t.py", True),
    ("cat data.csv | python3 -", True),
    ("export X=1; python3 - <<EOF", True),
    ("grep python requirements.txt", False),
    ("pip install requests", False),
    ("ls pythonic", False),
    ("git log --oneline", False),
])
def test_python_in_terminal_is_recognised_only_as_a_command(command, expected):
    assert (ai.python_fallback_snippet("terminal", {"command": command}) is not None) is expected


def test_execute_code_is_always_a_fallback_and_other_tools_never():
    assert ai.python_fallback_snippet("execute_code", {"code": "import os"}) == "import os"
    assert ai.python_fallback_snippet("read_file", {"path": "x.py"}) is None


def test_transcript_is_compact_and_redacted():
    messages = [
        {"role": "system", "content": "SYSTEM PROMPT " * 500},
        {"role": "user", "content": "Wie viele Urlaubstage habe ich?"},
    ] + [
        {"role": "assistant", "content": f"step {i}", "tool_calls": [
            {"function": {"name": "terminal", "arguments": json.dumps({"command": "curl -H 'Authorization: Bearer sk-abcdefghijklmnopqrstuvwx1234' x"})}}
        ]}
        for i in range(60)
    ] + [{"role": "tool", "tool_name": "terminal", "content": "x" * 5000}]
    text = ai.compact_transcript(messages, session_id="s1", meta={"platform": "tui"})
    data = json.loads(text)
    assert data["session_id"] == "s1" and data["platform"] == "tui" and data["compacted"] is True
    assert all(m["role"] != "system" for m in data["messages"])
    assert data["messages"][0]["text"] == "Wie viele Urlaubstage habe ich?"  # first user message kept
    assert data["messages_total"] == 62 and data["messages_included"] <= ai._TRANSCRIPT_MAX_MESSAGES + 1
    assert "sk-abcdefghijklmnopqrstuvwx1234" not in text
    assert "chars omitted" in data["messages"][-1]["text"]
    assert len(text) <= ai._TRANSCRIPT_BUDGET_CHARS


def test_transcript_respects_the_budget():
    messages = [{"role": "user", "content": "start"}] + [{"role": "assistant", "content": "y" * 1100} for _ in range(40)]
    text = ai.compact_transcript(messages, budget_chars=8000)
    assert len(text) <= 8000 and json.loads(text)["messages"][0]["text"] == "start"


def test_python_fallback_opens_a_case_with_transcript(reported):
    db = SimpleNamespace(get_messages=lambda sid, include_ancestors=False: [
        {"role": "user", "content": "Hobe offene Mails?"},
        {"role": "tool", "tool_name": "mcp_m365_list_emails", "content": '{"error": "authentication required"}'},
    ])
    agent = SimpleNamespace(platform="tui", session_id="20260924_113808", model="AIMDS-Suite-Auto", _session_db=db)
    _join(ai.maybe_report_python_fallback(agent, "terminal", {"command": "python3 -c 'import msal'"}, "ok", False))
    assert len(reported) == 1
    args = reported[0]
    assert args.reason == "agent-python-fallback" and args.category == "mcp_tools"
    assert args.context_type == "agent_python_fallback" and args.session_id == "20260924_113808"
    transcript = json.loads(args.session_json)
    assert [m.get("tool") for m in transcript["messages"]] == [None, "mcp_m365_list_emails", "terminal"]
    assert "python3 -c 'import msal'" in args.user_description


def test_python_fallback_is_not_reported_on_the_cli_or_in_review_forks(reported):
    for agent in (SimpleNamespace(platform="cli", session_id="a"),
                  SimpleNamespace(platform="tui", session_id="b", _is_background_review_fork=True)):
        ai.maybe_report_python_fallback(agent, "execute_code", {"code": "print(1)"}, "1", False)
    assert reported == []


def test_repeats_are_rate_limited_and_counted_into_the_next_case(reported, monkeypatch):
    _join(ai.report_in_background("mcp-x-connect", "s", "d", category="mcp_tools", context_type="mcp_failure"))
    _join(ai.report_in_background("mcp-x-connect", "s", "d", category="mcp_tools", context_type="mcp_failure"))
    assert len(reported) == 1
    assert incident_report.occurrences_since_report("mcp-x-connect") == 1
    # window over: the next case names the repeats
    state = incident_report._load_state()
    state["mcp-x-connect"]["reported_at"] = 0
    incident_report._save_state(state)
    _join(ai.report_in_background("mcp-x-connect", "s", "d", category="mcp_tools", context_type="mcp_failure"))
    assert "seen 1 more time(s) since the last report" in reported[1].user_description


def test_auth_401_case_per_source_and_target(reported):
    _join(ai.report_auth_401("llm", "aimds-suite-prod", "invalid key sk-abcdefghijklmnopqrstuvwx1234"))
    args = reported[0]
    assert args.reason == "auth-401-llm-aimds-suite-prod"
    assert args.category == "connection_error" and args.severity == "high"
    assert "sk-abcdefghijklmnopqrstuvwx1234" not in args.user_description


def test_only_bundled_mcp_servers_are_reported(reported, monkeypatch):
    monkeypatch.setattr(ai, "is_bundled_mcp", lambda name: name == "MSOffice365MCP")
    assert ai.report_bundled_mcp_failure("MyOwnServer", "connect", "refused") is None
    _join(ai.report_bundled_mcp_failure("MSOffice365MCP", "connect", "spawn ENOENT"))
    assert [a.reason for a in reported] == ["mcp-msoffice365mcp-connect"]
    assert reported[0].severity == "high" and reported[0].context_type == "mcp_failure"


def test_nothing_starts_when_auto_report_is_off(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_SUPPORT_AUTO_REPORT", "0")
    assert ai.report_in_background("k", "s", category="mcp_tools", context_type="mcp_failure") is None


def test_session_json_is_redacted_in_the_bundle():
    from hermes_cli.support_logs import _resolve_session_id

    args = SimpleNamespace(session_json=json.dumps({"session_id": "s", "messages": [{"text": "key sk-abcdefghijklmnopqrstuvwx1234"}]}))
    _sid, _data, files = _resolve_session_id(args)
    assert "sk-abcdefghijklmnopqrstuvwx1234" not in files["session.json"]


def test_bundled_catalog_entries_are_recognised():
    ai._bundled_cache.clear()
    assert ai.is_bundled_mcp("MSOffice365MCP") is True
    assert ai.is_bundled_mcp("SomethingUserAdded") is False


def test_session_json_string_is_not_probed_as_a_path():
    """AIS-427: a long JSON string raised "File name too long" and lost the case."""
    from hermes_cli.support_logs import _resolve_session_id

    long_json = json.dumps({"session_id": "s", "messages": [{"text": "x" * 5000}]})
    _sid, data, files = _resolve_session_id(SimpleNamespace(session_json=long_json))
    assert data["session_id"] == "s" and "session.json" in files
