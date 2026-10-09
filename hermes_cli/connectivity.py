"""Is this machine offline? (AIS-463)

An update that cannot reach GitHub because the network is down is not a
failure to report: it filed up to three support cases with technical text for
a short offline phase, and the user only saw "update failed". Callers ask
``is_offline()`` first and say "no internet connection, retrying later".

A direct TCP connection to GitHub decides. Behind a configured proxy that
says nothing about reachability, so the machine is never classed as offline
there and the normal (reporting) path runs. "Configured" includes the system
proxy settings (macOS network settings, Windows registry), not only the
environment: behind a system proxy the direct probe always failed, the update
exited "offline" and waited for a browser "online" event that never came
(AIS-527).
"""

from __future__ import annotations

import os
import socket
import time
from typing import Optional

#: The update only ever talks to GitHub (origin, release mirror, archives).
_PROBE_HOSTS = (("github.com", 443), ("api.github.com", 443), ("codeload.github.com", 443))
_PROXY_VARS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")
#: One update run asks several times (start, each incident); probe once.
_CACHE_SECONDS = 30.0

_cache: dict = {"at": None, "offline": False}


def _proxy_configured() -> bool:
    """A proxy from the environment or the operating system's settings."""
    if any(os.environ.get(var) for var in _PROXY_VARS):
        return True
    try:
        import urllib.request

        proxies = urllib.request.getproxies()
    except Exception:
        return False
    return any(proxies.get(scheme) for scheme in ("https", "http", "all"))


def is_offline(timeout: float = 3.0, *, use_cache: bool = True) -> bool:
    """True when none of GitHub's hosts accepts a TCP connection."""
    if _proxy_configured():
        return False
    now = time.monotonic()
    cached_at: Optional[float] = _cache["at"]
    if use_cache and cached_at is not None and now - cached_at < _CACHE_SECONDS:
        return bool(_cache["offline"])
    offline = True
    for host, port in _PROBE_HOSTS:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                offline = False
                break
        except OSError:
            continue
    _cache["at"] = now
    _cache["offline"] = offline
    return offline


def reset_cache() -> None:
    """Forget the last probe (tests, or a caller that knows the network changed)."""
    _cache["at"] = None
    _cache["offline"] = False
