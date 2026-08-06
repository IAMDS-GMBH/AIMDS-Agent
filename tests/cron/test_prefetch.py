import json
from pathlib import Path
from cron.jobs import (
    prefetch_cron_job_context,
    get_prefetched_cron_context,
    prepare_next_day_prefetch,
    CRON_CACHE_DIR,
)


def test_prefetch_cron_job_context(tmp_path, monkeypatch):
    test_cache = tmp_path / "cache"
    monkeypatch.setattr("cron.jobs.CRON_CACHE_DIR", test_cache)

    job = {
        "id": "testjob123",
        "name": "Test Job",
        "skills": ["test-skill"],
        "workdir": str(tmp_path),
        "next_run": "2026-08-07T08:00:00",
    }

    res = prefetch_cron_job_context(job)
    assert res["job_id"] == "testjob123"
    assert res["prefer_client_tools"] is True
    assert (test_cache / "testjob123.json").exists()

    fetched = get_prefetched_cron_context("testjob123")
    assert fetched is not None
    assert fetched["job_id"] == "testjob123"


def test_prepare_next_day_prefetch(tmp_path, monkeypatch):
    test_cache = tmp_path / "cache"
    monkeypatch.setattr("cron.jobs.CRON_CACHE_DIR", test_cache)

    jobs = [
        {
            "id": "job1",
            "name": "Job 1",
            "next_run": "2026-08-07T10:00:00",
        },
        {
            "id": "job2",
            "name": "Job 2",
            "next_run": "2026-08-07T14:00:00",
        }
    ]

    monkeypatch.setattr("cron.jobs.load_jobs", lambda: jobs)

    res = prepare_next_day_prefetch()
    assert res["count"] == 2
    assert "job1" in res["prefetched_jobs"]
    assert "job2" in res["prefetched_jobs"]
