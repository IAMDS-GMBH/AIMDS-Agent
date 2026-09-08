"""AIS-305: cron runs opt out of the background review unless configured."""

from __future__ import annotations

from tests.cron._run_job_stubs import FakeAgent, install_run_job_stubs


def test_cron_agent_defaults_to_no_background_review(tmp_path, monkeypatch):
    install_run_job_stubs(monkeypatch, tmp_path / "home", config={"cron": {}})
    from cron import scheduler

    ok, _out, _resp, err = scheduler.run_job({"id": "j1", "name": "j1", "prompt": "hello", "deliver": "local"})
    assert ok, err
    assert FakeAgent.instances[-1]._background_review_enabled is False


def test_cron_agent_can_opt_in_to_background_review(tmp_path, monkeypatch):
    install_run_job_stubs(monkeypatch, tmp_path / "home", config={"cron": {"background_review": True}})
    from cron import scheduler

    ok, _out, _resp, err = scheduler.run_job({"id": "j2", "name": "j2", "prompt": "hello", "deliver": "local"})
    assert ok, err
    assert FakeAgent.instances[-1]._background_review_enabled is True
