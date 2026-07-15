from __future__ import annotations

from types import SimpleNamespace

from agent.memory_dual_write import (
    build_local_mirror_payload,
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

