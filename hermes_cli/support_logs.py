"""Support log export/upload flow for ``hermes support send-logs``."""

from __future__ import annotations

import contextlib
import io
import json
import os
import platform
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agent.redact import redact_sensitive_text
from hermes_cli.config import load_config
from hermes_cli.dump import run_dump
from hermes_constants import display_hermes_home, get_hermes_home

_LOG_FILES = ("desktop.log", "agent.log", "errors.log", "gateway.log", "gui.log")
_DEFAULT_MAX_LINES_PER_FILE = 1200
_DEFAULT_TIMEOUT_SECONDS = 45
_DEFAULT_UPLOAD_URL = "https://suite-support.iamds.com/api/v1/upload"


def normalize_upload_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return _DEFAULT_UPLOAD_URL
    if raw in {"https://suite-support.iamds.com", "https://suite-support.iamds.com/"}:
        return _DEFAULT_UPLOAD_URL
    if not raw.endswith("/api/v1/upload") and not raw.endswith("/upload"):
        return raw.rstrip("/") + "/api/v1/upload"
    return raw


def normalize_telemetry_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return "https://suite-support.iamds.com/api/v1/telemetry"
    if raw.endswith("/api/v1/upload"):
        return raw[:-14] + "/api/v1/telemetry"
    if raw.endswith("/upload"):
        return raw[:-7] + "/telemetry"
    if not raw.endswith("/api/v1/telemetry") and not raw.endswith("/telemetry"):
        return raw.rstrip("/") + "/api/v1/telemetry"
    return raw


