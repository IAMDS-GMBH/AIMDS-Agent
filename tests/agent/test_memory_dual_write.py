from __future__ import annotations

from types import SimpleNamespace

from agent.memory_dual_write import (
    annotate_tool_result_with_local_mirror,
    build_structured_mirror_record,
    build_local_mirror_payload,
    read_structured_mirror_records,
    is_mcp_memory_save_tool,
    tool_result_indicates_success,
    mirror_mcp_memory_save_to_local,
)


def test_is_mcp_memory_save_tool_matches_suffix():
    assert is_mcp_memory_save_tool("memory_save")
    assert is_mcp_memory_save_tool("mcp_IAMDS_mcp_memory_memory_save")
    assert not is_mcp_memory_save_tool("memory_context")


def test_tool_result_success_parsing():
    assert tool_result_indicates_success('{"success": true}')
    assert not tool_result_indicates_success('{"success": false, "error": "x"}')
    assert tool_result_indicates_success("ok")  # non-json treated as success


def test_build_local_payload_maps_profile_to_user():
    target, content = build_local_mirror_payload(
        {
            "title": "Communication preference",
            "type": "profile",
            "content": "User prefers concise bullet points.",
            "tags": ["preferences"],
        }
    )
    assert target == "user"
    assert "Type: profile" in content
    assert "concise bullet points" in content


def test_mirror_mcp_save_to_local_calls_memory_tool(monkeypatch):
    called = {}

    def fake_memory_tool(**kwargs):
        called.update(kwargs)
        return '{"success": true}'

    monkeypatch.setattr("tools.memory_tool.memory_tool", fake_memory_tool)

    agent = SimpleNamespace(
        _memory_store=object(),
        _build_memory_write_metadata=lambda **kwargs: {"session_id": "s1", **kwargs},
    )
    mirror_mcp_memory_save_to_local(
        agent,
        "mcp_IAMDS_mcp_memory_memory_save",
        {"title": "Lang", "type": "profile", "content": "User prefers English."},
        '{"success": true}',
        effective_task_id="t1",
        tool_call_id="c1",
    )
    assert called["action"] == "add"
    assert called["target"] == "user"
    assert "User prefers English." in called["content"]
    assert called["metadata"]["write_origin"] == "mcp_mirror"


def test_mirror_mcp_save_writes_structured_record(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    called = {}

    def fake_memory_tool(**kwargs):
        called.update(kwargs)
        return '{"success": true}'

    monkeypatch.setattr("tools.memory_tool.memory_tool", fake_memory_tool)

    agent = SimpleNamespace(
        _memory_store=object(),
        _build_memory_write_metadata=lambda **kwargs: {"session_id": "s-structured", **kwargs},
    )
    mirror_mcp_memory_save_to_local(
        agent,
        "mcp_IAMDS_mcp_memory_memory_save",
        {
            "title": "Spanish preference",
            "type": "profile",
            "content": "User prefers Spanish responses.",
            "tags": ["language", "preferences"],
            "hints": {"communication": {"language": "es"}},
        },
        '{"success": true}',
        effective_task_id="task-1",
        tool_call_id="call-1",
    )

    rows = read_structured_mirror_records(limit=10, memory_type="profile")
    assert len(rows) == 1
    row = rows[0]
    assert row["type"] == "profile"
    assert row["title"] == "Spanish preference"
    assert row["scope"] == "user"
    assert row["target"] == "user"
    assert row["source_tool"] == "mcp_IAMDS_mcp_memory_memory_save"
    assert row["write_origin"] == "mcp_mirror"
    assert row["tool_call_id"] == "call-1"
    assert row["task_id"] == "task-1"
    assert "preferences" in row["tags"]
    assert row["hints"]["communication"]["language"] == "es"


def test_mirror_mcp_save_failure_does_not_write_structured_record(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    monkeypatch.setattr("tools.memory_tool.memory_tool", lambda **_kwargs: '{"success": true}')

    agent = SimpleNamespace(
        _memory_store=object(),
        _build_memory_write_metadata=lambda **kwargs: {"session_id": "s-fail", **kwargs},
    )
    mirror_mcp_memory_save_to_local(
        agent,
        "mcp_IAMDS_mcp_memory_memory_save",
        {"title": "Should not persist", "type": "profile", "content": "x"},
        '{"success": false, "error": "denied"}',
        effective_task_id="task-fail",
        tool_call_id="call-fail",
    )

    rows = read_structured_mirror_records(limit=10)
    assert rows == []


def test_build_structured_record_scope_mapping():
    user_row = build_structured_mirror_record(
        tool_args={"type": "person", "title": "Contact", "content": "Jane"},
        write_meta={"scope": "session"},
        tool_name="memory_save",
        effective_task_id="",
    )
    project_row = build_structured_mirror_record(
        tool_args={"type": "project", "title": "Repo", "content": "Hermes agent"},
        write_meta={"scope": "session"},
        tool_name="memory_save",
        effective_task_id="",
    )

    assert user_row is not None
    assert user_row["scope"] == "user"
    assert user_row["target"] == "user"
    assert project_row is not None
    assert project_row["scope"] == "project"
    assert project_row["target"] == "memory"


def test_annotate_tool_result_with_local_mirror_for_json_dict():
    result = annotate_tool_result_with_local_mirror('{"success": true}')
    assert isinstance(result, str)
    assert '"local_mirror": true' in result
