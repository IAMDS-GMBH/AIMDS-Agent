"""Tests for agent/prompt_caching.py — Anthropic cache control injection."""


from agent.prompt_caching import (
    _apply_cache_marker,
    apply_anthropic_cache_control,
)


MARKER = {"type": "ephemeral"}


class TestApplyCacheMarker:
    def test_tool_message_gets_top_level_marker_on_native_anthropic(self):
        """Native Anthropic path: cache_control injected top-level (adapter moves it inside tool_result)."""
        msg = {"role": "tool", "content": "result"}
        _apply_cache_marker(msg, MARKER, native_anthropic=True)
        assert msg["cache_control"] == MARKER

    def test_tool_message_skips_marker_on_openrouter(self):
        """OpenRouter path: top-level cache_control on role:tool is invalid and causes silent hang."""
        msg = {"role": "tool", "content": "result"}
        _apply_cache_marker(msg, MARKER, native_anthropic=False)
        assert "cache_control" not in msg

    def test_none_content_gets_top_level_marker(self):
        msg = {"role": "assistant", "content": None}
        _apply_cache_marker(msg, MARKER)
        assert msg["cache_control"] == MARKER

    def test_empty_string_content_gets_top_level_marker(self):
        """Empty text blocks cannot have cache_control (Anthropic rejects them)."""
        msg = {"role": "assistant", "content": ""}
        _apply_cache_marker(msg, MARKER)
        assert msg["cache_control"] == MARKER
        # Must NOT wrap into [{"type": "text", "text": "", "cache_control": ...}]
        assert msg["content"] == ""

    def test_string_content_wrapped_in_list(self):
        msg = {"role": "user", "content": "Hello"}
        _apply_cache_marker(msg, MARKER)
        assert isinstance(msg["content"], list)
        assert len(msg["content"]) == 1
        assert msg["content"][0]["type"] == "text"
        assert msg["content"][0]["text"] == "Hello"
        assert msg["content"][0]["cache_control"] == MARKER

    def test_list_content_last_item_gets_marker(self):
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "First"},
                {"type": "text", "text": "Second"},
            ],
        }
        _apply_cache_marker(msg, MARKER)
        assert "cache_control" not in msg["content"][0]
        assert msg["content"][1]["cache_control"] == MARKER

    def test_empty_list_content_no_crash(self):
        msg = {"role": "user", "content": []}
        # Should not crash on empty list
        _apply_cache_marker(msg, MARKER)


class TestApplyAnthropicCacheControl:
    def test_empty_messages(self):
        result = apply_anthropic_cache_control([])
        assert result == []

    def test_returns_deep_copy(self):
        msgs = [{"role": "user", "content": "Hello"}]
        result = apply_anthropic_cache_control(msgs)
        assert result is not msgs
        assert result[0] is not msgs[0]
        # Original should be unmodified
        assert "cache_control" not in msgs[0].get("content", "")

    def test_system_message_gets_marker(self):
        msgs = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hi"},
        ]
        result = apply_anthropic_cache_control(msgs)
        # System message should have cache_control
        sys_content = result[0]["content"]
        assert isinstance(sys_content, list)
        assert sys_content[0]["cache_control"]["type"] == "ephemeral"

    def test_last_3_non_system_get_markers(self):
        msgs = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
            {"role": "user", "content": "msg3"},
            {"role": "assistant", "content": "msg4"},
        ]
        result = apply_anthropic_cache_control(msgs)
        # System (index 0) + last 3 non-system (indices 2, 3, 4) = 4 breakpoints
        # Index 1 (msg1) should NOT have marker
        content_1 = result[1]["content"]
        if isinstance(content_1, str):
            assert True  # No marker applied (still a string)
        else:
            assert "cache_control" not in content_1[0]

    def test_no_system_message(self):
        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        result = apply_anthropic_cache_control(msgs)
        # Both should get markers (4 slots available, only 2 messages)
        assert len(result) == 2

    def test_1h_ttl(self):
        msgs = [{"role": "system", "content": "System prompt"}]
        result = apply_anthropic_cache_control(msgs, cache_ttl="1h")
        sys_content = result[0]["content"]
        assert isinstance(sys_content, list)
        assert sys_content[0]["cache_control"]["ttl"] == "1h"

    def test_max_4_breakpoints(self):
        msgs = [
            {"role": "system", "content": "System"},
        ] + [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg{i}"}
            for i in range(10)
        ]
        result = apply_anthropic_cache_control(msgs)
        # Count how many messages have cache_control
        count = 0
        for msg in result:
            content = msg.get("content")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and "cache_control" in item:
                        count += 1
            elif "cache_control" in msg:
                count += 1
        assert count <= 4

    def test_custom_max_breakpoints(self):
        msgs = [
            {"role": "system", "content": "System"},
        ] + [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg{i}"}
            for i in range(10)
        ]
        result = apply_anthropic_cache_control(msgs, max_breakpoints=3)
        count = 0
        for msg in result:
            content = msg.get("content")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and "cache_control" in item:
                        count += 1
            elif "cache_control" in msg:
                count += 1
        assert count == 3



