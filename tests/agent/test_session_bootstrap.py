from agent.session_bootstrap import (
    build_bootstrap_status_block,
    evaluate_session_bootstrap,
    memory_context_requires_hydration,
)


def test_memory_context_requires_hydration_for_stale_flags():
    assert memory_context_requires_hydration({"context_missing": True})
    assert memory_context_requires_hydration({"result": {"context_stale": "stale"}})
    assert not memory_context_requires_hydration({"result": {"profile": [{"name": "Ada"}]}})


def test_evaluate_session_bootstrap_ready():
    status = evaluate_session_bootstrap(
        payload={"result": {"profile": [{"name": "Ada"}]}},
        hydration_added=False,
        memory_context_required=True,
    )
    assert status.ready is True
    assert status.state == "ready"
    assert status.reason_code == "ok"


def test_evaluate_session_bootstrap_degraded_for_missing_memory_context():
    status = evaluate_session_bootstrap(
        payload={"error": "boom"},
        hydration_added=False,
        memory_context_required=True,
    )
    assert status.ready is False
    assert status.state == "degraded"
    assert status.reason_code == "memory_context_missing_or_failed"


def test_build_bootstrap_status_block_contains_reason_code():
    status = evaluate_session_bootstrap(
        payload={"error": "boom"},
        hydration_added=False,
        memory_context_required=True,
    )
    text = build_bootstrap_status_block(status)
    assert "Session-start bootstrap status:" in text
    assert "reason_code: memory_context_missing_or_failed" in text
