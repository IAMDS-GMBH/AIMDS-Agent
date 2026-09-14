"""AIS-332 / SUP-20260914-063903: a cron run whose API retries all failed with
connection errors is failed right away instead of idling until the
inactivity limit (975 s in the reported morning brief).

``cron.scheduler._wait_for_cron_result`` is the supervision loop ``run_job``
uses; it is exercised directly with a fake agent that reports the activity
tracker state the conversation loop leaves behind after its last retry.
"""

from __future__ import annotations

import concurrent.futures
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cron.scheduler import _cron_api_recovery_limit, _wait_for_cron_result  # noqa: E402


class _StuckAgent:
    """Runs "forever"; the activity tracker says what the loop last did."""

    def __init__(self, desc: str, idle: float = 5.0):
        self._desc = desc
        self._idle = idle
        self.interrupted = None

    def get_activity_summary(self):
        return {
            "seconds_since_activity": self._idle,
            "last_activity_desc": self._desc,
            "current_tool": None,
            "api_call_count": 1,
            "max_iterations": 90,
        }

    def interrupt(self, msg):
        self.interrupted = msg

    def run_conversation(self, prompt):
        time.sleep(5)
        return {"final_response": "late", "messages": []}


def _supervise(agent, **kwargs):
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(agent.run_conversation, "prompt")
    try:
        return _wait_for_cron_result(agent, future, poll_interval=0.05, **kwargs)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def test_exhausted_api_retries_fail_before_inactivity_limit():
    agent = _StuckAgent("API error recovery (attempt 3/3)", idle=2.0)
    result, reason = _supervise(agent, inactivity_limit=600.0, api_recovery_limit=1.0)
    assert result is None
    assert reason == "provider_unreachable"


def test_retries_still_in_progress_are_not_a_failure():
    """Attempt 1/3 means the loop is still retrying — give it the full limit."""
    agent = _StuckAgent("API error recovery (attempt 1/3)", idle=2.0)
    result, reason = _supervise(agent, inactivity_limit=0.3, api_recovery_limit=1.0)
    assert result is None
    assert reason == "inactivity"


def test_other_idle_activity_uses_inactivity_limit():
    agent = _StuckAgent("tool_call", idle=2.0)
    result, reason = _supervise(agent, inactivity_limit=0.3, api_recovery_limit=1.0)
    assert reason == "inactivity"


def test_finished_run_returns_result():
    class _Quick(_StuckAgent):
        def run_conversation(self, prompt):
            return {"final_response": "ok", "messages": []}

    agent = _Quick("API error recovery (attempt 3/3)", idle=99.0)
    result, reason = _supervise(agent, inactivity_limit=600.0, api_recovery_limit=1.0)
    assert reason is None
    assert result["final_response"] == "ok"


def test_api_recovery_limit_env(monkeypatch):
    monkeypatch.delenv("HERMES_CRON_API_RECOVERY_TIMEOUT", raising=False)
    assert _cron_api_recovery_limit() == 120.0
    monkeypatch.setenv("HERMES_CRON_API_RECOVERY_TIMEOUT", "45")
    assert _cron_api_recovery_limit() == 45.0
    monkeypatch.setenv("HERMES_CRON_API_RECOVERY_TIMEOUT", "0")
    assert _cron_api_recovery_limit() is None
    monkeypatch.setenv("HERMES_CRON_API_RECOVERY_TIMEOUT", "nope")
    assert _cron_api_recovery_limit() == 120.0