# ---------------------------------------------------------------------------
# Prefix/message TTL split and the last-tool breakpoint (LiteLLM layout)
# ---------------------------------------------------------------------------

from agent.prompt_caching import mark_last_tool  # noqa: E402


def _conv():
    return [
        {"role": "system", "content": "SYSTEM PROMPT " * 300},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "type": "function", "function": {"name": "sql", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "1", "content": "rows"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "second"},
    ]


class TestPrefixAndMessageTtl:
    def test_system_takes_prefix_ttl_and_messages_take_message_ttl(self):
        out = apply_anthropic_cache_control(_conv(), native_anthropic=False, max_breakpoints=3, prefix_ttl="1h", message_ttl="5m")
        assert out[0]["content"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
        marked = [m for m in out[1:] if any("cache_control" in (c if isinstance(c, dict) else {}) for c in (m.get("content") or []) if isinstance(m.get("content"), list)) or "cache_control" in m]
        assert [m["role"] for m in marked] == ["assistant", "user"]  # newest two markable messages
        for m in marked:
            cc = m.get("cache_control") or m["content"][-1]["cache_control"]
            assert cc == {"type": "ephemeral"}  # 5m carries no ttl key

    def test_openai_layout_skips_tool_messages_and_marks_earlier_ones(self):
        msgs = _conv() + [
            {"role": "assistant", "content": None, "tool_calls": [{"id": "2", "type": "function", "function": {"name": "sql", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "2", "content": "rows2"},
            {"role": "tool", "tool_call_id": "3", "content": "rows3"},
        ]
        out = apply_anthropic_cache_control(msgs, native_anthropic=False, max_breakpoints=3, prefix_ttl="1h")
        assert "cache_control" not in out[-1] and "cache_control" not in out[-2]
        assert out[-3]["cache_control"] == {"type": "ephemeral"}  # assistant with tool_calls (content None → envelope)
        assert out[5]["content"][0]["cache_control"] == {"type": "ephemeral"}  # "second" user message

    def test_native_layout_may_mark_tool_messages(self):
        out = apply_anthropic_cache_control(_conv(), native_anthropic=True, max_breakpoints=4, prefix_ttl="1h")
        assert out[3]["cache_control"] == {"type": "ephemeral"}

    def test_legacy_single_ttl_argument_still_sets_both(self):
        out = apply_anthropic_cache_control(_conv(), cache_ttl="1h", max_breakpoints=2)
        assert out[0]["content"][0]["cache_control"]["ttl"] == "1h"
        assert out[-1]["content"][0]["cache_control"]["ttl"] == "1h"


class TestMarkLastTool:
    def _tools(self):
        return [
            {"type": "function", "function": {"name": "a", "parameters": {}}},
            {"type": "function", "function": {"name": "b", "parameters": {}}},
        ]

    def test_marks_a_copy_and_leaves_the_agent_list_alone(self):
        tools = self._tools()
        marked = mark_last_tool(tools, "1h")
        assert marked[-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
        assert "cache_control" not in tools[-1] and marked[0] is tools[0]

    def test_after_a_deferred_load_only_the_new_last_tool_is_marked(self):
        tools = self._tools()
        first = mark_last_tool(tools, "1h")
        tools.append({"type": "function", "function": {"name": "c", "parameters": {}}})
        second = mark_last_tool(tools, "1h")
        assert sum("cache_control" in t for t in second) == 1 and second[-1]["function"]["name"] == "c"
        assert sum("cache_control" in t for t in first) == 1

    def test_existing_marker_is_kept_and_empty_lists_pass_through(self):
        tools = [{"type": "function", "function": {"name": "a"}, "cache_control": {"type": "ephemeral"}}]
        assert mark_last_tool(tools, "1h")[-1]["cache_control"] == {"type": "ephemeral"}
        assert mark_last_tool([], "1h") == [] and mark_last_tool(None, "1h") is None


def test_full_request_layout_has_four_breakpoints_in_anthropic_order():
    """last tool (1h) → system (1h) → two newest messages (5m)."""
    tools = mark_last_tool([{"type": "function", "function": {"name": "x"}}], "1h")
    msgs = apply_anthropic_cache_control(_conv(), native_anthropic=False, max_breakpoints=3, prefix_ttl="1h", message_ttl="5m")
    markers = [tools[-1]["cache_control"], msgs[0]["content"][0]["cache_control"]]
    markers += [m.get("cache_control") or m["content"][-1]["cache_control"] for m in msgs[1:] if "cache_control" in m or (isinstance(m.get("content"), list) and "cache_control" in m["content"][-1])]
    assert len(markers) == 4
    assert [mk.get("ttl", "5m") for mk in markers] == ["1h", "1h", "5m", "5m"]


class TestSystemPromptCacheSplit:
    """AIS-279: the volatile timestamp tail must live OUTSIDE the cached
    system block — with it inside, the 58KB prefix changed every minute and
    cross-session cache_read was structurally 0."""

    SYSTEM = (
        "# SOUL\nstable guidance here\n\n"
        "## User preferences & profile\n- [profile] Language: English\n\n"
        "Current Local Time & Date: Monday, September 01, 2026 10:00:00 (CEST)\n"
        "ISO Date: 2026-09-01\n"
        "Session ID: 20260901_100000_abc123"
    )

    def test_split_is_byte_exact_and_marker_anchored(self):
        from agent.prompt_caching import split_system_for_caching

        stable, volatile = split_system_for_caching(self.SYSTEM)
        assert stable + volatile == self.SYSTEM
        assert volatile.startswith("\n\nCurrent Local Time & Date: ")
        assert "Session ID:" in volatile and "Session ID:" not in stable
        # no marker → everything stable
        assert split_system_for_caching("just guidance") == ("just guidance", "")

    def test_system_message_becomes_two_blocks_with_marker_on_stable(self):
        from agent.prompt_caching import apply_anthropic_cache_control

        messages = [
            {"role": "system", "content": self.SYSTEM},
            {"role": "user", "content": "Hallo"},
        ]
        out = apply_anthropic_cache_control(messages, prefix_ttl="1h", message_ttl="5m")
        system = out[0]["content"]
        assert isinstance(system, list) and len(system) == 2
        assert system[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
        assert "cache_control" not in system[1]
        assert system[0]["text"] + system[1]["text"] == self.SYSTEM
        # original input untouched (deep copy)
        assert isinstance(messages[0]["content"], str)

    def test_system_without_marker_keeps_single_block_behavior(self):
        from agent.prompt_caching import apply_anthropic_cache_control

        out = apply_anthropic_cache_control(
            [{"role": "system", "content": "guidance only"}, {"role": "user", "content": "hi"}],
            prefix_ttl="1h",
        )
        system = out[0]["content"]
        assert isinstance(system, list) and len(system) == 1
        assert system[0]["cache_control"]["ttl"] == "1h"
