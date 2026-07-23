"""GitHub Device Code OAuth Flow helpers."""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Standard GitHub Client ID (matches Copilot CLI / opencode)
GITHUB_OAUTH_CLIENT_ID = os.getenv("GITHUB_OAUTH_CLIENT_ID", "Ov23li8tweQw6odWQebz")
DEFAULT_GITHUB_SCOPES = ["repo", "read:org", "user"]


def request_github_device_code(
    client_id: str = GITHUB_OAUTH_CLIENT_ID,
    scopes: Optional[list[str]] = None,
    host: str = "github.com",
) -> Dict[str, Any]:
    """Request a device and user code from GitHub's OAuth device endpoint."""
    domain = host.rstrip("/")
    url = f"https://{domain}/login/device/code"
    scope_str = " ".join(scopes or DEFAULT_GITHUB_SCOPES)

    data = urllib.parse.urlencode({
        "client_id": client_id,
        "scope": scope_str,
    }).encode()

    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "HermesAgent/1.0",
        },
    )

    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def poll_github_device_code(
    device_code: str,
    client_id: str = GITHUB_OAUTH_CLIENT_ID,
    host: str = "github.com",
) -> Dict[str, Any]:
    """Poll GitHub's OAuth token endpoint for access token completion."""
    domain = host.rstrip("/")
    url = f"https://{domain}/login/oauth/access_token"

    data = urllib.parse.urlencode({
        "client_id": client_id,
        "device_code": device_code,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    }).encode()

    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "HermesAgent/1.0",
        },
    )

    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def get_github_oauth_status() -> Dict[str, Any]:
    """Check if a GitHub OAuth/PAT token exists in env or config."""
    from hermes_cli.config import get_env_value
    val = get_env_value("GITHUB_PERSONAL_ACCESS_TOKEN") or os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN") or os.getenv("GITHUB_TOKEN") or ""
    val = val.strip()
    if val:
        preview = val[-4:] if len(val) >= 4 else val
        return {
            "logged_in": True,
            "source": "env",
            "source_label": f"GitHub Account Token (…{preview})",
            "token_preview": f"…{preview}",
            "expires_at": None,
            "has_refresh_token": True,
        }
    return {"logged_in": False, "source": None}
