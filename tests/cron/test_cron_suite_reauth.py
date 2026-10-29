"""AIS-599: cron jobs wait while their Suite environment needs a re-login."""

import pytest

import cron.scheduler as sched
from cron import jobs as cron_jobs


@pytest.fixture()
def tmp_cron_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("cron.jobs.CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", tmp_path / "cron" / "output")
    return tmp_path


@pytest.fixture()
def pending(monkeypatch):
    """Suite environments that wait for a re-login."""
    import hermes_cli.iamds_suite as suite

    waiting: set = set()
    monkeypatch.setattr(suite, "suite_background_blocked", lambda provider=None: suite.canonical_suite_provider(provider) in waiting)
    monkeypatch.setattr(sched, "load_config", lambda: {"model": {"provider": "aimds-suite-prod"}})
    return waiting


class TestBlocking:
    def test_job_on_the_active_suite_provider_waits(self, pending):
        pending.add("aimds-suite-prod")
        assert sched.suite_reauth_blocking({"id": "j"}) == "aimds-suite-prod"

    def test_job_provider_wins_over_the_config(self, pending):
        pending.add("aimds-suite-prod")
        assert sched.suite_reauth_blocking({"id": "j", "provider": "aimds-suite-staging"}) is None
        assert sched.suite_reauth_blocking({"id": "j", "provider": "iamds-litellm"}) == "aimds-suite-prod"

    def test_script_jobs_and_other_providers_keep_running(self, pending):
        pending.add("aimds-suite-prod")
        assert sched.suite_reauth_blocking({"id": "j", "no_agent": True, "script": "x.sh"}) is None
        assert sched.suite_reauth_blocking({"id": "j", "provider": "openrouter"}) is None

    def test_nothing_pending(self, pending):
        assert sched.suite_reauth_blocking({"id": "j"}) is None


class TestWaitingRun:
    def test_waiting_is_not_a_failure_and_clears_on_the_next_run(self, tmp_cron_dir):
        job = cron_jobs.create_job(prompt="check mail", schedule="every 30m", name="M365 Mail Check")
        before = cron_jobs.get_job(job["id"])["next_run_at"]
        cron_jobs.mark_job_waiting(job["id"], "reauth:aimds-suite-prod")
        waiting = cron_jobs.get_job(job["id"])
        assert waiting["last_status"] == "waiting_reauth"
        assert waiting["waiting_reason"] == "reauth:aimds-suite-prod"
        assert not waiting.get("last_error")
        assert waiting["next_run_at"] and waiting["next_run_at"] >= before
        assert waiting["enabled"] is True

        cron_jobs.mark_job_run(job["id"], True)
        done = cron_jobs.get_job(job["id"])
        assert done["last_status"] == "ok"
        assert "waiting_reason" not in done and "waiting_since" not in done

    def test_tick_skips_the_run_without_delivery(self, pending, monkeypatch):
        pending.add("aimds-suite-prod")
        job = {"id": "m365", "name": "M365 Mail Check"}
        ran, delivered, waited = [], [], []
        monkeypatch.setattr(sched, "get_due_jobs", lambda: [job])
        monkeypatch.setattr(sched, "advance_next_run", lambda *_a, **_kw: None)
        monkeypatch.setattr(sched, "run_job", lambda j: ran.append(j) or (True, "", "", None))
        monkeypatch.setattr(sched, "_deliver_result", lambda *a, **k: delivered.append(a))
        monkeypatch.setattr(sched, "mark_job_run", lambda *a, **k: pytest.fail("a skipped run is not a run"))
        monkeypatch.setattr(sched, "mark_job_waiting", lambda jid, reason: waited.append((jid, reason)))

        sched.tick(verbose=False)

        assert (ran, delivered) == ([], [])
        assert waited == [("m365", "reauth:aimds-suite-prod")]
