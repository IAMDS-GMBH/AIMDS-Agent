"""Tests for /project-path handling in tui_gateway."""

from __future__ import annotations

import importlib
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    yield home


@pytest.fixture()
def server(hermes_home):
    with patch.dict(
        "sys.modules",
        {
            "hermes_cli.env_loader": MagicMock(),
            "hermes_cli.banner": MagicMock(),
        },
    ):
        mod = importlib.import_module("tui_gateway.server")
        yield mod
        mod._sessions.clear()
        mod._pending.clear()
        mod._answers.clear()


@pytest.fixture()
def session(server, tmp_path):
    sid = "sid-project-path"
    session_key = "tui-project-path-1"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    s = {
        "session_key": session_key,
        "history": [],
        "history_lock": threading.Lock(),
        "history_version": 0,
        "running": False,
        "attached_images": [],
        "cols": 120,
        "cwd": str(workspace),
    }
    server._sessions[sid] = s
    return sid, s


def _call(server, method, **params):
    return server._methods[method](1, params)


def test_project_path_show_is_disabled_by_default(server, session):
    sid, _ = session
    r = _call(server, "command.dispatch", name="project-path", arg="show", session_id=sid)
    assert r["result"]["type"] == "exec"
    assert "disabled" in r["result"]["output"].lower()


def test_project_path_set_show_and_clear(server, session):
    sid, s = session
    set_resp = _call(
        server,
        "command.dispatch",
        name="project-path",
        arg="set scripts",
        session_id=sid,
    )
    assert set_resp["result"]["type"] == "exec"
    assert "enabled" in set_resp["result"]["output"].lower()

    show_resp = _call(server, "command.dispatch", name="project-path", arg="show", session_id=sid)
    assert "scripts/" in show_resp["result"]["output"]

    from tools.project_output_routing import get_project_output_subfolder

    info = get_project_output_subfolder(s["cwd"])
    assert info["subfolder"] == "scripts"

    clear_resp = _call(server, "command.dispatch", name="project-path", arg="clear", session_id=sid)
    assert "disabled" in clear_resp["result"]["output"].lower()
    info_after = get_project_output_subfolder(s["cwd"])
    assert info_after["subfolder"] is None


def test_project_path_set_rejects_invalid_subfolder(server, session):
    sid, _ = session
    r = _call(
        server,
        "command.dispatch",
        name="project-path",
        arg="set ../escape",
        session_id=sid,
    )
    assert "error" in r
    assert r["error"]["code"] == 4004


def test_slash_exec_rejects_project_path_routes_to_command_dispatch(server, session):
    sid, _ = session
    r = _call(server, "slash.exec", command="project-path show", session_id=sid)
    assert "error" in r
    assert r["error"]["code"] == 4018
    assert "command.dispatch" in r["error"]["message"]


def test_pending_input_commands_includes_project_path(server):
    assert "project-path" in server._PENDING_INPUT_COMMANDS
