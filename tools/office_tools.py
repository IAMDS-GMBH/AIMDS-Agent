#!/usr/bin/env python3
"""Office productivity tool wrappers (Word/Excel/PowerPoint skill scripts)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from tools.registry import registry, tool_error, tool_result


_REPO_ROOT = Path(__file__).resolve().parent.parent

_WORD_SCRIPT_ROOT = _REPO_ROOT / "skills" / "productivity" / "word" / "scripts"
_EXCEL_SCRIPT_ROOT = _REPO_ROOT / "skills" / "productivity" / "excel" / "scripts"
_PPT_SCRIPT_ROOT = _REPO_ROOT / "skills" / "productivity" / "powerpoint" / "scripts"


def _check_office_tools() -> bool:
    return (
        (_WORD_SCRIPT_ROOT / "read.py").exists()
        and (_EXCEL_SCRIPT_ROOT / "read.py").exists()
        and (_PPT_SCRIPT_ROOT / "add_slide.py").exists()
    )


def _safe_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _run_script(script_path: Path, args: list[str]) -> str:
    if not script_path.exists():
        return tool_error(f"Missing script: {script_path}")

    cmd = [sys.executable, str(script_path), *args]
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
        )
    except Exception as exc:
        return tool_error(f"Failed to execute {script_path.name}: {exc}")

    ok = completed.returncode == 0
    return tool_result(
        success=ok,
        command=cmd,
        returncode=completed.returncode,
        stdout=(completed.stdout or "").strip(),
        stderr=(completed.stderr or "").strip(),
    )


def office_word_tool(args: dict, **kwargs) -> str:
    action = _safe_str(args.get("action"))
    if not action:
        return tool_error("action is required")

    path = _safe_str(args.get("path"))
    output_path = _safe_str(args.get("output_path"))
    text = _safe_str(args.get("text"))
    find_text = _safe_str(args.get("find_text"))
    replace_text = _safe_str(args.get("replace_text"))
    source_path = _safe_str(args.get("source_path"))
    source_paths = args.get("source_paths") or []
    fmt = (_safe_str(args.get("format")) or "").lower()

    if action in {"read_text", "read_markdown", "read_tables", "read_metadata", "read_styles"}:
        if not path:
            return tool_error("path is required for read actions")
        action_args = [path]
        flag_map = {
            "read_markdown": "--markdown",
            "read_tables": "--tables",
            "read_metadata": "--metadata",
            "read_styles": "--styles",
        }
        flag = flag_map.get(action)
        if flag:
            action_args.append(flag)
        return _run_script(_WORD_SCRIPT_ROOT / "read.py", action_args)

    if action == "from_markdown":
        if not source_path or not output_path:
            return tool_error("source_path and output_path are required for from_markdown")
        return _run_script(_WORD_SCRIPT_ROOT / "write.py", ["--from-md", source_path, output_path])

    if action == "find_replace":
        if not path or find_text is None or replace_text is None:
            return tool_error("path, find_text, and replace_text are required for find_replace")
        return _run_script(_WORD_SCRIPT_ROOT / "write.py", ["--replace", find_text, replace_text, path])

    if action == "append_paragraph":
        if not path or text is None:
            return tool_error("path and text are required for append_paragraph")
        return _run_script(_WORD_SCRIPT_ROOT / "write.py", ["--append", text, path])

    if action == "merge":
        if not source_paths or not isinstance(source_paths, list) or not output_path:
            return tool_error("source_paths (array) and output_path are required for merge")
        paths = [str(p) for p in source_paths if _safe_str(p)]
        if len(paths) < 2:
            return tool_error("merge requires at least two source_paths")
        return _run_script(_WORD_SCRIPT_ROOT / "write.py", ["--merge", *paths, "--out", output_path])

    if action == "convert":
        if not path or fmt not in {"pdf", "md", "markdown", "html", "htm"}:
            return tool_error("convert requires path and format in [pdf, md, markdown, html, htm]")
        return _run_script(_WORD_SCRIPT_ROOT / "convert.py", [path, "--to", fmt])

    return tool_error(
        "Unsupported word action",
        supported_actions=[
            "read_text",
            "read_markdown",
            "read_tables",
            "read_metadata",
            "read_styles",
            "from_markdown",
            "find_replace",
            "append_paragraph",
            "merge",
            "convert",
        ],
    )


def office_excel_tool(args: dict, **kwargs) -> str:
    action = _safe_str(args.get("action"))
    if not action:
        return tool_error("action is required")

    path = _safe_str(args.get("path"))
    output_path = _safe_str(args.get("output_path"))
    sheet = _safe_str(args.get("sheet"))
    cell = _safe_str(args.get("cell"))
    value = args.get("value")
    row_csv = _safe_str(args.get("row_csv"))
    source_path = _safe_str(args.get("source_path"))

    if action in {"read_sheet", "list_sheets", "stats", "read_cell", "metadata"}:
        if not path:
            return tool_error("path is required for read actions")
        cmd = [path]
        if action == "list_sheets":
            cmd.append("--sheets")
        elif action == "stats":
            cmd.append("--stats")
        elif action == "read_cell":
            if not cell:
                return tool_error("cell is required for read_cell")
            cmd.extend(["--cell", cell])
        elif action == "metadata":
            cmd.append("--metadata")
        if sheet:
            cmd.extend(["--sheet", sheet])
        return _run_script(_EXCEL_SCRIPT_ROOT / "read.py", cmd)

    if action == "from_csv":
        if not source_path or not output_path:
            return tool_error("source_path and output_path are required for from_csv")
        return _run_script(_EXCEL_SCRIPT_ROOT / "write.py", ["--from-csv", source_path, output_path])

    if action == "set_cell":
        if not path or not cell or value is None:
            return tool_error("path, cell, and value are required for set_cell")
        cmd = ["--set-cell", cell, str(value), path]
        if sheet:
            cmd.extend(["--sheet", sheet])
        return _run_script(_EXCEL_SCRIPT_ROOT / "write.py", cmd)

    if action == "append_row":
        if not path or not row_csv:
            return tool_error("path and row_csv are required for append_row")
        cmd = ["--append-row", row_csv, path]
        if sheet:
            cmd.extend(["--sheet", sheet])
        return _run_script(_EXCEL_SCRIPT_ROOT / "write.py", cmd)

    if action == "to_csv":
        if not path:
            return tool_error("path is required for to_csv")
        cmd = ["--to-csv", path]
        if output_path:
            cmd.append(output_path)
        if sheet:
            cmd.extend(["--sheet", sheet])
        return _run_script(_EXCEL_SCRIPT_ROOT / "write.py", cmd)

    if action == "to_pdf":
        if not path:
            return tool_error("path is required for to_pdf")
        return _run_script(_EXCEL_SCRIPT_ROOT / "write.py", ["--to-pdf", path])

    return tool_error(
        "Unsupported excel action",
        supported_actions=[
            "read_sheet",
            "list_sheets",
            "stats",
            "read_cell",
            "metadata",
            "from_csv",
            "set_cell",
            "append_row",
            "to_csv",
            "to_pdf",
        ],
    )


def office_powerpoint_tool(args: dict, **kwargs) -> str:
    action = _safe_str(args.get("action"))
    if not action:
        return tool_error("action is required")

    unpacked_dir = _safe_str(args.get("unpacked_dir"))
    source = _safe_str(args.get("source"))
    input_directory = _safe_str(args.get("input_directory"))
    output_file = _safe_str(args.get("output_file"))
    original_file = _safe_str(args.get("original_file"))
    validate = args.get("validate")

    if action == "add_slide":
        if not unpacked_dir or not source:
            return tool_error("unpacked_dir and source are required for add_slide")
        return _run_script(_PPT_SCRIPT_ROOT / "add_slide.py", [unpacked_dir, source])

    if action == "clean":
        if not unpacked_dir:
            return tool_error("unpacked_dir is required for clean")
        return _run_script(_PPT_SCRIPT_ROOT / "clean.py", [unpacked_dir])

    if action == "pack":
        if not input_directory or not output_file:
            return tool_error("input_directory and output_file are required for pack")
        cmd = [input_directory, output_file]
        if original_file:
            cmd.extend(["--original", original_file])
        if isinstance(validate, bool):
            cmd.extend(["--validate", "true" if validate else "false"])
        return _run_script(_PPT_SCRIPT_ROOT / "office" / "pack.py", cmd)

    return tool_error(
        "Unsupported powerpoint action",
        supported_actions=["add_slide", "clean", "pack"],
    )


OFFICE_WORD_SCHEMA = {
    "name": "office_word",
    "description": "Operate on Word files via the bundled productivity skill scripts.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string"},
            "path": {"type": "string"},
            "source_path": {"type": "string"},
            "source_paths": {"type": "array", "items": {"type": "string"}},
            "output_path": {"type": "string"},
            "format": {"type": "string"},
            "text": {"type": "string"},
            "find_text": {"type": "string"},
            "replace_text": {"type": "string"},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}

OFFICE_EXCEL_SCHEMA = {
    "name": "office_excel",
    "description": "Operate on Excel/CSV files via the bundled productivity skill scripts.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string"},
            "path": {"type": "string"},
            "source_path": {"type": "string"},
            "output_path": {"type": "string"},
            "sheet": {"type": "string"},
            "cell": {"type": "string"},
            "value": {"type": ["string", "number", "boolean"]},
            "row_csv": {"type": "string"},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}

OFFICE_POWERPOINT_SCHEMA = {
    "name": "office_powerpoint",
    "description": "Operate on unpacked PPTX directories via the bundled powerpoint scripts.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string"},
            "unpacked_dir": {"type": "string"},
            "source": {"type": "string"},
            "input_directory": {"type": "string"},
            "output_file": {"type": "string"},
            "original_file": {"type": "string"},
            "validate": {"type": "boolean"},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}


registry.register(
    name="office_word",
    toolset="office",
    schema=OFFICE_WORD_SCHEMA,
    handler=office_word_tool,
    check_fn=_check_office_tools,
    emoji="📝",
)

registry.register(
    name="office_excel",
    toolset="office",
    schema=OFFICE_EXCEL_SCHEMA,
    handler=office_excel_tool,
    check_fn=_check_office_tools,
    emoji="📊",
)

registry.register(
    name="office_powerpoint",
    toolset="office",
    schema=OFFICE_POWERPOINT_SCHEMA,
    handler=office_powerpoint_tool,
    check_fn=_check_office_tools,
    emoji="📽️",
)

