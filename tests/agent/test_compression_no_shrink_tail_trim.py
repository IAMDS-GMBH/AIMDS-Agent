"""AIS-786: mid-turn compaction must make room or stop retrying.

Chat 20261030_112808_6330c0 (-> bb0660 -> 7b8ad1): compaction ran before every
call with "messages=17->17", "19->19" while the real prompt stayed at ~87k
above the 80k budget, 20-30 s each. The protected tail held a 50,788-character
terminal output and a 19,488-character file read, so no summary of the middle
could reach the budget. Two deterministic fixes:

* a tail over its own budget gets its big tool results cut to a preview plus
  the path of the saved full text (no LLM summary of tool results);
* a pass that did not shrink the context is not repeated before the prompt
  grew by a margin.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.context_compressor import (
    LATEST_TOOL_RESULT_PREVIEW_CHARS,
    SUITE_PROMPT_BUDGET_TOKENS,
    ContextCompressor,
)
from agent.model_metadata import estimate_messages_tokens_rough
from tools.tool_result_storage import PERSISTED_OUTPUT_TAG

TERMINAL_CHARS = 50_788
READ_FILE_CHARS = 19_488


def _suite_compressor() -> ContextCompressor:
    with patch("agent.context_compressor.get_model_context_length", return_value=200_000):
        return ContextCompressor(
            model="AIMDS-Suite-Auto",
            provider="aimds-suite-prod",
            threshold_percent=0.75,
            quiet_mode=True,
        )


def _call(call_id: str, name: str, args: str) -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}


def _lines(prefix: str, total_chars: int) -> str:
    line = f"{prefix} " + "x" * 70 + "\n"
    return (line * (total_chars // len(line) + 1))[:total_chars]


def _chat_6330c0() -> list[dict]:
    """17 messages shaped like the changelog chat right before the loop."""
    messages = [
        {"role": "system", "content": "You are the user's assistant."},
        {"role": "user", "content": "jetzt einen changelog für die LBBW-Suite und der version 0.4 erstellen"},
        {"role": "assistant", "content": "[CONTEXT COMPACTION] earlier: the user wants a changelog; tags listed."},
    ]
    for i in range(4):
        messages += [
            {"role": "assistant", "content": "", "tool_calls": [_call(f"t{i}", "terminal", f'{{"command": "git log {i}"}}')]},
            {"role": "tool", "tool_call_id": f"t{i}", "content": _lines(f"commit {i}", 900)},
        ]
    messages += [
        {"role": "assistant", "content": "Ich lese die Commits.", "tool_calls": [_call("big", "terminal", '{"command": "git log --stat v0.3..v0.4"}')]},
        {"role": "tool", "tool_call_id": "big", "content": _lines("stat", TERMINAL_CHARS)},
        {"role": "assistant", "content": "", "tool_calls": [_call("rf", "read_file", '{"path": "CHANGELOG.md"}')]},
        {"role": "tool", "tool_call_id": "rf", "content": _lines("changelog", READ_FILE_CHARS)},
    ]
    assert len(messages) == 15
    messages += [
        {"role": "assistant", "content": "", "tool_calls": [_call("t9", "terminal", '{"command": "wc -l CHANGELOG.md"}')]},
        {"role": "tool", "tool_call_id": "t9", "content": "412 CHANGELOG.md"},
    ]
    assert len(messages) == 17
    return messages


@pytest.fixture
def saved(tmp_path: Path):
    files: dict[str, Path] = {}

    def persist(content: str, call_id: str) -> str:
        path = tmp_path / f"{call_id}.txt"
        path.write_text(content, encoding="utf-8")
        files[call_id] = path
        return str(path)

    return files, persist


def _tool(messages: list[dict], call_id: str) -> dict:
    return next(m for m in messages if m.get("role") == "tool" and m.get("tool_call_id") == call_id)


def test_big_results_in_the_protected_tail_become_preview_plus_saved_file(saved, caplog):
    files, persist = saved
    c = _suite_compressor()
    c.tool_result_persister = persist
    messages = _chat_6330c0()

    with patch.object(c, "_generate_summary", return_value="Summary of the earlier turns."), caplog.at_level(
        logging.INFO, logger="agent.context_compressor"
    ):
        out = c.compress(messages, current_tokens=85_015)

    big = _tool(out, "big")["content"]
    assert PERSISTED_OUTPUT_TAG in big
    assert len(big) < 3_000
    assert files["big"].read_text(encoding="utf-8") == _tool(messages, "big")["content"]
    assert str(files["big"]) in big and "read_file" in big

    # The tail now fits; the file read the model works on stays readable.
    tail_trim = [r for r in caplog.records if "compression tail trim" in r.getMessage()]
    assert len(tail_trim) == 1
    assert len(_tool(out, "rf")["content"]) >= LATEST_TOOL_RESULT_PREVIEW_CHARS

    # A real shrink: the pass does not arm the no-shrink guard.
    assert estimate_messages_tokens_rough(out) < estimate_messages_tokens_rough(messages) * 0.5
    assert c._no_shrink_trigger_tokens == 0
    # The input transcript is not mutated.
    assert len(_tool(messages, "big")["content"]) == TERMINAL_CHARS


def test_the_newest_result_keeps_a_longer_preview_and_its_path(saved):
    files, persist = saved
    c = _suite_compressor()
    c.tool_result_persister = persist
    # The file read is the newest result and the largest one in the tail.
    messages = _chat_6330c0()[:-2]
    messages[-1] = {**messages[-1], "content": _lines("changelog", 60_000)}

    with patch.object(c, "_generate_summary", return_value="Summary."):
        out = c.compress(messages, current_tokens=95_317)

    newest = _tool(out, "rf")["content"]
    assert PERSISTED_OUTPUT_TAG in newest
    assert LATEST_TOOL_RESULT_PREVIEW_CHARS - 100 <= len(newest.split("Preview (first ", 1)[1]) < 6_000
    assert str(files["rf"]) in newest
    assert files["rf"].read_text(encoding="utf-8") == messages[-1]["content"]


def test_without_a_saved_file_the_preview_says_the_rest_is_gone():
    c = _suite_compressor()
    c.tool_result_persister = lambda content, call_id: None
    messages = _chat_6330c0()

    with patch.object(c, "_generate_summary", return_value="Summary."):
        out = c.compress(messages, current_tokens=85_015)

    big = _tool(out, "big")["content"]
    assert "could not be saved" in big
    assert len(big) < 3_000


def test_a_tail_within_its_budget_is_left_alone(saved):
    files, persist = saved
    c = _suite_compressor()
    c.tool_result_persister = persist
    messages = [
        {**m, "content": _lines("stat", 9_000)} if m.get("tool_call_id") == "big" else m for m in _chat_6330c0()
    ]

    with patch.object(c, "_generate_summary", return_value="Summary."):
        c.compress(messages, current_tokens=82_000)

    assert files == {}


def test_a_pass_that_did_not_shrink_waits_until_the_prompt_grew(caplog):
    c = _suite_compressor()
    # Tail within budget, and the summary is as long as what it replaces — the
    # shape of "messages=17->17": a full summary round trip that cut nothing.
    middle = [
        {"role": "assistant", "content": "a" * 400},
        {"role": "user", "content": "b" * 400},
    ]
    tail = [
        {"role": "assistant", "content": "", "tool_calls": [_call(f"x{i}", "terminal", "{}")]} if i % 2 == 0
        else {"role": "tool", "tool_call_id": f"x{i - 1}", "content": _lines(f"out{i}", 6_000)}
        for i in range(6)
    ]
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}, *middle, *tail]

    def as_long_as_the_turns(turns, **_kwargs):
        return "s" * sum(len(str(m.get("content") or "")) + 40 for m in turns)

    with patch.object(c, "_generate_summary", side_effect=as_long_as_the_turns), caplog.at_level(
        logging.INFO, logger="agent.context_compressor"
    ):
        c.compress(messages, current_tokens=86_950)

    assert c._no_shrink_trigger_tokens == 86_950
    assert any("did not shrink the context" in r.getMessage() for r in caplog.records)

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="agent.context_compressor"):
        # Same content plus a little more: no new pass before the next call.
        assert c.should_compress(86_950 + 2_000) is False
    assert any("compression skipped: the last pass did not shrink" in r.getMessage() for r in caplog.records)

    # Grown by the margin (10 % of the 80k budget): a pass has something new to cut.
    assert c.should_compress(86_950 + 8_000) is True
    # Below the budget nothing changes.
    assert c.should_compress(SUITE_PROMPT_BUDGET_TOKENS - 1) is False

    c.on_session_reset()
    assert c._no_shrink_trigger_tokens == 0
    assert c.should_compress(86_950 + 2_000) is True


def test_an_effective_pass_clears_the_guard():
    c = _suite_compressor()
    c._no_shrink_trigger_tokens = 85_015
    messages = _chat_6330c0()
    c.tool_result_persister = lambda content, call_id: None

    with patch.object(c, "_generate_summary", return_value="Summary."):
        c.compress(messages, current_tokens=95_317)

    assert c._no_shrink_trigger_tokens == 0


def test_the_agent_binds_a_saver_for_trimmed_results(tmp_path: Path):
    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
        from run_agent import AIAgent

        agent = AIAgent(
            api_key="test-key",
            base_url="https://openrouter.ai/api/v1",
            model="test/model",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
    c = _suite_compressor()
    agent.context_compressor = c

    with patch("tools.terminal_tool.get_active_env", return_value=None), patch(
        "tempfile.gettempdir", return_value=str(tmp_path)
    ), patch.object(
        c, "_generate_summary", return_value="Summary."
    ):
        out, _ = agent._compress_context(_chat_6330c0(), "sys", approx_tokens=85_015)

    big = _tool(out, "big")["content"]
    saved_file = tmp_path / "hermes-results" / "big.txt"
    assert saved_file.exists() and str(saved_file) in big
    assert len(saved_file.read_text(encoding="utf-8")) == TERMINAL_CHARS
