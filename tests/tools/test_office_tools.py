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


def test_action_normalization_variants() -> None:
    assert office_tools._normalize_action("Read Metadata") == "read_metadata"
    assert office_tools._normalize_action("read-metadata") == "read_metadata"
    assert office_tools._normalize_action("  READ  ") == "read"


def test_word_action_alias_convert_pdf_requires_path() -> None:
    result = _parse(office_tools.office_word_tool({"action": "convert-pdf"}))
    assert "error" in result
    assert "convert requires path" in result["error"]


def test_word_generate_report_requires_output_path() -> None:
    result = _parse(office_tools.office_word_tool({"action": "generate_report"}))
    assert result["error"] == "output_path is required for generate_exec_report"


def test_excel_generate_workbook_requires_output_path() -> None:
    result = _parse(office_tools.office_excel_tool({"action": "generate_workbook"}))
    assert result["error"] == "output_path is required for generate_kpi_workbook"


def test_powerpoint_generate_deck_requires_output_file() -> None:
    result = _parse(office_tools.office_powerpoint_tool({"action": "generate_deck"}))
    assert result["error"] == "output_file is required for generate_review_deck"


def test_powerpoint_validate_requires_path() -> None:
    result = _parse(office_tools.office_powerpoint_tool({"action": "validate"}))
    assert result["error"] == "output_file or path is required for validate_deck"


def test_office_word_schema_requires_from_markdown_paths() -> None:
    schema = office_tools.OFFICE_WORD_SCHEMA["parameters"]
    branch = next(
        item
        for item in schema["oneOf"]
        if item.get("properties", {}).get("action", {}).get("const") == "from_markdown"
    )
    assert set(branch["required"]) == {"action", "source_path", "output_path"}


def test_office_powerpoint_schema_requires_add_slide_inputs() -> None:
    schema = office_tools.OFFICE_POWERPOINT_SCHEMA["parameters"]
    branch = next(
        item
        for item in schema["oneOf"]
        if item.get("properties", {}).get("action", {}).get("const") == "add_slide"
    )
    assert set(branch["required"]) == {"action", "unpacked_dir", "source"}
