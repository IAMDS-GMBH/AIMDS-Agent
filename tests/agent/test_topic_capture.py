from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from agent.topic_capture import capture_durable_topic


def test_capture_durable_topic_queues_confirmation_for_ambiguous_confidence(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    with patch("agent.topic_capture._resolve_thresholds", return_value=(0.8, 0.4)), \
         patch("agent.topic_capture.append_open_question_entry") as oq_mock:
        result = capture_durable_topic(
            source="test",
            title="Possible preference",
            content="User might want shorter updates.",
            confidence=0.6,
            ask_on_ambiguous=True,
        )

    assert result.decision == "queued_for_confirmation"
    assert result.local_saved is False
    assert result.mcp_saved is False
    oq_mock.assert_called_once()


def test_capture_durable_topic_saves_local_and_mcp(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    with patch("agent.topic_capture._resolve_thresholds", return_value=(0.7, 0.4)), \
         patch("agent.topic_capture._try_mcp_memory_save", return_value=(True, "saved_via_memory_save")):
        result = capture_durable_topic(
            source="test",
            title="Team convention",
            content="Use scripts/run_tests.sh for CI-parity test execution.",
            confidence=0.9,
            tags=["testing"],
            memory_type="notes",
            scope="project",
        )

    assert result.decision == "saved"
    assert result.local_saved is True
    assert result.mcp_saved is True
    mirror_store = Path(tmp_path) / "memories" / "MCP_MIRROR_MEMORY.jsonl"
    assert mirror_store.exists()
