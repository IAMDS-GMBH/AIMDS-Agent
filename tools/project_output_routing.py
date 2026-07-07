#!/usr/bin/env python3
"""Project-level output routing for simple write_file targets."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_META_PREFIX = "project_output_subfolder::"


def _normalize_workspace_root(cwd: str) -> str:
    base = Path(cwd or os.getcwd()).expanduser().resolve()
    try:
        proc = subprocess.run(
            ["git", "-C", str(base), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except Exception:
        return str(base)
    if proc.returncode != 0:
        return str(base)
    top = (proc.stdout or "").strip()
    if not top:
        return str(base)
    return str(Path(top).expanduser().resolve())


def _meta_key(cwd: str) -> str:
    return f"{_META_PREFIX}{_normalize_workspace_root(cwd)}"


def _db():
    from hermes_state import SessionDB

    return SessionDB()


def _normalize_subfolder(value: str) -> str:
    raw = (value or "").strip().replace("\\", "/")
    if not raw:
        raise ValueError("subfolder cannot be empty")
    if raw.startswith("/") or raw.startswith("~"):
        raise ValueError("subfolder must be a relative path")
    parts = [p for p in raw.split("/") if p]
    if not parts:
        raise ValueError("subfolder cannot be empty")
    if any(p in {".", ".."} for p in parts):
        raise ValueError("subfolder cannot contain '.' or '..'")
    return "/".join(parts)


def set_project_output_subfolder(cwd: str, subfolder: str) -> dict:
    normalized = _normalize_subfolder(subfolder)
    root = _normalize_workspace_root(cwd)
    db = _db()
    db.set_meta(_meta_key(cwd), normalized)
    return {"project_root": root, "subfolder": normalized}


def clear_project_output_subfolder(cwd: str) -> dict:
    root = _normalize_workspace_root(cwd)
    db = _db()
    db.set_meta(_meta_key(cwd), "")
    return {"project_root": root, "subfolder": None}


def get_project_output_subfolder(cwd: str) -> dict:
    root = _normalize_workspace_root(cwd)
    db = _db()
    value = db.get_meta(_meta_key(cwd))
    if not value:
        return {"project_root": root, "subfolder": None}
    return {"project_root": root, "subfolder": value}


def _is_simple_relative_file(path: str) -> bool:
    raw = (path or "").strip()
    if not raw:
        return False
    if os.path.isabs(raw) or raw.startswith("~"):
        return False
    norm = raw.replace("\\", "/")
    if "/" in norm:
        return False
    if norm in {".", ".."}:
        return False
    # Avoid surprising routing of dotfiles such as ".env".
    if norm.startswith("."):
        return False
    return True


def route_write_path(path: str, cwd: str) -> tuple[str, str | None]:
    if not _is_simple_relative_file(path):
        return path, None
    info = get_project_output_subfolder(cwd)
    subfolder = info.get("subfolder")
    if not subfolder:
        return path, None
    routed = f"{subfolder.rstrip('/')}/{path.strip()}"
    return routed, subfolder
