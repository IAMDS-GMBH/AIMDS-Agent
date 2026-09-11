"""Automatic support cases for update / installer fallbacks (AIS-323).

The public release repository is the primary code source of installed
clients; the source repository (git, GitHub archives) stays as an emergency
fallback so it can be made public again without losing clients. Every time a
client has to take that fallback — or an update fails outright — the event is
logged *and* reported to the support server as a case, so the operators see
it before customers do. ``hermes support send-logs`` is the transport (same
bundle: redacted logs, ``hermes dump``, metadata.json), this module only adds
the policy around it:

* ``support.auto_report`` (config, default ``true``) or the environment
  variable ``HERMES_SUPPORT_AUTO_REPORT=0`` switch the upload off — logging
  stays on.
* One case per event kind per 24 hours (``<HERMES_HOME>/logs/incident-reports.json``),
  so a flapping network cannot flood the support queue.
* Never raises, never blocks for long (short upload timeout), never runs
  under pytest.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

logger = logging.getLogger("hermes.incident")

CATEGORY_INSTALLATION_UPDATE = "installation_update"
STATE_FILENAME = "incident-reports.json"
DEFAULT_WINDOW_SECONDS = 24 * 60 * 60
_UPLOAD_TIMEOUT_SECONDS = 20
_MAX_LOG_LINES = 400


def _hermes_home() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home()


def _support_config() -> dict[str, Any]:
    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
        support = cfg.get("support")
        return support if isinstance(support, dict) else {}
    except Exception:
        return {}


def auto_report_enabled() -> bool:
    """Whether fallbacks/failures are uploaded as support cases (logging is unconditional)."""
    env = os.environ.get("HERMES_SUPPORT_AUTO_REPORT", "").strip().lower()
    if env in ("0", "false", "no", "off"):
        return False
    if env in ("1", "true", "yes", "on"):
        return True
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    value = _support_config().get("auto_report", True)
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "off")
    return bool(value)


def state_path() -> Path:
    return _hermes_home() / "logs" / STATE_FILENAME


def _load_state() -> dict[str, Any]:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict[str, Any]) -> None:
    path = state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        logger.debug("incident state not saved: %s", exc)


def recently_reported(kind: str, *, window_seconds: int = DEFAULT_WINDOW_SECONDS, now: Optional[float] = None) -> bool:
    entry = _load_state().get(kind)
    if not isinstance(entry, dict):
        return False
    try:
        last = float(entry.get("reported_at", 0))
    except (TypeError, ValueError):
        return False
    return (now if now is not None else time.time()) - last < window_seconds


def _remember(kind: str, case_id: str, summary: str) -> None:
    state = _load_state()
    state[kind] = {"reported_at": time.time(), "case_id": case_id, "summary": summary[:200]}
    _save_state(state)


def _upload(args: SimpleNamespace) -> dict[str, Any]:
    """Build and upload the support bundle without the CLI's stdout chatter."""
    from hermes_cli.support_logs import (
        _collect_payload,
        _upload_bundle,
        _write_bundle,
        normalize_upload_url,
    )

    support_cfg = _support_config()
    upload_url = normalize_upload_url(str(support_cfg.get("upload_url", "") or os.getenv("SUPPORT_UPLOAD_URL", "")))
    api_key = str(support_cfg.get("api_key", "") or os.getenv("SUPPORT_API_KEY", "")).strip()
    files, metadata = _collect_payload(args, include_dump=True, max_lines_per_file=_MAX_LOG_LINES)
    bundle_path = _write_bundle(files)
    try:
        return _upload_bundle(
            upload_url=upload_url,
            api_key=api_key,
            bundle_path=bundle_path,
            timeout_seconds=_UPLOAD_TIMEOUT_SECONDS,
            reason=args.reason,
            support_case_id=str(metadata.get("support_case_id", "") if isinstance(metadata, dict) else ""),
        )
    finally:
        try:
            bundle_path.unlink()
        except OSError:
            pass


def report_incident(
    kind: str,
    summary: str,
    description: str = "",
    *,
    severity: str = "medium",
    category: str = CATEGORY_INSTALLATION_UPDATE,
    context_type: str = "update_failure",
    install_type: str = "update",
    client_type: str = "hermes-cli",
    session_id: str = "",
    quiet: bool = False,
) -> Optional[str]:
    """Log the incident and, when allowed and not rate-limited, open a support case.

    Returns the case / reference id when a case was created, else ``None``.
    ``kind`` is a stable slug such as ``update-fallback-git`` — it becomes the
    upload reason and the rate-limit key.
    """
    kind = (kind or "incident").strip().lower().replace(" ", "-")
    logger.warning("[incident] %s: %s%s", kind, summary, f" — {description}" if description else "")
    if not auto_report_enabled():
        logger.info("[incident] %s not reported (support.auto_report is off)", kind)
        return None
    if recently_reported(kind):
        logger.info("[incident] %s already reported within the last 24 h — not reported again", kind)
        return None
    args = SimpleNamespace(
        reason=kind,
        category=category,
        severity=severity,
        summary=summary[:200],
        user_description=description,
        session_id=session_id,
        session_json="",
        client_type=client_type,
        client_version="",
        install_type=install_type,
        context_type=context_type,
        attachment=[],
        attachments=[],
        include_dump=True,
        max_lines=_MAX_LOG_LINES,
        timeout=_UPLOAD_TIMEOUT_SECONDS,
        url="",
        api_key="",
        output=None,
        json=True,
    )
    try:
        result = _upload(args)
    except Exception as exc:  # network, server, packaging — never break the caller
        logger.warning("[incident] %s could not be reported to support: %s", kind, exc)
        return None
    case_id = str(result.get("reference_id") or result.get("support_case_id") or "").strip()
    _remember(kind, case_id, summary)
    logger.info("[incident] %s reported to support as %s", kind, case_id or "(no id)")
    if not quiet:
        print(f"  ℹ Reported to support{f' as {case_id}' if case_id else ''} (automatic incident report; disable with support.auto_report: false)", file=sys.stderr)
    return case_id or ""


__all__ = [
    "CATEGORY_INSTALLATION_UPDATE",
    "auto_report_enabled",
    "recently_reported",
    "report_incident",
    "state_path",
]
