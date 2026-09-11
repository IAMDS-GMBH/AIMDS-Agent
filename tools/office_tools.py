#!/usr/bin/env python3
"""Office productivity tools (Word / Excel / PowerPoint).

Thin, hardened wrappers around the deterministic skill scripts in
``skills/productivity/{word,excel,powerpoint}/scripts``. Every action runs the
script in a subprocess with a timeout, resolves relative paths against the
workspace, refuses writes to sensitive system paths and cleans up its own
temp files. Content is always supplied by the caller — there are no canned
"report generators" here (AIS-139).
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from tools.registry import registry, tool_error, tool_result


_REPO_ROOT = Path(__file__).resolve().parent.parent

_WORD_SCRIPT_ROOT = _REPO_ROOT / "skills" / "productivity" / "word" / "scripts"
_EXCEL_SCRIPT_ROOT = _REPO_ROOT / "skills" / "productivity" / "excel" / "scripts"
_PPT_SCRIPT_ROOT = _REPO_ROOT / "skills" / "productivity" / "powerpoint" / "scripts"

_DEFAULT_TIMEOUT_SECONDS = 120
_MAX_TIMEOUT_SECONDS = 900
_DEFAULT_MAX_ROWS = 200
_MAX_ROWS_CAP = 5000

# Import names of the Python libraries the scripts need at runtime. Declared
# in pyproject.toml's ``office`` extra; ``_check_office_tools`` gates the
# toolset on them so a venv without the libs hides the tools instead of
# failing on first call.
_REQUIRED_MODULES = ("docx", "openpyxl", "pptx")

_SENSITIVE_PREFIXES = ("/etc/", "/boot/", "/usr/lib/systemd/", "/private/etc/")


def _check_office_tools() -> bool:
    scripts_present = (
        (_WORD_SCRIPT_ROOT / "read.py").exists()
        and (_EXCEL_SCRIPT_ROOT / "read.py").exists()
        and (_PPT_SCRIPT_ROOT / "deck.py").exists()
    )
    if not scripts_present:
        return False
    try:
        return all(importlib.util.find_spec(mod) is not None for mod in _REQUIRED_MODULES)
    except (ImportError, ValueError):
        return False


def missing_office_dependencies() -> list[str]:
    """Return the import names from the ``office`` extra that are unavailable."""
    missing = []
    for mod in _REQUIRED_MODULES:
        try:
            if importlib.util.find_spec(mod) is None:
                missing.append(mod)
        except (ImportError, ValueError):
            missing.append(mod)
    return missing


# --------------------------------------------------------------------------- arg helpers

def _safe_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_int(value: Any, default: int, *, lo: int | None = None, hi: int | None = None) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    if lo is not None:
        result = max(lo, result)
    if hi is not None:
        result = min(hi, result)
    return result


def _normalize_action(action: str | None) -> str:
    if not action:
        return ""
    return action.strip().lower().replace("-", "_").replace(" ", "_")


def _script_timeout() -> int:
    raw = os.environ.get("HERMES_OFFICE_TIMEOUT", "").strip()
    return _safe_int(raw, _DEFAULT_TIMEOUT_SECONDS, lo=5, hi=_MAX_TIMEOUT_SECONDS)


# --------------------------------------------------------------------------- paths

def _workspace_root() -> Path:
    raw = (os.environ.get("TERMINAL_CWD") or "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        if candidate.is_absolute() and candidate.is_dir():
            return candidate
    return Path.cwd()


def _resolve_workspace_path(path_value: str) -> str:
    p = Path(path_value).expanduser()
    if p.is_absolute():
        return str(p.resolve())
    return str((_workspace_root() / p).resolve())


def _write_guard(path_value: str) -> str | None:
    """Return an error message when *path_value* must not be written to.

    Delegates to the file tool's sensitive-path guard (system dirs, Hermes
    config) when importable and falls back to a local prefix check.
    """
    try:
        from tools.file_tools import _check_sensitive_path

        return _check_sensitive_path(path_value)
    except Exception:
        resolved = _resolve_workspace_path(path_value)
        for prefix in _SENSITIVE_PREFIXES:
            if resolved.startswith(prefix):
                return f"Refusing to write to sensitive system path: {path_value}"
        return None


def _resolve_for_write(path_value: str) -> tuple[str | None, str | None]:
    """Resolve a write target; returns ``(resolved_path, error)``."""
    resolved = _resolve_workspace_path(path_value)
    err = _write_guard(resolved)
    if err:
        return None, err
    return resolved, None


def _resolve_for_read(path_value: str, *, must_exist: bool = True) -> tuple[str | None, str | None]:
    resolved = _resolve_workspace_path(path_value)
    if must_exist and not Path(resolved).exists():
        return None, f"File not found: {path_value} (resolved to {resolved})"
    return resolved, None


class _TempFiles:
    """Collects temp files created for one tool call and removes them afterwards."""

    def __init__(self) -> None:
        self._dir: str | None = None

    def write_text(self, text: str, suffix: str) -> str:
        if self._dir is None:
            self._dir = tempfile.mkdtemp(prefix="hermes-office-")
        target = Path(self._dir) / f"input{suffix}"
        target.write_text(text, encoding="utf-8")
        return str(target)

    def cleanup(self) -> None:
        if self._dir:
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = None


_MARKDOWN_DATA_PREFIX = "data:text/markdown,"


def _materialize_markdown_source(source_path: str, temps: _TempFiles) -> tuple[str | None, str | None]:
    if source_path.startswith(_MARKDOWN_DATA_PREFIX):
        markdown_text = unquote(source_path[len(_MARKDOWN_DATA_PREFIX):])
        return temps.write_text(markdown_text, ".md"), None
    return _resolve_for_read(source_path)


def _markdown_to_slide_sections(markdown: str) -> list[tuple[str, list[str]]]:
    sections: list[tuple[str, list[str]]] = []
    current_title: str | None = None
    current_bullets: list[str] = []

    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            if current_title and current_bullets:
                sections.append((current_title, current_bullets[:8]))
            current_title = line.lstrip("#").strip() or "Section"
            current_bullets = []
            continue
        if line.startswith(("- ", "* ")):
            current_bullets.append(line[2:].strip())
        elif re.match(r"^\d+\.\s+", line):
            current_bullets.append(re.sub(r"^\d+\.\s+", "", line))
        elif current_title:
            current_bullets.append(line)

    if current_title and current_bullets:
        sections.append((current_title, current_bullets[:8]))

    if sections:
        return sections[:30]

    plain_lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    if not plain_lines:
        return []
    return [("Summary", plain_lines[:10])]


# --------------------------------------------------------------------------- subprocess

def _run_script(script_path: Path, args: list[str], *, timeout: int | None = None) -> str:
    if not script_path.exists():
        return tool_error(f"Missing script: {script_path}")

    missing = missing_office_dependencies()
    if missing:
        return tool_error(
            "Office dependencies missing: " + ", ".join(missing),
            hint="Install with: uv pip install -e '.[office]' (or run `hermes update`).",
        )

    timeout = timeout or _script_timeout()
    cmd = [sys.executable, str(script_path), *args]
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return tool_error(
            f"{script_path.name} timed out after {timeout}s",
            hint="Large files or a hanging LibreOffice/pandoc process. Raise HERMES_OFFICE_TIMEOUT if needed.",
        )
    except Exception as exc:
        return tool_error(f"Failed to execute {script_path.name}: {exc}")

    ok = completed.returncode == 0
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    payload: dict[str, Any] = {
        "success": ok,
        "script": script_path.name,
        "returncode": completed.returncode,
        "stdout": stdout,
    }
    if stderr:
        payload["stderr"] = stderr[-4000:]
    if not ok:
        payload["error"] = _summarize_failure(stderr or stdout or f"{script_path.name} failed")
    return tool_result(payload)


def _summarize_failure(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return "script failed"
    last = lines[-1].strip()
    if "ModuleNotFoundError" in last or "No module named" in last:
        return f"{last} — install the office extra: uv pip install -e '.[office]'"
    return last[:500]


# --------------------------------------------------------------------------- Word

_WORD_ACTIONS = [
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
]

_WORD_ALIASES = {
    "read": "read_text",
    "read_doc": "read_text",
    "read_docx": "read_text",
    "markdown": "read_markdown",
    "read_md": "read_markdown",
    "tables": "read_tables",
    "metadata": "read_metadata",
    "styles": "read_styles",
    "from_md": "from_markdown",
    "create": "from_markdown",
    "write": "from_markdown",
    "replace": "find_replace",
    "append": "append_paragraph",
    "merge_docs": "merge",
    "convert_pdf": "convert",
    "convert_md": "convert",
    "convert_html": "convert",
    "to_pdf": "convert",
}


def office_word_tool(args: dict, **kwargs) -> str:
    action = _WORD_ALIASES.get(_normalize_action(_safe_str(args.get("action"))), _normalize_action(_safe_str(args.get("action"))))
    if not action:
        return tool_error("action is required", supported_actions=_WORD_ACTIONS)

    path = _safe_str(args.get("path"))
    output_path = _safe_str(args.get("output_path"))
    text = _safe_str(args.get("text"))
    find_text = args.get("find_text")
    replace_text = args.get("replace_text")
    source_path = _safe_str(args.get("source_path"))
    source_paths = args.get("source_paths") or []
    fmt = (_safe_str(args.get("format")) or "").lower()

    if action in {"read_text", "read_markdown", "read_tables", "read_metadata", "read_styles"}:
        if not path:
            return tool_error("path is required for read actions")
        resolved, err = _resolve_for_read(path)
        if err:
            return tool_error(err)
        flag_map = {
            "read_markdown": "--markdown",
            "read_tables": "--tables",
            "read_metadata": "--metadata",
            "read_styles": "--styles",
        }
        script_args = [resolved]
        if action in flag_map:
            script_args.append(flag_map[action])
        return _run_script(_WORD_SCRIPT_ROOT / "read.py", script_args)

    if action == "from_markdown":
        if not (source_path or text) or not output_path:
            return tool_error("output_path plus source_path (or inline text) are required for from_markdown")
        temps = _TempFiles()
        try:
            if source_path:
                resolved_source, err = _materialize_markdown_source(source_path, temps)
            else:
                resolved_source, err = temps.write_text(text or "", ".md"), None
            if err:
                return tool_error(err)
            resolved_output, err = _resolve_for_write(output_path)
            if err:
                return tool_error(err)
            return _run_script(_WORD_SCRIPT_ROOT / "write.py", ["--from-md", resolved_source, resolved_output])
        finally:
            temps.cleanup()

    if action == "find_replace":
        if not path or find_text is None or replace_text is None:
            return tool_error("path, find_text, and replace_text are required for find_replace")
        if not str(find_text):
            return tool_error("find_text must not be empty")
        resolved, err = _resolve_for_read(path)
        if err:
            return tool_error(err)
        _, err = _resolve_for_write(resolved)
        if err:
            return tool_error(err)
        return _run_script(_WORD_SCRIPT_ROOT / "write.py", ["--replace", str(find_text), str(replace_text), resolved])

    if action == "append_paragraph":
        if not path or text is None:
            return tool_error("path and text are required for append_paragraph")
        resolved, err = _resolve_for_read(path)
        if err:
            return tool_error(err)
        _, err = _resolve_for_write(resolved)
        if err:
            return tool_error(err)
        return _run_script(_WORD_SCRIPT_ROOT / "write.py", ["--append", text, resolved])

    if action == "merge":
        if not isinstance(source_paths, list) or not source_paths or not output_path:
            return tool_error("source_paths (array) and output_path are required for merge")
        resolved_sources = []
        for candidate in source_paths:
            candidate = _safe_str(candidate)
            if not candidate:
                continue
            resolved, err = _resolve_for_read(candidate)
            if err:
                return tool_error(err)
            resolved_sources.append(resolved)
        if len(resolved_sources) < 2:
            return tool_error("merge requires at least two existing source_paths")
        resolved_output, err = _resolve_for_write(output_path)
        if err:
            return tool_error(err)
        return _run_script(_WORD_SCRIPT_ROOT / "write.py", ["--merge", *resolved_sources, "--out", resolved_output])

    if action == "convert":
        if not path or fmt not in {"pdf", "md", "markdown", "html", "htm"}:
            return tool_error("convert requires path and format in [pdf, md, markdown, html, htm]")
        resolved, err = _resolve_for_read(path)
        if err:
            return tool_error(err)
        _, err = _resolve_for_write(str(Path(resolved).with_suffix(".pdf")))
        if err:
            return tool_error(err)
        return _run_script(_WORD_SCRIPT_ROOT / "convert.py", [resolved, "--to", fmt])

    return tool_error("Unsupported word action", supported_actions=_WORD_ACTIONS)


# --------------------------------------------------------------------------- Excel

_EXCEL_ACTIONS = [
    "read_sheet",
    "list_sheets",
    "stats",
    "read_cell",
    "metadata",
    "create",
    "from_csv",
    "set_cell",
    "set_cells",
    "append_row",
    "format_cells",
    "to_csv",
    "to_pdf",
]

_EXCEL_ALIASES = {
    "read": "read_sheet",
    "read_range": "read_sheet",
    "range": "read_sheet",
    "list": "list_sheets",
    "sheets": "list_sheets",
    "statistics": "stats",
    "describe": "stats",
    "cell": "read_cell",
    "meta": "metadata",
    "new": "create",
    "new_workbook": "create",
    "create_workbook": "create",
    "csv_to_xlsx": "from_csv",
    "fromcsv": "from_csv",
    "set": "set_cell",
    "set_batch": "set_cells",
    "write_cells": "set_cells",
    "append": "append_row",
    "format": "format_cells",
    "style": "format_cells",
    "export_csv": "to_csv",
    "export_pdf": "to_pdf",
    "pdf": "to_pdf",
}


def office_excel_tool(args: dict, **kwargs) -> str:
    raw_action = _normalize_action(_safe_str(args.get("action")))
    action = _EXCEL_ALIASES.get(raw_action, raw_action)
    if not action:
        return tool_error("action is required", supported_actions=_EXCEL_ACTIONS)

    path = _safe_str(args.get("path"))
    output_path = _safe_str(args.get("output_path"))
    sheet = _safe_str(args.get("sheet"))
    cell = _safe_str(args.get("cell"))
    cell_range = _safe_str(args.get("range"))
    value = args.get("value")
    cells = args.get("cells")
    row_csv = _safe_str(args.get("row_csv"))
    values = args.get("values")
    style = args.get("style")
    source_path = _safe_str(args.get("source_path"))
    sheets = args.get("sheets")
    max_rows = _safe_int(args.get("max_rows"), _DEFAULT_MAX_ROWS, lo=1, hi=_MAX_ROWS_CAP)

    def _sheet_args() -> list[str]:
        return ["--sheet", sheet] if sheet else []

    if action in {"read_sheet", "list_sheets", "stats", "read_cell", "metadata"}:
        if not path:
            return tool_error("path is required for read actions")
        resolved, err = _resolve_for_read(path)
        if err:
            return tool_error(err)
        cmd = [resolved]
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
        elif cell_range:
            cmd.extend(["--range", cell_range])
        else:
            cmd.extend(["--max-rows", str(max_rows)])
        cmd.extend(_sheet_args())
        return _run_script(_EXCEL_SCRIPT_ROOT / "read.py", cmd)

    if action == "create":
        if not output_path:
            return tool_error("output_path is required for create")
        resolved_output, err = _resolve_for_write(output_path)
        if err:
            return tool_error(err)
        cmd = ["--new", resolved_output]
        if isinstance(sheets, list) and sheets:
            cmd.extend(["--sheets", ",".join(str(s) for s in sheets if _safe_str(s))])
        elif isinstance(sheets, str) and sheets.strip():
            cmd.extend(["--sheets", sheets])
        return _run_script(_EXCEL_SCRIPT_ROOT / "write.py", cmd)

    if action == "from_csv":
        if not source_path or not output_path:
            return tool_error("source_path and output_path are required for from_csv")
        resolved_source, err = _resolve_for_read(source_path)
        if err:
            return tool_error(err)
        resolved_output, err = _resolve_for_write(output_path)
        if err:
            return tool_error(err)
        return _run_script(_EXCEL_SCRIPT_ROOT / "write.py", ["--from-csv", resolved_source, resolved_output])

    # Everything below edits an existing workbook in place.
    if action in {"set_cell", "set_cells", "append_row", "format_cells", "to_csv", "to_pdf"}:
        if not path:
            return tool_error(f"path is required for {action}")
        resolved, err = _resolve_for_read(path)
        if err:
            return tool_error(err)
        if action != "to_csv":
            _, err = _resolve_for_write(resolved)
            if err:
                return tool_error(err)
    else:
        return tool_error("Unsupported excel action", supported_actions=_EXCEL_ACTIONS)

    if action == "set_cell":
        if not cell or value is None:
            return tool_error("cell and value are required for set_cell")
        payload = json.dumps({cell: value})
        return _run_script(_EXCEL_SCRIPT_ROOT / "write.py", ["--set-cells", payload, resolved, *_sheet_args()])

    if action == "set_cells":
        if not cells or not isinstance(cells, (dict, list)):
            return tool_error("cells is required for set_cells: an object {\"A1\": value, ...} or a list of [cell, value] pairs")
        try:
            payload = json.dumps(cells)
        except (TypeError, ValueError) as exc:
            return tool_error(f"cells is not JSON-serializable: {exc}")
        return _run_script(_EXCEL_SCRIPT_ROOT / "write.py", ["--set-cells", payload, resolved, *_sheet_args()])

    if action == "append_row":
        if isinstance(values, list) and values:
            return _run_script(
                _EXCEL_SCRIPT_ROOT / "write.py",
                ["--append-json", json.dumps(values), resolved, *_sheet_args()],
            )
        if not row_csv:
            return tool_error("append_row needs values (array) or row_csv (quoted CSV line)")
        return _run_script(_EXCEL_SCRIPT_ROOT / "write.py", ["--append-row", row_csv, resolved, *_sheet_args()])

    if action == "format_cells":
        spec: dict[str, Any] = dict(style) if isinstance(style, dict) else {}
        if cell_range:
            spec["range"] = cell_range
        elif cell and "range" not in spec:
            spec["range"] = cell
        if not spec.get("range"):
            return tool_error("format_cells requires range (e.g. A1:C1) plus style options")
        return _run_script(_EXCEL_SCRIPT_ROOT / "write.py", ["--format", json.dumps(spec), resolved, *_sheet_args()])

    if action == "to_csv":
        cmd = ["--to-csv", resolved]
        if output_path:
            resolved_output, err = _resolve_for_write(output_path)
            if err:
                return tool_error(err)
            cmd.append(resolved_output)
        cmd.extend(_sheet_args())
        return _run_script(_EXCEL_SCRIPT_ROOT / "write.py", cmd)

    if action == "to_pdf":
        return _run_script(_EXCEL_SCRIPT_ROOT / "write.py", ["--to-pdf", resolved])

    return tool_error("Unsupported excel action", supported_actions=_EXCEL_ACTIONS)


# --------------------------------------------------------------------------- PowerPoint

_PPT_ACTIONS = [
    "read_text",
    "list_layouts",
    "create",
    "add_slide",
    "delete_slide",
    "find_replace",
    "to_pdf",
]

_PPT_ALIASES = {
    "read": "read_text",
    "read_deck": "read_text",
    "extract": "read_text",
    "layouts": "list_layouts",
    "list": "list_layouts",
    "new": "create",
    "create_deck": "create",
    "generate": "create",
    "generate_deck": "create",
    "add": "add_slide",
    "append_slide": "add_slide",
    "insert_slide": "add_slide",
    "remove_slide": "delete_slide",
    "delete": "delete_slide",
    "replace": "find_replace",
    "pdf": "to_pdf",
    "export_pdf": "to_pdf",
    "convert": "to_pdf",
}


def _slides_from_args(args: dict) -> tuple[list[dict] | None, str | None]:
    """Build the slide list for ``create`` from slides[], text (markdown) or source_path."""
    slides = args.get("slides")
    if isinstance(slides, list) and slides:
        normalized: list[dict] = []
        for entry in slides:
            if isinstance(entry, str):
                normalized.append({"title": entry})
            elif isinstance(entry, dict):
                normalized.append(entry)
            else:
                return None, f"Unsupported slide entry: {entry!r}"
        return normalized, None

    markdown = _safe_str(args.get("text"))
    source_path = _safe_str(args.get("source_path"))
    if source_path:
        resolved, err = _resolve_for_read(source_path)
        if err:
            return None, err
        try:
            markdown = Path(resolved).read_text(encoding="utf-8")
        except OSError as exc:
            return None, f"Failed to read source_path: {exc}"
    if markdown:
        return [{"title": title, "bullets": bullets} for title, bullets in _markdown_to_slide_sections(markdown)], None
    return [], None


def office_powerpoint_tool(args: dict, **kwargs) -> str:
    raw_action = _normalize_action(_safe_str(args.get("action")))
    action = _PPT_ALIASES.get(raw_action, raw_action)
    if not action:
        return tool_error("action is required", supported_actions=_PPT_ACTIONS)

    path = _safe_str(args.get("path")) or _safe_str(args.get("output_file"))
    output_path = _safe_str(args.get("output_path")) or _safe_str(args.get("output_file"))
    title = _safe_str(args.get("title"))
    subtitle = _safe_str(args.get("subtitle"))
    notes = _safe_str(args.get("notes"))
    bullets = args.get("bullets")
    layout = args.get("layout")
    index = args.get("index")
    template = _safe_str(args.get("template"))
    find_text = args.get("find_text")
    replace_text = args.get("replace_text")
    fmt = (_safe_str(args.get("format")) or "text").lower()

    script = _PPT_SCRIPT_ROOT / "deck.py"

    if action == "read_text":
        if not path:
            return tool_error("path is required for read_text")
        resolved, err = _resolve_for_read(path)
        if err:
            return tool_error(err)
        cmd = ["read", resolved]
        if fmt == "json":
            cmd.append("--json")
        elif fmt in {"markdown", "md"}:
            cmd.append("--markdown")
        return _run_script(script, cmd)

    if action == "list_layouts":
        if not path:
            return tool_error("path is required for list_layouts")
        resolved, err = _resolve_for_read(path)
        if err:
            return tool_error(err)
        return _run_script(script, ["layouts", resolved])

    if action == "create":
        if not output_path or not title:
            return tool_error("output_path and title are required for create (plus slides[], text or source_path for content)")
        resolved_output, err = _resolve_for_write(output_path)
        if err:
            return tool_error(err)
        slides, err = _slides_from_args(args)
        if err:
            return tool_error(err)
        cmd = ["create", resolved_output, "--title", title]
        if subtitle:
            cmd.extend(["--subtitle", subtitle])
        if slides:
            cmd.extend(["--slides-json", json.dumps(slides, ensure_ascii=False)])
        if layout is not None and _safe_str(layout):
            cmd.extend(["--layout", str(layout)])
        if template:
            resolved_template, err = _resolve_for_read(template)
            if err:
                return tool_error(err)
            cmd.extend(["--template", resolved_template])
            if args.get("keep_template_slides"):
                cmd.append("--keep-template-slides")
        return _run_script(script, cmd)

    if action in {"add_slide", "delete_slide", "find_replace", "to_pdf"}:
        if not path:
            return tool_error(f"path is required for {action}")
        resolved, err = _resolve_for_read(path)
        if err:
            return tool_error(err)
        _, err = _resolve_for_write(resolved)
        if err:
            return tool_error(err)
    else:
        return tool_error("Unsupported powerpoint action", supported_actions=_PPT_ACTIONS)

    if action == "add_slide":
        if not title and not bullets:
            return tool_error("add_slide needs a title and/or bullets")
        cmd = ["add-slide", resolved]
        if title:
            cmd.extend(["--title", title])
        if isinstance(bullets, list) and bullets:
            cmd.extend(["--bullets-json", json.dumps(bullets, ensure_ascii=False)])
        elif isinstance(bullets, str) and bullets.strip():
            lines = [line for line in bullets.splitlines() if line.strip()]
            cmd.extend(["--bullets-json", json.dumps(lines, ensure_ascii=False)])
        if notes:
            cmd.extend(["--notes", notes])
        if layout is not None and _safe_str(layout):
            cmd.extend(["--layout", str(layout)])
        if index is not None:
            cmd.extend(["--index", str(_safe_int(index, 1, lo=1))])
        return _run_script(script, cmd)

    if action == "delete_slide":
        if index is None:
            return tool_error("index (1-based) is required for delete_slide")
        return _run_script(script, ["delete-slide", resolved, "--index", str(_safe_int(index, 1, lo=1))])

    if action == "find_replace":
        if find_text is None or replace_text is None or not str(find_text):
            return tool_error("find_text (non-empty) and replace_text are required for find_replace")
        return _run_script(script, ["replace", resolved, "--find", str(find_text), "--replace", str(replace_text)])

    if action == "to_pdf":
        return _run_script(script, ["to-pdf", resolved])

    return tool_error("Unsupported powerpoint action", supported_actions=_PPT_ACTIONS)


# --------------------------------------------------------------------------- schemas

OFFICE_WORD_SCHEMA = {
    "name": "office_word",
    "description": (
        "Read, create, edit and convert Word (.docx) documents. Relative paths resolve against the workspace. "
        "Actions and required args: read_text|read_markdown|read_tables|read_metadata|read_styles -> path; "
        "from_markdown -> output_path + (source_path | text markdown); "
        "find_replace -> path+find_text+replace_text (spans formatting runs, reports count); "
        "append_paragraph -> path+text; merge -> source_paths[]+output_path; "
        "convert -> path+format (pdf|md|html; pdf needs LibreOffice or Word, md/html prefer pandoc)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": _WORD_ACTIONS},
            "path": {"type": "string", "description": "Existing .docx to read or edit"},
            "source_path": {"type": "string", "description": "Markdown/text file (or data:text/markdown,<url-encoded>) for from_markdown"},
            "text": {"type": "string", "description": "Inline markdown for from_markdown, or paragraph text for append_paragraph"},
            "source_paths": {"type": "array", "items": {"type": "string"}},
            "output_path": {"type": "string"},
            "format": {"type": "string", "enum": ["pdf", "md", "markdown", "html", "htm"]},
            "find_text": {"type": "string"},
            "replace_text": {"type": "string"},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}

OFFICE_EXCEL_SCHEMA = {
    "name": "office_excel",
    "description": (
        "Read, create, edit, format and export Excel (.xlsx) / CSV files. Relative paths resolve against the workspace. "
        "Actions: read_sheet -> path (+sheet, max_rows default 200, or range 'A1:D20'); list_sheets|stats|metadata -> path; "
        "read_cell -> path+cell; create -> output_path (+sheets[]); from_csv -> source_path+output_path; "
        "set_cell -> path+cell+value; set_cells -> path+cells ({'A1': v, ...} or [[cell, v], ...], one save); "
        "append_row -> path + values[] (typed) or row_csv (quoted CSV); "
        "format_cells -> path+range+style {bold, italic, font_color, fill, font_size, number_format, align, wrap, col_width, row_height, autofit}; "
        "to_csv -> path (+output_path); to_pdf -> path (LibreOffice). Values starting with '=' are formulas."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": _EXCEL_ACTIONS},
            "path": {"type": "string"},
            "source_path": {"type": "string"},
            "output_path": {"type": "string"},
            "sheet": {"type": "string"},
            "sheets": {"type": "array", "items": {"type": "string"}, "description": "Sheet names for create"},
            "cell": {"type": "string", "description": "Cell reference like B5"},
            "range": {"type": "string", "description": "Cell range like A1:D20"},
            "value": {"type": ["string", "number", "boolean"]},
            "cells": {
                "type": ["object", "array"],
                "description": "Batch for set_cells: {\"A1\": \"Region\", \"B2\": 12.5} or [[\"A1\", \"Region\"], ...]",
            },
            "values": {"type": "array", "items": {"type": ["string", "number", "boolean", "null"]}, "description": "Typed row for append_row"},
            "row_csv": {"type": "string", "description": "One CSV line for append_row (quotes protect commas)"},
            "max_rows": {"type": "integer", "minimum": 1, "maximum": _MAX_ROWS_CAP},
            "style": {"type": "object", "description": "Formatting options for format_cells"},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}

OFFICE_POWERPOINT_SCHEMA = {
    "name": "office_powerpoint",
    "description": (
        "Read, create and edit PowerPoint (.pptx) presentations / slide decks with python-pptx (no unpack/pack). Relative paths resolve against the workspace. "
        "Actions: read_text -> path (+format text|json|markdown); list_layouts -> path; "
        "create -> output_path+title (+subtitle, slides[] of {title, bullets[], notes, layout} | text markdown with # headings and - bullets | source_path, template .pptx); "
        "add_slide -> path + title/bullets[] (+notes, layout index|name, index 1-based); delete_slide -> path+index; "
        "find_replace -> path+find_text+replace_text (slides, tables, notes); to_pdf -> path (LibreOffice)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": _PPT_ACTIONS},
            "path": {"type": "string", "description": "Existing .pptx"},
            "output_path": {"type": "string", "description": "Target .pptx for create"},
            "title": {"type": "string"},
            "subtitle": {"type": "string"},
            "slides": {
                "type": "array",
                "items": {"type": ["object", "string"]},
                "description": "Content slides for create: [{\"title\": ..., \"bullets\": [\"a\", {\"text\": \"b\", \"level\": 1}], \"notes\": ..., \"layout\": ...}]",
            },
            "text": {"type": "string", "description": "Markdown outline for create (# heading per slide, - bullets)"},
            "source_path": {"type": "string", "description": "Markdown file for create"},
            "template": {"type": "string", "description": "Existing .pptx whose masters/layouts to reuse for create"},
            "keep_template_slides": {"type": "boolean"},
            "bullets": {"type": "array", "items": {"type": ["string", "object"]}},
            "notes": {"type": "string"},
            "layout": {"type": ["string", "integer"], "description": "Layout index or name (see list_layouts)"},
            "index": {"type": "integer", "minimum": 1},
            "find_text": {"type": "string"},
            "replace_text": {"type": "string"},
            "format": {"type": "string", "enum": ["text", "json", "markdown"]},
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
