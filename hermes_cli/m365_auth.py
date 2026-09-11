"""Microsoft 365 MSAL Authentication and Token Cache utilities.

Provides a unified MSAL PublicClientApplication factory and atomic token cache
persistence (~/.hermes/m365_token_cache.bin) shared across Hermes CLI,
Dashboard (web_server.py), and MSOffice365MCP server.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional


# ---------------------------------------------------------------------------
# Scope tiers (AIS-286)
#
# Microsoft Graph delegated permissions fall into three consent classes for
# this app. Verified against the Graph permissions reference (2026-09):
#
#   * SELF  — any tenant member may consent for themselves (unless the tenant
#             restricts user consent entirely). This is the login default in
#             every entry point (dashboard button, CLI install, chat tool).
#   * ORG   — "Admin consent required = Yes". A tenant admin has to grant them
#             once, org-wide; afterwards MSAL hands them out silently to every
#             user (refresh tokens are not scope-bound).
#   * ADMIN — directory-wide / org-wide write scopes, only meaningful for
#             tenant administrators.
#
# optional-mcps/MSOffice365MCP/server.py mirrors these literals in its
# ImportError fallback; tests/optional_mcps/test_m365_server.py pins equality.
# ---------------------------------------------------------------------------

M365_DEFAULT_CLIENT_ID = "41c29967-8ee6-4fac-b484-e87460272bda"  # IAMDS-owned multi-tenant app
M365_DEFAULT_ADMIN_CONSENT_REDIRECT_URI = "http://localhost:8400"

M365_SELF_CONSENT_SCOPES: list[str] = [
    "User.Read",
    "Mail.ReadWrite",
    "Mail.Send",
    "Calendars.ReadWrite",
    "Contacts.ReadWrite",
    "Files.ReadWrite.All",
]

M365_ORG_CONSENT_SCOPES: list[str] = [
    "Mail.ReadWrite.Shared",
    "Mail.Send.Shared",
    "Calendars.ReadWrite.Shared",
    "Chat.ReadWrite",
    "Presence.Read",
    "OnlineMeetings.Read",
    "Tasks.ReadWrite",
]

M365_ADMIN_SCOPES: list[str] = [
    "User.Read.All",
    "Directory.Read.All",
    "Sites.ReadWrite.All",
]

M365_STANDARD_SCOPES: list[str] = M365_SELF_CONSENT_SCOPES + M365_ORG_CONSENT_SCOPES
M365_ALL_SCOPES: list[str] = M365_STANDARD_SCOPES + M365_ADMIN_SCOPES

# What every login entry point requests by default.
M365_LOGIN_SCOPES: list[str] = M365_SELF_CONSENT_SCOPES

M365_SCOPE_TIERS: dict[str, list[str]] = {
    "self": M365_SELF_CONSENT_SCOPES,
    "standard": M365_STANDARD_SCOPES,
    "admin": M365_ALL_SCOPES,
}

# Silent token acquisition probes the widest tier first: once a tenant admin
# has consented org-wide, every user silently gets the superset.
M365_SCOPE_TIER_ORDER: tuple[str, ...] = ("admin", "standard", "self")

# Graph endpoints (path fragments) that need at least the given tier. Used to
# turn a bare 403 into "which consent step is missing".
M365_ENDPOINT_TIER_MARKERS: tuple[tuple[str, str], ...] = (
    ("/users", "admin"),
    ("/sites", "admin"),
    ("/chats", "standard"),
    ("/presence", "standard"),
    ("/onlineMeetings", "standard"),
    ("/todo", "standard"),
    ("/communications", "standard"),
    ("/users/", "standard"),  # delegated /users/{upn}/mailFolders etc. (shared scopes)
)


def m365_scopes_for_tier(tier: Optional[str]) -> list[str]:
    """Return the scope list for ``tier`` (``self`` | ``standard`` | ``admin``).

    Unknown or empty values fall back to the login default (``self``) so a
    typo can never silently escalate to an admin-consent prompt.
    """
    key = (tier or "self").strip().lower()
    return list(M365_SCOPE_TIERS.get(key, M365_LOGIN_SCOPES))


def m365_tier_for_endpoint(endpoint: str) -> str:
    """Return the minimum consent tier a Graph endpoint needs (best effort)."""
    ep = endpoint or ""
    # Delegated access to another mailbox (/users/{upn}/...) needs the
    # *.Shared scopes (standard tier), while a bare /users listing needs
    # directory read (admin tier).
    if ep.startswith("/users/") and ep.count("/") >= 3:
        return "standard"
    for marker, tier in M365_ENDPOINT_TIER_MARKERS:
        if marker == "/users/":
            continue
        if marker in ep:
            return tier
    return "self"


def resolve_m365_client_id(client_id: Optional[str] = None) -> str:
    """Client id precedence: explicit → M365_/OUTLOOK_/TEAMS_CLIENT_ID env → IAMDS default."""
    return (
        (client_id or "").strip()
        or os.environ.get("M365_CLIENT_ID", "").strip()
        or os.environ.get("OUTLOOK_CLIENT_ID", "").strip()
        or os.environ.get("TEAMS_CLIENT_ID", "").strip()
        or M365_DEFAULT_CLIENT_ID
    )


def resolve_m365_tenant_id(tenant_id: Optional[str] = None) -> str:
    """Tenant precedence: explicit → env → realm of the cached account → ``organizations``.

    ``common`` is never returned: the v2 admin-consent endpoint rejects it and
    MSAL's authority for work accounts should be ``organizations`` at most.
    """
    candidate = (
        (tenant_id or "").strip()
        or os.environ.get("M365_TENANT_ID", "").strip()
        or os.environ.get("OUTLOOK_TENANT_ID", "").strip()
        or os.environ.get("TEAMS_TENANT_ID", "").strip()
    )
    if candidate and candidate.lower() != "common":
        return candidate
    try:
        cache_path = get_m365_token_cache_path()
        if cache_path.exists():
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            for acc in (data.get("Account") or {}).values():
                realm = (acc.get("realm") or "").strip()
                if realm and realm.lower() not in ("common", "organizations"):
                    return realm
    except Exception:
        pass
    return "organizations"


def build_admin_consent_url(
    *,
    client_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    scopes: Optional[Iterable[str]] = None,
    redirect_uri: str = M365_DEFAULT_ADMIN_CONSENT_REDIRECT_URI,
    use_default_scope: bool = False,
    state: Optional[str] = None,
) -> str:
    """Build the Entra v2 admin-consent URL for tenant onboarding.

    Scopes are fully qualified (``https://graph.microsoft.com/<Scope>``) as the
    v2 endpoint requires; ``use_default_scope=True`` sends
    ``https://graph.microsoft.com/.default`` instead, which grants exactly what
    the app registration declares. The redirect URI must be registered on the
    app; after consent the browser lands on it with
    ``admin_consent=True&tenant=<id>`` (a "connection refused" page there is
    expected and harmless).
    """
    cid = resolve_m365_client_id(client_id)
    tid = resolve_m365_tenant_id(tenant_id)
    if use_default_scope:
        scope_value = "https://graph.microsoft.com/.default"
    else:
        scope_list = list(scopes) if scopes is not None else list(M365_ALL_SCOPES)
        scope_value = " ".join(
            s if s.startswith("http") else f"https://graph.microsoft.com/{s}" for s in scope_list
        )
    params = {
        "client_id": cid,
        "scope": scope_value,
        "redirect_uri": redirect_uri,
    }
    if state:
        params["state"] = state
    return f"https://login.microsoftonline.com/{tid}/v2.0/adminconsent?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


def m365_granted_tier(app: Any, account: Any) -> Optional[str]:
    """Return the widest tier the cached ``account`` can obtain silently, or None."""
    for tier in M365_SCOPE_TIER_ORDER:
        try:
            result = app.acquire_token_silent(M365_SCOPE_TIERS[tier], account=account)
        except Exception:
            result = None
        if result and "access_token" in result:
            return tier
    return None


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


def list_msal_accounts() -> list[dict[str, Any]]:
    """Return a list of all M365 accounts currently stored in the MSAL cache."""
    try:
        app = get_msal_app()
        accounts = app.get_accounts()
        result = []
        for acc in accounts:
            username = acc.get("username") or acc.get("preferred_username") or ""
            home_account_id = acc.get("home_account_id") or ""
            name = acc.get("name") or username
            realm = acc.get("realm") or ""
            result.append({
                "username": username,
                "name": name,
                "home_account_id": home_account_id,
                "realm": realm,
                "account_object": acc,
            })
        return result
    except Exception:
        return []


def get_msal_account_by_identifier(account_identifier: Optional[str] = None) -> Optional[Any]:
    """Retrieve MSAL account object by username/email substring or home_account_id.

    If account_identifier is None or empty, returns the first/default account if available.
    """
    try:
        app = get_msal_app()
        accounts = app.get_accounts()
        if not accounts:
            return None
        if not account_identifier or not str(account_identifier).strip():
            return accounts[0]

        ident = str(account_identifier).strip().lower()
        for acc in accounts:
            username = (acc.get("username") or "").lower()
            name = (acc.get("name") or "").lower()
            home_id = (acc.get("home_account_id") or "").lower()
            if ident in username or ident in name or ident == home_id:
                return acc
        return accounts[0]
    except Exception:
        return None


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

    client_id_val = custom_client_id or M365_DEFAULT_CLIENT_ID
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
    "AADSTS65002": "Consent between this application and Microsoft Graph must be configured by the publisher; the requested scope is not exposed to third-party apps.",
    "AADSTS90094": "This permission set needs admin approval in your organization (\"Need admin approval\"). Ask a tenant administrator to open the admin-consent URL once; afterwards every user can sign in.",
    "AADSTS650052": "The app needs access to a service that your organization has not subscribed to or enabled.",
    "AADSTS500011": "The resource principal was not found in the tenant — the app has never been consented in this organization. Use the admin-consent URL.",
    "AADSTS7000218": "The app registration must allow public client flows (Authentication → Advanced settings → Allow public client flows = Yes).",
    "AADSTS70016": "The device code has not been confirmed yet or has expired. Open the verification page and enter the code.",
    "AADSTS700016": "Application ID (client_id) was not found in the directory.",
    "AADSTS90002": "Tenant ID was not found or is invalid.",
    "AADSTS50105": "The signed in user is not assigned to a role for the application in Azure Active Directory.",
    "AADSTS7000215": "Invalid client secret provided.",
}

# OAuth 2.0 error strings MSAL returns in ``result["error"]`` (device-code and
# refresh flows). Mapped to a category so callers can act, not just print.
OAUTH_ERROR_MAP = {
    "consent_required": ("consent", "Your organization requires admin approval for this sign-in. A tenant administrator must grant consent once using the admin-consent URL."),
    "interaction_required": ("consent", "Microsoft needs an interactive sign-in (consent or conditional access). Sign in again; if the prompt says \"Need admin approval\", a tenant administrator must consent first."),
    "invalid_grant": ("expired", "The cached sign-in is no longer valid (password change, revoked session, or expired refresh token). Sign in again."),
    "authorization_declined": ("declined", "The sign-in was declined in the browser."),
    "expired_token": ("expired", "The device code expired before it was confirmed. Start the sign-in again."),
    "authorization_pending": ("pending", "Waiting for the code to be entered in the browser."),
    "invalid_scope": ("config", "The app registration does not expose one of the requested permissions. Check the declared delegated permissions."),
    "access_denied": ("declined", "Access was denied by the user or by a conditional-access policy."),
    "invalid_client": ("config", "The client id is unknown to Microsoft Entra or the app is not configured as a public client."),
    "unauthorized_client": ("config", "The app is not authorized for the device-code flow. Enable \"Allow public client flows\" on the app registration."),
}

_AADSTS_CATEGORY = {
    "AADSTS65001": "consent",
    "AADSTS65002": "consent",
    "AADSTS90094": "consent",
    "AADSTS500011": "consent",
    "AADSTS650052": "config",
    "AADSTS7000218": "config",
    "AADSTS700016": "config",
    "AADSTS90002": "config",
    "AADSTS50011": "config",
    "AADSTS7000215": "config",
    "AADSTS50105": "declined",
    "AADSTS50076": "mfa",
    "AADSTS70016": "pending",
}

_AADSTS_CODE_RE = re.compile(r"AADSTS\d{4,7}")


@dataclass
class M365AuthError:
    """Structured view of an MSAL / Entra sign-in failure."""

    code: str = ""
    category: str = "unknown"  # consent | declined | expired | mfa | config | pending | unknown
    message: str = ""
    admin_consent_required: bool = False
    raw: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_code": self.code,
            "category": self.category,
            "message": self.message,
            "admin_consent_required": self.admin_consent_required,
        }


def classify_m365_auth_error(result_or_text: Any) -> M365AuthError:
    """Classify an MSAL result dict or raw error text.

    Recognises AADSTS codes (anywhere in the text) and OAuth error strings
    (``result["error"]``). Consent failures set ``admin_consent_required`` so
    callers can attach the admin-consent URL.
    """
    raw_text = ""
    oauth_error = ""
    if isinstance(result_or_text, dict):
        oauth_error = str(result_or_text.get("error") or "").strip()
        raw_text = str(result_or_text.get("error_description") or "") or json.dumps(result_or_text, default=str)
    elif result_or_text is not None:
        raw_text = str(result_or_text)

    code_match = _AADSTS_CODE_RE.search(raw_text)
    aadsts = code_match.group(0) if code_match else ""

    if aadsts and aadsts in AADSTS_ERROR_MAP:
        category = _AADSTS_CATEGORY.get(aadsts, "unknown")
        return M365AuthError(
            code=aadsts,
            category=category,
            message=AADSTS_ERROR_MAP[aadsts],
            admin_consent_required=(category == "consent"),
            raw=raw_text,
        )
    if oauth_error and oauth_error in OAUTH_ERROR_MAP:
        category, message = OAUTH_ERROR_MAP[oauth_error]
        return M365AuthError(
            code=oauth_error,
            category=category,
            message=message,
            admin_consent_required=(category == "consent"),
            raw=raw_text,
        )
    lowered = (raw_text + " " + oauth_error).lower()
    if "need admin approval" in lowered or "admin approval" in lowered or "consent" in lowered:
        return M365AuthError(
            code=aadsts or oauth_error or "consent_required",
            category="consent",
            message=OAUTH_ERROR_MAP["consent_required"][1],
            admin_consent_required=True,
            raw=raw_text,
        )
    return M365AuthError(
        code=aadsts or oauth_error,
        category="unknown",
        message=(raw_text or oauth_error or "Microsoft sign-in failed").strip()[:400],
        raw=raw_text,
    )


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
