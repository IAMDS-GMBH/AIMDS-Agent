from __future__ import annotations

import json

from agent.memory import ManagedMemoryStore


def _read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_capture_mode_auto_writes_records(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    store = ManagedMemoryStore(enabled=True, capture_mode="auto")

    store.record_write(
        action="add",
        target="user",
        content="User prefers concise answers.",
        metadata={"scope": "user", "session_id": "s1"},
    )

    rows = _read_jsonl(tmp_path / "memories" / "MANAGED_MEMORY.jsonl")
    assert len(rows) == 1
    assert rows[0]["action"] == "add"
    assert rows[0]["scope"] == "user"
    assert rows[0]["target"] == "user"


def test_capture_mode_suggest_stages_pending(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    store = ManagedMemoryStore(enabled=True, capture_mode="suggest")

    store.record_write(
        action="replace",
        target="memory",
        content="Use scripts/run_tests.sh for test execution.",
        old_text="run pytest",
        metadata={"scope": "project"},
    )

    pending = _read_jsonl(tmp_path / "memories" / "MANAGED_MEMORY.pending.jsonl")
    committed = _read_jsonl(tmp_path / "memories" / "MANAGED_MEMORY.jsonl")
    assert len(pending) == 1
    assert committed == []
    assert pending[0]["action"] == "replace"


def test_hybrid_recall_filters_by_scope_and_query(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    store = ManagedMemoryStore(
        enabled=True,
        capture_mode="auto",
        retrieval_enabled=True,
        retrieval_top_k=3,
        retrieval_scopes=["user", "project"],
    )
    store.record_write(
        action="add",
        target="user",
        content="User likes short bullet points for summaries.",
        metadata={"scope": "user"},
    )
    store.record_write(
        action="add",
        target="memory",
        content="Use scripts/run_tests.sh for parity with CI.",
        metadata={"scope": "project"},
    )
    store.record_write(
        action="add",
        target="memory",
        content="Temporary scratchpad note unrelated to testing.",
        metadata={"scope": "session"},
    )

    text = store.build_recall_context("How should I run tests?")
    assert "Managed memory recall" in text
    assert "run_tests.sh" in text
    assert "scratchpad" not in text

