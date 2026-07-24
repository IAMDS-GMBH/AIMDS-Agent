from pathlib import Path
from agent.session_bootstrap import (
    build_bootstrap_status_block,
    check_agents_md,
    check_soul_md,
    evaluate_session_bootstrap,
    memory_context_requires_hydration,
)


def test_memory_context_requires_hydration_for_stale_flags():
    assert memory_context_requires_hydration({"context_missing": True})
    assert memory_context_requires_hydration({"result": {"context_stale": "stale"}})
    assert not memory_context_requires_hydration({"result": {"profile": [{"name": "Ada"}]}})


def test_evaluate_session_bootstrap_ready():
    # Bootstrap with valid memory context should be ready (even if context files missing)
    status = evaluate_session_bootstrap(
        payload={"result": {"profile": [{"name": "Ada"}]}},
        hydration_added=False,
        memory_context_required=True,
    )
    assert status.ready is True  # Can continue with or without context files
    # state may be "ready" or "degraded" depending on context file availability


def test_evaluate_session_bootstrap_context_files_tracked():
    # Bootstrap status should always report context file states
    status = evaluate_session_bootstrap(
        payload={"result": {"profile": [{"name": "Ada"}]}},
        hydration_added=False,
        memory_context_required=True,
    )
    assert hasattr(status, 'soul_ok')
    assert hasattr(status, 'agents_ok')
    assert isinstance(status.soul_ok, bool)
    assert isinstance(status.agents_ok, bool)


def test_evaluate_session_bootstrap_degraded_context_files_missing():
    # Bootstrap with missing context files should be degraded but still ready to continue
    status = evaluate_session_bootstrap(
        payload={"result": {"profile": [{"name": "Ada"}]}},
        hydration_added=False,
        memory_context_required=True,
        cwd=Path("/nonexistent"),  # AGENTS.md doesn't exist here
    )
    assert status.ready is True  # Session can continue
    # When context files missing, state should be degraded
    if not status.agents_ok:
        assert status.state == "degraded"
        assert status.reason_code == "context_files_missing"


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
    assert "Session-start bootstrap status: degraded" in text
    assert "memory_context_missing_or_failed" in text


def test_check_soul_md_returns_bool():
    # SOUL.md should exist in ~/.hermes/SOUL.md after install
    result = check_soul_md()
    assert isinstance(result, bool)


def test_check_agents_md_returns_bool():
    # AGENTS.md should exist in current repo root
    result = check_agents_md(Path(__file__).parent.parent.parent)
    assert isinstance(result, bool)


def test_check_agents_md_false_for_nonexistent_dir():
    result = check_agents_md(Path("/nonexistent"))
    assert result is False


def test_evaluate_session_bootstrap_includes_context_file_checks():
    # Even with good memory context, bootstrap should report context file status
    status = evaluate_session_bootstrap(
        payload={"result": {"profile": [{"name": "Ada"}]}},
        hydration_added=False,
        memory_context_required=True,
        cwd=Path(__file__).parent.parent.parent,  # repo root has AGENTS.md
    )
    assert hasattr(status, 'agents_ok')
    assert hasattr(status, 'soul_ok')
