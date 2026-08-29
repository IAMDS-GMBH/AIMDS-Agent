"""A mid-turn steer must not rewrite history before the cache breakpoints."""

from agent.steer_injection import inject_pre_api_steer


def _history():
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do it"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "type": "function", "function": {"name": "sql", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "1", "content": "rows"},
    ]


def test_steer_becomes_its_own_user_message_and_leaves_the_tool_result_alone():
    msgs = _history()
    before = [dict(m) for m in msgs]
    assert inject_pre_api_steer(msgs, "prefer March only") is True
    assert msgs[:4] == before  # nothing before the new message changed
    assert msgs[-1]["role"] == "user" and "prefer March only" in msgs[-1]["content"]
    assert msgs[-1]["content"].startswith("<") or msgs[-1]["content"].startswith("[")  # the taught wrapper


def test_without_a_tool_result_the_steer_stays_pending():
    msgs = _history()[:2]
    assert inject_pre_api_steer(msgs, "x") is False and len(msgs) == 2
    assert inject_pre_api_steer([], "x") is False
    assert inject_pre_api_steer(_history(), "") is False
