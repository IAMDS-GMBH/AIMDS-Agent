from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import office_tools


def _parse(result: str) -> dict:
    return json.loads(result)


def test_office_word_requires_action() -> None:
    result = _parse(office_tools.office_word_tool({}))
    assert "error" in result


def test_office_excel_requires_path_for_read_actions() -> None:
    result = _parse(office_tools.office_excel_tool({"action": "read_sheet"}))
    assert "error" in result


def test_office_powerpoint_requires_parameters() -> None:
    result = _parse(office_tools.office_powerpoint_tool({"action": "add_slide"}))
    assert "error" in result


def test_run_script_reports_missing_script(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.py"
    result = _parse(office_tools._run_script(missing, []))
    assert result["error"].startswith("Missing script:")


def test_check_office_tools_true_for_repo_layout() -> None:
    assert office_tools._check_office_tools() is True
