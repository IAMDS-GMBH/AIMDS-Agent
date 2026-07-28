"""Microsoft 365 MSAL Authentication and Token Cache utilities.

Provides a unified MSAL PublicClientApplication factory and atomic token cache
persistence (~/.hermes/m365_token_cache.bin) shared across Hermes CLI,
Dashboard (web_server.py), and MSOffice365MCP server.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional


def get_m365_token_cache_path() -> Path:
    """Return absolute path to the shared M365 MSAL token cache file."""
    hermes_home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    cache_dir = Path(hermes_home)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "m365_token_cache.bin"


def has_valid_msal_cache() -> bool:
    """Return True if the shared M365 MSAL cache file exists and contains accounts."""
    try:
        app = get_msal_app()
        return bool(app.get_accounts())
    except Exception:
        return False


def get_msal_app(
    client_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> Any:
    """Instantiate MSAL PublicClientApplication loaded with the shared token cache.

    Args:
        client_id: Optional custom client ID (defaults to M365_CLIENT_ID or multi-tenant app ID).
        tenant_id: Optional custom tenant ID (defaults to M365_TENANT_ID or 'organizations').
    """
    try:
        from tools.lazy_deps import ensure as _lazy_ensure
        _lazy_ensure("provider.msal", prompt=False)
    except Exception:
        pass
    import msal

    custom_client_id = (
        client_id
        or os.environ.get("M365_CLIENT_ID")
        or os.environ.get("OUTLOOK_CLIENT_ID")
        or os.environ.get("TEAMS_CLIENT_ID")
    )
    custom_tenant_id = (
        tenant_id
        or os.environ.get("M365_TENANT_ID")
        or os.environ.get("OUTLOOK_TENANT_ID")
        or os.environ.get("TEAMS_TENANT_ID")
    )

    client_id_val = custom_client_id or "41c29967-8ee6-4fac-b484-e87460272bda"  # Microsoft Intune / Office multi-tenant app ID
    tenant_id_val = custom_tenant_id or "organizations"

    if tenant_id_val == "common":
        tenant_id_val = "organizations"

    cache = msal.SerializableTokenCache()
    cache_path = get_m365_token_cache_path()
    if cache_path.exists():
        try:
            cache_data = json.loads(cache_path.read_text(encoding="utf-8"))
            accounts = cache_data.get("Account", {})
            if accounts and tenant_id_val == "organizations":
                first_acc = next(iter(accounts.values()))
                if first_acc.get("realm"):
                    tenant_id_val = first_acc["realm"]
            cache.deserialize(json.dumps(cache_data))
        except Exception:
            pass

    authority = f"https://login.microsoftonline.com/{tenant_id_val}"

    return msal.PublicClientApplication(
        client_id=client_id_val,
        authority=authority,
        token_cache=cache,
    )


AADSTS_ERROR_MAP = {
    "AADSTS50011": "The reply URL specified in the request does not match the reply URLs configured for the application (Azure Portal Redirect URI mismatch).",
    "AADSTS50076": "Multi-Factor Authentication (MFA) or conditional access policy is required by your tenant administrator.",
    "AADSTS65001": "The user or administrator has not consented to use the application. Call m365_generate_admin_consent_url or request tenant admin approval.",
    "AADSTS700016": "Application ID (client_id) was not found in the directory.",
    "AADSTS90002": "Tenant ID was not found or is invalid.",
    "AADSTS50105": "The signed in user is not assigned to a role for the application in Azure Active Directory.",
    "AADSTS7000215": "Invalid client secret provided.",
}


def translate_aadsts_error(error_text: str) -> str:
    """Translate raw Azure Active Directory AADSTS error codes into user-friendly explanation."""
    if not error_text:
        return ""
    for code, explanation in AADSTS_ERROR_MAP.items():
        if code in str(error_text):
            return f"\n[M365 OAuth Hint ({code})]: {explanation}"
    return ""


def save_msal_cache(app: Any, cache_path: Optional[Path] = None) -> None:
    """Atomically persist MSAL token cache to disk if state has changed."""
    cache = getattr(app, "token_cache", None)
    if cache and getattr(cache, "has_state_changed", False):
        if cache_path is None:
            cache_path = get_m365_token_cache_path()
        tmp_path = cache_path.with_name(f"{cache_path.name}.tmp.{os.getpid()}")
        tmp_path.write_text(cache.serialize(), encoding="utf-8")
        os.replace(tmp_path, cache_path)