def send_client_telemetry(args: Any = None) -> dict[str, Any]:
    """Send client version telemetry to the support server.

    Quietly returns error dict on failure without raising.
    """
    try:
        support_cfg = _support_config()
        raw_url = str(
            getattr(args, "url", "")
            or support_cfg.get("upload_url", "")
            or os.getenv("SUPPORT_UPLOAD_URL", "")
        ).strip()
        telemetry_url = normalize_telemetry_url(raw_url)
        api_key = str(
            getattr(args, "api_key", "")
            or support_cfg.get("api_key", "")
            or os.getenv("SUPPORT_API_KEY", "")
        ).strip()

        hostname = socket.gethostname()
        user_id_val = os.getenv("USER") or os.getenv("USERNAME") or "user"
        try:
            import getpass

            user_id_val = getpass.getuser() or user_id_val
        except Exception:
            pass
        client_id = f"{hostname}-{user_id_val}"

        version = os.getenv("HERMES_VERSION") or getattr(args, "version", "") or ""
        if not version:
            try:
                p = Path(__file__).resolve().parent.parent / "pyproject.toml"
                if p.exists():
                    for line in p.read_text(encoding="utf-8").splitlines():
                        if line.strip().startswith("version ="):
                            version = line.split("=")[1].strip().strip('"')
                            break
            except Exception:
                pass
        if not version:
            version = "0.7.1"

        channel = "main"
        patch_level = version
        commits_behind_main = 0

        try:
            hermes_home = get_hermes_home()
            root = hermes_home / "hermes-agent"
            if not (root / ".git").exists():
                root = Path(__file__).resolve().parent.parent
            if (root / ".git").exists():
                import subprocess

                b_res = subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if b_res.returncode == 0 and b_res.stdout.strip():
                    channel = b_res.stdout.strip()
                s_res = subprocess.run(
                    ["git", "rev-parse", "--short", "HEAD"],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if s_res.returncode == 0 and s_res.stdout.strip():
                    patch_level = s_res.stdout.strip()
                c_res = subprocess.run(
                    ["git", "rev-list", "HEAD..origin/main", "--count"],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if c_res.returncode == 0 and c_res.stdout.strip().isdigit():
                    commits_behind_main = int(c_res.stdout.strip())
        except Exception:
            pass

        payload = {
            "client_id": client_id,
            "customer_id": os.getenv("IAMDS_CUSTOMER_ID") or support_cfg.get("customer_id") or "cust-iamds",
            "environment": os.getenv("HERMES_ENV") or "production",
            "version": version,
            "channel": channel,
            "patch_level": patch_level,
            "commits_behind_main": commits_behind_main,
        }

        data_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "hermes-client-telemetry/1.0",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        req = urllib.request.Request(telemetry_url, data=data_bytes, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return {
                "ok": True,
                "telemetry_url": telemetry_url,
                "status_code": getattr(resp, "status", 200),
                "payload": payload,
                "response": body,
            }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def run_send_telemetry(args: Any) -> int:
    json_mode = bool(getattr(args, "json", False))
    res = send_client_telemetry(args)
    if json_mode:
        print(json.dumps(res))
    else:
        if res.get("ok"):
            print(f"Telemetry sent successfully to {res.get('telemetry_url')}.")
        else:
            print(f"Telemetry send failed: {res.get('error')}", file=sys.stderr)
    return 0 if res.get("ok") else 1


def _read_last_lines(path: Path, count: int) -> list[str]:
    if count <= 0:
        return []
    try:
        size = path.stat().st_size
    except OSError:
        return []
    if size <= 0:
        return []

    if size <= 1_048_576:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                return handle.readlines()[-count:]
        except OSError:
            return []

    lines: list[bytes] = []
    pos = size
    chunk_size = 8192
    try:
        with open(path, "rb") as handle:
            while pos > 0 and len(lines) <= count + 1:
                read_size = min(chunk_size, pos)
                pos -= read_size
                handle.seek(pos)
                chunk = handle.read(read_size)
                chunk_lines = chunk.split(b"\n")
                if lines:
                    lines[0] = chunk_lines[-1] + lines[0]
                    lines = chunk_lines[:-1] + lines
                else:
                    lines = chunk_lines
                chunk_size = min(chunk_size * 2, 65_536)
    except OSError:
        return []

    out: list[str] = []
    for raw in lines:
        if not raw:
            continue
        out.append(raw.decode("utf-8", errors="replace") + "\n")
    return out[-count:]


def _capture_dump_text() -> str:
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        run_dump(SimpleNamespace(show_keys=False))
    return stream.getvalue()


def _support_config() -> dict[str, Any]:
    cfg = load_config()
    support = cfg.get("support", {})
    return support if isinstance(support, dict) else {}


def _collect_payload(
    args: Any = None, *, include_dump: bool = True, max_lines_per_file: int = _DEFAULT_MAX_LINES_PER_FILE
) -> tuple[dict[str, str], dict[str, Any]]:
    hermes_home = get_hermes_home()
    log_dir = hermes_home / "logs"
    files: dict[str, str] = {}
    included_files: list[dict[str, Any]] = []

    for filename in _LOG_FILES:
        path = log_dir / filename
        if not path.exists() or not path.is_file():
            continue
        lines = _read_last_lines(path, max_lines_per_file)
        if not lines:
            continue
        redacted = "".join(redact_sensitive_text(line, force=True) for line in lines)
        files[f"logs/{filename}"] = redacted
        included_files.append(
            {
                "name": filename,
                "lines": len(lines),
                "bytes": len(redacted.encode("utf-8")),
            }
        )

    if include_dump:
        files["dump.txt"] = redact_sensitive_text(_capture_dump_text(), force=True)

    session_data: dict[str, Any] | None = None
    session_json_input = getattr(args, "session_json", None) or ""
    if session_json_input:
        raw_text = ""
        p = Path(session_json_input).expanduser()
        if p.exists() and p.is_file():
            try:
                raw_text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
        else:
            raw_text = session_json_input

        if raw_text:
            try:
                session_data = json.loads(raw_text)
                files["session.json"] = json.dumps(session_data, indent=2, ensure_ascii=False) + "\n"
            except json.JSONDecodeError:
                files["session.json"] = raw_text

    cfg = load_config()
    support_cfg = cfg.get("support", {}) if isinstance(cfg.get("support"), dict) else {}
    model_used = (cfg.get("model") or {}).get("default") or "AIMDS-Suite-Auto"
    provider_name = (cfg.get("model") or {}).get("provider") or "aimds-suite-prod"
    providers = cfg.get("providers") or {}
    provider_cfg = providers.get(provider_name) if isinstance(providers.get(provider_name), dict) else {}
    litellm_url = provider_cfg.get("base_url") or "https://suite.iamds.com/litellm/v1"
    mcp_servers = list((cfg.get("mcp_servers") or {}).keys())
    active_skills = list((cfg.get("skills") or {}).get("inline") or [])

    now_utc = datetime.now(timezone.utc)
    support_case_id = f"SUP-{now_utc.strftime('%Y%m%d-%H%M%S')}"

    files_manifest: list[dict[str, Any]] = []
    for rel_path, content in files.items():
        category = "log"
        mime = "text/plain"
        if rel_path.endswith(".json"):
            mime = "application/json"
            category = "chat_history" if "session" in rel_path else "config"
        elif rel_path.endswith(".txt"):
            category = "system_info"

        files_manifest.append(
            {
                "path": rel_path,
                "mime_type": mime,
                "size_bytes": len(content.encode("utf-8")),
                "content_category": category,
            }
        )

    session_id_val = getattr(args, "session_id", "") or (session_data.get("session_id") if session_data else "")
    session_title_val = session_data.get("title") if session_data else ""
    session_turn_count = session_data.get("message_count") if session_data else 0

    user_id_val = os.getenv("USER") or os.getenv("USERNAME") or "unknown"
    try:
        import getpass
        user_id_val = getpass.getuser() or user_id_val
    except Exception:
        pass

    metadata = {
        "schema_version": "1.1.0",
        "support_case_id": support_case_id,
        "customer_id": os.getenv("IAMDS_CUSTOMER_ID") or support_cfg.get("customer_id") or "cust-iamds",
        "customer_name": os.getenv("IAMDS_CUSTOMER_NAME") or support_cfg.get("customer_name") or "IAMDS GmbH",
        "litellm_url": litellm_url,
        "model_used": model_used,
        "embedding_model_used": "text-embedding-3-small",
        "environment": os.getenv("HERMES_ENV") or "production",
        "timestamp": now_utc.isoformat(),
        "client_info": {
            "client_type": getattr(args, "client_type", None) or "hermes-cli",
            "client_version": getattr(args, "client_version", None) or "v1.0.75",
            "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "user_id": user_id_val,
        },
        "issue_details": {
            "category": getattr(args, "category", None) or "other",
            "severity": getattr(args, "severity", None) or "medium",
            "summary": getattr(args, "summary", None) or getattr(args, "reason", "manual"),
            "user_description": getattr(args, "user_description", None) or "",
        },
        "session_context": {
            "session_id": session_id_val,
            "session_title": session_title_val,
            "turn_count": session_turn_count,
            "loaded_mcp_tools": mcp_servers,
            "active_skills": active_skills,
        },
        "context_type": getattr(args, "context_type", None) or ("chat_session" if session_id_val else "manual"),
        "install_type": getattr(args, "install_type", None) or None,
        "lifecycle": {
            "retention_days": 14,
            "max_size_kb": 25600,
        },
        "files": files_manifest,
    }

    files["metadata.json"] = json.dumps(metadata, indent=2, ensure_ascii=False) + "\n"

    manifest = {
        "schema": 1,
        "created_at": now_utc.isoformat(),
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "hermes_home": display_hermes_home(),
        "included_files": included_files,
        "includes_dump": include_dump,
        "metadata": metadata,
    }
    files["manifest.json"] = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"

    return files, metadata


def _write_bundle(files: dict[str, str], *, keep_path: str | None = None) -> Path:
    if keep_path:
        bundle_path = Path(keep_path).expanduser().resolve()
        bundle_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        fd, tmp_path = tempfile.mkstemp(prefix="hermes-support-", suffix=".zip")
        os.close(fd)
        bundle_path = Path(tmp_path)

    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel, body in files.items():
            zf.writestr(rel, body)
    return bundle_path


def _build_multipart_payload(
    file_bytes: bytes,
    filename: str,
    support_case_id: str = "",
) -> tuple[bytes, str]:
    boundary = f"----HermesBoundary{uuid.uuid4().hex}"
    body = bytearray()

    if support_case_id:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(b'Content-Disposition: form-data; name="support_case_id"\r\n\r\n')
        body.extend(f"{support_case_id}\r\n".encode("utf-8"))

    zip_filename = filename if filename.lower().endswith(".zip") else f"{filename}.zip"
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(f'Content-Disposition: form-data; name="file"; filename="{zip_filename}"\r\n'.encode("utf-8"))
    body.extend(b"Content-Type: application/zip\r\n\r\n")
    body.extend(file_bytes)
    body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    content_type = f"multipart/form-data; boundary={boundary}"
    return bytes(body), content_type


def _upload_bundle(
    *,
    upload_url: str,
    api_key: str,
    bundle_path: Path,
    timeout_seconds: int,
    reason: str,
    support_case_id: str = "",
) -> dict[str, Any]:
    file_bytes = bundle_path.read_bytes()
    payload, content_type = _build_multipart_payload(
        file_bytes=file_bytes,
        filename=bundle_path.name,
        support_case_id=support_case_id,
    )
    headers = {
        "Authorization": f"Bearer {api_key}" if api_key else "Bearer anonymous",
        "Content-Type": content_type,
        "User-Agent": "hermes-support-log-export/1",
        "X-Hermes-Reason": reason or "manual",
        "X-Hermes-Filename": bundle_path.name,
    }
    req = urllib.request.Request(upload_url, data=payload, headers=headers, method="POST")
    started = time.time()
    with urllib.request.urlopen(req, timeout=max(5, int(timeout_seconds))) as response:
        body = response.read().decode("utf-8", errors="replace")
        elapsed_ms = int((time.time() - started) * 1000)
        parsed: dict[str, Any] = {}
        if body:
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = {"raw": body}

        job_id = parsed.get("job_id") or parsed.get("id") or ""
        support_case_id = parsed.get("support_case_id") or parsed.get("case_id") or ""
        reference_id = (
            job_id
            or support_case_id
            or parsed.get("reference_id")
            or parsed.get("ticket_id")
        )
        return {
            "status_code": int(getattr(response, "status", 202)),
            "elapsed_ms": elapsed_ms,
            "job_id": job_id,
            "support_case_id": support_case_id,
            "reference_id": reference_id,
            "server": parsed,
            "bytes_sent": len(payload),
        }


def run_send_logs(args) -> int:
    support_cfg = _support_config()
    raw_url = str(getattr(args, "url", "") or support_cfg.get("upload_url", "") or os.getenv("SUPPORT_UPLOAD_URL", "")).strip()
    upload_url = normalize_upload_url(raw_url)
    api_key = str(getattr(args, "api_key", "") or support_cfg.get("api_key", "") or os.getenv("SUPPORT_API_KEY", "")).strip()
    timeout_seconds = int(
        getattr(args, "timeout", 0)
        or support_cfg.get("timeout_seconds", _DEFAULT_TIMEOUT_SECONDS)
        or _DEFAULT_TIMEOUT_SECONDS
    )
    max_lines = int(getattr(args, "max_lines", _DEFAULT_MAX_LINES_PER_FILE) or _DEFAULT_MAX_LINES_PER_FILE)
    include_dump = bool(getattr(args, "include_dump", True))
    reason = str(getattr(args, "reason", "manual") or "manual").strip()
    json_mode = bool(getattr(args, "json", False))

    bundle_path: Path | None = None
    try:
        files, metadata = _collect_payload(args, include_dump=include_dump, max_lines_per_file=max_lines)
        if len(files) <= 1:  # only manifest.json
            payload = {"ok": False, "error": "No log content available to upload."}
            if json_mode:
                print(json.dumps(payload))
            else:
                print(payload["error"], file=sys.stderr)
            return 1

        keep_path = getattr(args, "output", None)
        bundle_path = _write_bundle(files, keep_path=keep_path)
        support_case_id = metadata.get("support_case_id", "") if isinstance(metadata, dict) else ""
        upload = _upload_bundle(
            upload_url=upload_url,
            api_key=api_key,
            bundle_path=bundle_path,
            timeout_seconds=timeout_seconds,
            reason=reason,
            support_case_id=support_case_id,
        )
        payload = {
            "ok": True,
            "upload_url": upload_url,
            "bundle_path": str(bundle_path),
            "bundle_bytes": bundle_path.stat().st_size,
            "job_id": upload.get("job_id"),
            "support_case_id": upload.get("support_case_id"),
            "files": metadata.get("files", []),
            "includes_dump": include_dump,
            "status_code": upload["status_code"],
            "elapsed_ms": upload["elapsed_ms"],
            "reference_id": upload.get("reference_id"),
            "server": upload.get("server"),
        }
        if json_mode:
            print(json.dumps(payload))
        else:
            ref = f" (reference: {payload['reference_id']})" if payload.get("reference_id") else ""
            print(f"Support logs uploaded{ref}.")
            print(f"Bundle: {payload['bundle_path']} ({payload['bundle_bytes']} bytes)")
        return 0
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        payload = {"ok": False, "error": f"Support server rejected upload: HTTP {exc.code}", "body": body}
    except urllib.error.URLError as exc:
        payload = {"ok": False, "error": f"Could not reach support server: {exc.reason}"}
    except Exception as exc:
        payload = {"ok": False, "error": str(exc)}
    finally:
        if bundle_path and not getattr(args, "output", None):
            bundle_path.unlink(missing_ok=True)

    if json_mode:
        print(json.dumps(payload))
    else:
        print(payload["error"], file=sys.stderr)
    return 1


def support_command(args) -> int:
    action = getattr(args, "support_action", None)
    if action in {"send-logs", "send_logs"}:
        return run_send_logs(args)
    if action in {"send-telemetry", "send_telemetry"}:
        return run_send_telemetry(args)

    print("Usage: hermes support [send-logs|send-telemetry] [--json]", file=sys.stderr)
    return 2
