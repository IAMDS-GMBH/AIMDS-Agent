"""
Timezone-aware clock for Hermes.

Provides a single ``now()`` helper that returns a timezone-aware datetime
based on the user's configured IANA timezone (e.g. ``Asia/Kolkata``).

Resolution order:
  1. ``HERMES_TIMEZONE`` environment variable
  2. ``timezone`` key in ``~/.hermes/config.yaml``
  3. Falls back to the server's local time (``datetime.now().astimezone()``)

Invalid timezone values log a warning and fall back safely — Hermes never
crashes due to a bad timezone string.
"""

import logging
import os
import platform
from datetime import datetime
from hermes_constants import get_config_path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
except ImportError:
    # Python 3.8 fallback (shouldn't be needed — Hermes requires 3.9+)
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

# Cached state — resolved once, reused on every call.
# Call reset_cache() to force re-resolution (e.g. after config changes).
_cached_tz: Optional[ZoneInfo] = None
_cached_tz_name: Optional[str] = None
_cache_resolved: bool = False

# Separate cache for the *named* zone used by external APIs (e.g. Microsoft
# Graph) that require a real zone name rather than a numeric offset.
_cached_default_tz_name: Optional[str] = None


def _resolve_timezone_name() -> str:
    """Read the configured IANA timezone string (or empty string).

    This does file I/O when falling through to config.yaml, so callers
    should cache the result rather than calling on every ``now()``.
    """
    # 1. Environment variable (highest priority — set by Supervisor, etc.)
    tz_env = os.getenv("HERMES_TIMEZONE", "").strip()
    if tz_env:
        return tz_env

    # 2. config.yaml ``timezone`` key
    try:
        import yaml
        config_path = get_config_path()
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            tz_cfg = cfg.get("timezone", "")
            if isinstance(tz_cfg, str) and tz_cfg.strip():
                return tz_cfg.strip()
    except Exception:
        pass

    return ""


def _get_zoneinfo(name: str) -> Optional[ZoneInfo]:
    """Validate and return a ZoneInfo, or None if invalid."""
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (KeyError, Exception) as exc:
        logger.warning(
            "Invalid timezone '%s': %s. Falling back to server local time.",
            name, exc,
        )
        return None


def get_timezone() -> Optional[ZoneInfo]:
    """Return the user's configured ZoneInfo, or None (meaning server-local).

    Resolved once and cached. Call ``reset_cache()`` after config changes.
    """
    global _cached_tz, _cached_tz_name, _cache_resolved
    if not _cache_resolved:
        _cached_tz_name = _resolve_timezone_name()
        _cached_tz = _get_zoneinfo(_cached_tz_name)
        _cache_resolved = True
    return _cached_tz


def reset_cache() -> None:
    """Clear the cached timezone so the next call re-resolves it.

    Call this after the configured timezone may have changed (e.g. after a
    config edit or ``HERMES_TIMEZONE`` update) to force ``get_timezone()`` /
    ``now()`` to read the new value instead of the value cached at first use.
    """
    global _cached_tz, _cached_tz_name, _cache_resolved, _cached_default_tz_name
    _cached_tz = None
    _cached_tz_name = None
    _cache_resolved = False
    _cached_default_tz_name = None


def now() -> datetime:
    """
    Return the current time as a timezone-aware datetime.

    If a valid timezone is configured, returns wall-clock time in that zone.
    Otherwise returns the server's local time (via ``astimezone()``).
    """
    tz = get_timezone()
    if tz is not None:
        return datetime.now(tz)
    # No timezone configured — use server-local (still tz-aware)
    return datetime.now().astimezone()


def _resolve_os_timezone_name() -> str:
    """Best-effort resolution of the *operating system's* configured
    timezone name, independent of any Hermes-specific config.

    Used only as the last fallback in ``default_timezone_name()`` before
    giving up and returning ``"UTC"``. Unlike ``get_timezone()`` (which
    returns a ``ZoneInfo`` for wall-clock math and is happy to silently fall
    back to a fixed-offset ``astimezone()`` value), external APIs such as
    Microsoft Graph's ``dateTimeTimeZone.timeZone`` require an actual *named*
    zone (IANA or Windows) — a raw UTC offset is not accepted. So we need a
    real name here, not just an offset.
    """
    if platform.system() == "Windows":
        # Microsoft Graph natively understands Windows timezone key names
        # (e.g. "W. Europe Standard Time"), so we can read the OS's own
        # configured zone straight out of the registry and pass it through
        # unmodified — no extra dependency (e.g. tzlocal) required.
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\TimeZoneInformation",
            ) as key:
                name, _ = winreg.QueryValueEx(key, "TimeZoneKeyName")
                if name:
                    return str(name).strip()
        except Exception:
            logger.debug("Could not read Windows timezone from registry", exc_info=True)
        return ""

    # POSIX (macOS/Linux): /etc/localtime is conventionally a symlink into
    # the system zoneinfo database, e.g.
    # /usr/share/zoneinfo/Europe/Berlin -> IANA name "Europe/Berlin".
    try:
        real_path = os.path.realpath("/etc/localtime")
        marker = "zoneinfo/"
        idx = real_path.find(marker)
        if idx != -1:
            name = real_path[idx + len(marker):]
            if name:
                return name
    except Exception:
        logger.debug("Could not resolve /etc/localtime symlink", exc_info=True)
    return ""


def default_timezone_name() -> str:
    """Resolve a real, *named* timezone suitable for external APIs (e.g.
    Microsoft Graph's ``dateTimeTimeZone.timeZone``) that require a zone
    name rather than a numeric offset.

    Resolution order (first match wins):
      1. ``HERMES_TIMEZONE`` env var / ``config.yaml`` ``timezone`` key
         (same source as ``get_timezone()``), if it resolves to a valid zone.
      2. The OS's own configured timezone name (Windows registry key name /
         POSIX ``/etc/localtime`` symlink target).
      3. ``"UTC"`` — only if every other resolution attempt fails.

    Unlike ``get_timezone()``, this never returns ``None`` — callers that
    need to hand a zone name to a third-party API always get *something*
    usable back.
    """
    global _cached_default_tz_name
    if _cached_default_tz_name is not None:
        return _cached_default_tz_name

    configured_name = _resolve_timezone_name()
    if configured_name and _get_zoneinfo(configured_name) is not None:
        _cached_default_tz_name = configured_name
        return configured_name

    os_name = _resolve_os_timezone_name()
    if os_name:
        _cached_default_tz_name = os_name
        return os_name

    logger.warning(
        "Could not resolve a named local timezone (no HERMES_TIMEZONE/config "
        "value and no OS timezone detected) — falling back to literal UTC."
    )
    _cached_default_tz_name = "UTC"
    return "UTC"


