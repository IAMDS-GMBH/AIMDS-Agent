"""AIS-428: the desktop restarts its backend at night only when nothing runs."""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client():
    from hermes_cli.web_server import _SESSION_HEADER_NAME, _SESSION_TOKEN, app

    c = TestClient(app)
    c.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return c


def test_idle_when_nothing_runs(client, monkeypatch):
    from cron import scheduler
    from tui_gateway import server as tui

    monkeypatch.setattr(tui, "_sessions", {"a": {"running": False}})
    monkeypatch.setattr(scheduler, "_running_job_ids", set())
    assert client.get("/api/backend/idle").json() == {"idle": True, "running_sessions": [], "running_jobs": []}


def test_busy_while_a_turn_or_a_cron_job_runs(client, monkeypatch):
    from cron import scheduler
    from tui_gateway import server as tui

    monkeypatch.setattr(tui, "_sessions", {"a": {"running": True}, "b": {}})
    monkeypatch.setattr(scheduler, "_running_job_ids", {"aimds-morning-brief"})
    body = client.get("/api/backend/idle").json()
    assert body == {"idle": False, "running_sessions": ["a"], "running_jobs": ["aimds-morning-brief"]}


def test_requires_the_session_token():
    from hermes_cli.web_server import app

    assert TestClient(app).get("/api/backend/idle").status_code == 401
