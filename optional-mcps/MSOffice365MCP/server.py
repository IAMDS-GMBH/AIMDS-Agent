"""Microsoft 365 MCP Server (Outlook Mail & Calendar, Teams, OneDrive).

Provides access to Microsoft 365 services via MS Graph API with MSAL OAuth authentication,
auto-discovery, and admin role detection.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import quote

import httpx
import msal
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP Server
mcp = FastMCP("MSOffice365MCP")

# Constants
GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

# Consent tiers (AIS-286). Single source of truth is hermes_cli/m365_auth.py;
# the literals below are the standalone mirror for installs that run this
# server outside the Hermes checkout (catalog installs never receive code
# fixes, so the fallback branch is production code). A test pins equality.
#
#   SELF_CONSENT_SCOPES  tier 0 — every tenant member can consent themselves.
#                        Login default in dashboard, CLI and chat tool.
#   ORG_CONSENT_SCOPES   tier 1 — "Admin consent required": a tenant admin grants
#                        them once org-wide (m365_generate_admin_consent_url);
#                        afterwards acquire_token_silent hands them to every user.
#   ADMIN_SCOPES         tier 2 — directory-wide / org-wide write, admins only.
try:
    from hermes_cli.m365_auth import (
        M365_ADMIN_SCOPES as ADMIN_SCOPES,
        M365_ORG_CONSENT_SCOPES as ORG_CONSENT_SCOPES,
        M365_SELF_CONSENT_SCOPES as SELF_CONSENT_SCOPES,
    )
except ImportError:
    SELF_CONSENT_SCOPES = [
        "User.Read",
        "Mail.ReadWrite",
        "Mail.Send",
        "Calendars.ReadWrite",
        "Contacts.ReadWrite",
        "Files.ReadWrite.All",
    ]
    ORG_CONSENT_SCOPES = [
        "Mail.ReadWrite.Shared",
        "Mail.Send.Shared",
        "Calendars.ReadWrite.Shared",
        "Chat.ReadWrite",
        "Presence.Read",
        "OnlineMeetings.Read",
        "Tasks.ReadWrite",
    ]
    ADMIN_SCOPES = [
        "User.Read.All",
        "Directory.Read.All",
        "Sites.ReadWrite.All",
    ]

# BASE_SCOPES keeps its historical meaning "everything a regular user needs"
# (tiers 0+1); ALL_SCOPES adds the admin tier. LOGIN_SCOPES is what a fresh
# sign-in requests: tier 0 only, so non-admin users never hit the
# "Need admin approval" wall. Tier 1 arrives silently after org consent.
BASE_SCOPES = SELF_CONSENT_SCOPES + ORG_CONSENT_SCOPES
STANDARD_SCOPES = BASE_SCOPES
ALL_SCOPES = BASE_SCOPES + ADMIN_SCOPES
LOGIN_SCOPES = SELF_CONSENT_SCOPES

# Backwards-compatible alias: callers importing SCOPES directly get the full
# superset (the admin-consent URL grants everything at once).
SCOPES = ALL_SCOPES

SCOPE_TIERS = {"self": SELF_CONSENT_SCOPES, "standard": STANDARD_SCOPES, "admin": ALL_SCOPES}
SCOPE_TIER_ORDER = ("admin", "standard", "self")
_GRANTED_TIER_TTL_SECONDS = 600.0
# home_account_id -> (tier, monotonic timestamp). Without this cache every
# Graph call would pay two failing network redemptions before the working tier.
_GRANTED_TIER_CACHE: Dict[str, Tuple[str, float]] = {}

DEFAULT_CLIENT_ID = "41c29967-8ee6-4fac-b484-e87460272bda"  # IAMDS-owned multi-tenant app

ADMIN_ROLE_IDS = {
    "62e90394-69f5-4237-9190-012177145e10": "Global Administrator",
    "9b895ad3-a367-43b6-9916-24e03102d6b3": "Application Administrator",
    "158c4001-0a58-4720-8022-c8402e213b30": "Cloud Application Administrator",
}


def _get_timezone_name() -> str:
    tz = os.environ.get("HERMES_TIMEZONE") or os.environ.get("TIMEZONE")
    if tz:
        return tz
    try:
        from hermes_time import default_timezone_name
        return default_timezone_name()
    except Exception:
        return "Europe/Berlin"


def _format_timestamp_local(dt_value: Any) -> str:
    """Format an ISO timestamp or Graph API dateTime object into local/configured timezone string.

    Handles:
    - ISO strings ending with 'Z' or offset: e.g. '2026-07-28T10:21:46Z' -> '2026-07-28 12:21:46 CEST'
    - Dict object: e.g. {'dateTime': '2026-07-28T10:21:46.0000000', 'timeZone': 'UTC'}
    """
    if not dt_value:
        return ""

    raw_dt_str = ""
    source_tz_name = "UTC"

    if isinstance(dt_value, dict):
        raw_dt_str = str(dt_value.get("dateTime") or "")
        source_tz_name = str(dt_value.get("timeZone") or "UTC")
    elif isinstance(dt_value, str):
        raw_dt_str = dt_value
    else:
        return str(dt_value)

    if not raw_dt_str:
        return ""

    try:
        from datetime import datetime
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo  # type: ignore

        clean_str = raw_dt_str.replace("Z", "+00:00")
        if "." in clean_str:
            parts = clean_str.split(".")
            second_part = parts[1]
            tz_offset = ""
            for idx, char in enumerate(second_part):
                if char in ("+", "-", "Z"):
                    tz_offset = second_part[idx:]
                    second_part = second_part[:idx]
                    break
            second_part = second_part[:6]
            clean_str = f"{parts[0]}.{second_part}{tz_offset}"

        dt = datetime.fromisoformat(clean_str)

        if dt.tzinfo is None:
            try:
                dt = dt.replace(tzinfo=ZoneInfo(source_tz_name))
            except Exception:
                dt = dt.replace(tzinfo=ZoneInfo("UTC"))

        target_tz_name = _get_timezone_name()
        try:
            target_tz = ZoneInfo(target_tz_name)
        except Exception:
            target_tz = datetime.now().astimezone().tzinfo

        dt_local = dt.astimezone(target_tz)
        return dt_local.strftime("%Y-%m-%d %H:%M:%S %Z").strip()
    except Exception:
        return raw_dt_str


def _enrich_timestamps(obj: Any) -> Any:
    """Enrich Graph API dictionary objects with local timestamp fields (_local)."""
    if isinstance(obj, dict):
        time_fields = [
            "createdDateTime",
            "lastModifiedDateTime",
            "lastUpdatedDateTime",
            "receivedDateTime",
            "sentDateTime",
            "completedDateTime",
        ]
        for field in time_fields:
            if field in obj and f"{field}_local" not in obj:
                obj[f"{field}_local"] = _format_timestamp_local(obj.get(field))
        if "dueDateTime" in obj and obj.get("dueDateTime") and "dueDateTime_local" not in obj:
            obj["dueDateTime_local"] = _format_timestamp_local(obj.get("dueDateTime"))
    return obj


def _normalize_datetime_input(dt_str: Optional[str], default_tz: Optional[str] = None) -> Tuple[str, str]:
    """Normalize any user/LLM datetime input string into Graph API compatible format.

    Returns (clean_iso_str_without_offset, tz_name).

    Handles:
    - Naive ISO: '2026-07-29T17:00:00' or '2026-07-29 17:00' -> ('2026-07-29T17:00:00', 'Europe/Berlin')
    - ISO with Z: '2026-07-29T15:00:00Z' -> converts to local tz 'Europe/Berlin' -> ('2026-07-29T17:00:00', 'Europe/Berlin')
    - ISO with offset: '2026-07-29T17:00:00+02:00' -> converts to local tz -> ('2026-07-29T17:00:00', 'Europe/Berlin')
    - Date only: '2026-07-29' -> ('2026-07-29T00:00:00', 'Europe/Berlin')
    """
    if not dt_str or not str(dt_str).strip():
        return "", default_tz or _get_timezone_name()

    raw = str(dt_str).strip()
    target_tz_name = default_tz or _get_timezone_name()

    try:
        from datetime import datetime
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo  # type: ignore

        target_tz = ZoneInfo(target_tz_name)

        # Handle simple date-only string 'YYYY-MM-DD'
        if len(raw) == 10 and raw.count("-") == 2:
            raw = f"{raw}T00:00:00"

        raw_clean = raw.replace(" ", "T")
        raw_clean_z = raw_clean.replace("Z", "+00:00")

        # Trim fractional seconds if needed
        if "." in raw_clean_z:
            parts = raw_clean_z.split(".")
            second_part = parts[1]
            tz_offset = ""
            for idx, char in enumerate(second_part):
                if char in ("+", "-"):
                    tz_offset = second_part[idx:]
                    second_part = second_part[:idx]
                    break
            second_part = second_part[:6]
            raw_clean_z = f"{parts[0]}.{second_part}{tz_offset}"

        dt = datetime.fromisoformat(raw_clean_z)

        # Convert to target timezone
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=target_tz)
        else:
            dt = dt.astimezone(target_tz)

        clean_iso = dt.strftime("%Y-%m-%dT%H:%M:%S")
        return clean_iso, target_tz_name
    except Exception:
        # Fallback: strip Z and offsets naively if parsing fails
        clean_raw = raw.replace(" ", "T").split("+")[0].split("Z")[0]
        return clean_raw, target_tz_name


try:
    from hermes_cli.m365_auth import (
        build_admin_consent_url as _build_admin_consent_url,
        classify_m365_auth_error as _classify_auth_error,
        get_m365_token_cache_path as _get_token_cache_path,
        get_msal_app as _get_msal_app,
        m365_tier_for_endpoint as _tier_for_endpoint,
        resolve_m365_client_id as _resolve_client_id,
        resolve_m365_tenant_id as _resolve_tenant_id,
        save_msal_cache as _save_msal_cache,
        translate_aadsts_error as _translate_aadsts_error,
    )

    def _save_cache(app: msal.PublicClientApplication) -> None:
        """Persist the MSAL token cache atomically."""
        _save_msal_cache(app, cache_path=_get_token_cache_path())
except ImportError:
    def _translate_aadsts_error(err: str) -> str:
        return ""

    class _FallbackAuthError:
        def __init__(self, raw: Any):
            text = json.dumps(raw, default=str) if isinstance(raw, dict) else str(raw)
            oauth = str(raw.get("error") or "") if isinstance(raw, dict) else ""
            lowered = (text + " " + oauth).lower()
            self.raw = text
            self.code = oauth
            if "aadsts90094" in lowered or "aadsts65001" in lowered or "consent" in lowered or "admin approval" in lowered:
                self.category, self.admin_consent_required = "consent", True
                self.message = "Your organization requires admin approval for this sign-in. A tenant administrator must grant consent once using the admin-consent URL."
            elif oauth in ("authorization_declined", "access_denied"):
                self.category, self.admin_consent_required = "declined", False
                self.message = "The sign-in was declined in the browser."
            elif oauth in ("expired_token", "invalid_grant"):
                self.category, self.admin_consent_required = "expired", False
                self.message = "The sign-in expired. Start it again."
            else:
                self.category, self.admin_consent_required = "unknown", False
                self.message = text[:400]

        def to_dict(self) -> Dict[str, Any]:
            return {"error_code": self.code, "category": self.category, "message": self.message, "admin_consent_required": self.admin_consent_required}

    def _classify_auth_error(raw: Any) -> "_FallbackAuthError":
        return _FallbackAuthError(raw)

    def _resolve_client_id(client_id: Optional[str] = None) -> str:
        return (
            (client_id or "").strip()
            or os.environ.get("M365_CLIENT_ID", "").strip()
            or os.environ.get("OUTLOOK_CLIENT_ID", "").strip()
            or os.environ.get("TEAMS_CLIENT_ID", "").strip()
            or DEFAULT_CLIENT_ID
        )

    def _resolve_tenant_id(tenant_id: Optional[str] = None) -> str:
        candidate = (
            (tenant_id or "").strip()
            or os.environ.get("M365_TENANT_ID", "").strip()
            or os.environ.get("OUTLOOK_TENANT_ID", "").strip()
            or os.environ.get("TEAMS_TENANT_ID", "").strip()
        )
        if candidate and candidate.lower() != "common":
            return candidate
        try:
            cache_path = _get_token_cache_path()
            if cache_path.exists():
                data = json.loads(cache_path.read_text(encoding="utf-8"))
                for acc in (data.get("Account") or {}).values():
                    realm = (acc.get("realm") or "").strip()
                    if realm and realm.lower() not in ("common", "organizations"):
                        return realm
        except Exception:
            pass
        return "organizations"

    def _build_admin_consent_url(
        *,
        client_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        scopes: Optional[List[str]] = None,
        redirect_uri: str = "http://localhost:8400",
        use_default_scope: bool = False,
        state: Optional[str] = None,
    ) -> str:
        import urllib.parse

        if use_default_scope:
            scope_value = "https://graph.microsoft.com/.default"
        else:
            scope_value = " ".join(
                sc if sc.startswith("http") else f"https://graph.microsoft.com/{sc}"
                for sc in (scopes if scopes is not None else ALL_SCOPES)
            )
        params = {"client_id": _resolve_client_id(client_id), "scope": scope_value, "redirect_uri": redirect_uri}
        if state:
            params["state"] = state
        return (
            f"https://login.microsoftonline.com/{_resolve_tenant_id(tenant_id)}/v2.0/adminconsent?"
            + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        )

    def _tier_for_endpoint(endpoint: str) -> str:
        ep = endpoint or ""
        if ep.startswith("/users/") and ep.count("/") >= 3:
            return "standard"
        for marker, tier in (("/users", "admin"), ("/sites", "admin"), ("/chats", "standard"), ("/presence", "standard"), ("/onlineMeetings", "standard"), ("/todo", "standard"), ("/communications", "standard")):
            if marker in ep:
                return tier
        return "self"

    def _get_token_cache_path() -> Path:
        hermes_home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
        cache_dir = Path(hermes_home)
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / "m365_token_cache.bin"


    def _get_msal_app() -> msal.PublicClientApplication:
        # 1. Custom app registration client_id / tenant_id if specified in env
        custom_client_id = (
            os.environ.get("M365_CLIENT_ID")
            or os.environ.get("OUTLOOK_CLIENT_ID")
            or os.environ.get("TEAMS_CLIENT_ID")
        )
        custom_tenant_id = (
            os.environ.get("M365_TENANT_ID")
            or os.environ.get("OUTLOOK_TENANT_ID")
            or os.environ.get("TEAMS_TENANT_ID")
        )

        # Defaults: standard multi-tenant client app if no custom app registration provided
        client_id = custom_client_id or DEFAULT_CLIENT_ID
        tenant_id = custom_tenant_id or "organizations"

        if tenant_id == "common":
            tenant_id = "organizations"

        cache = msal.SerializableTokenCache()
        cache_path = _get_token_cache_path()
        if cache_path.exists():
            try:
                cache_data = json.loads(cache_path.read_text(encoding="utf-8"))
                accounts = cache_data.get("Account", {})
                if accounts and tenant_id == "organizations":
                    first_acc = next(iter(accounts.values()))
                    if first_acc.get("realm"):
                        tenant_id = first_acc["realm"]
                cache.deserialize(json.dumps(cache_data))
            except Exception:
                pass

        authority = f"https://login.microsoftonline.com/{tenant_id}"

        app = msal.PublicClientApplication(
            client_id=client_id,
            authority=authority,
            token_cache=cache,
        )
        return app


    def _save_cache(app: msal.PublicClientApplication) -> None:
        """Persist the MSAL token cache atomically.

        Standalone mirror of ``hermes_cli.m365_auth.save_msal_cache``. This
        branch runs precisely because that module could not be imported, so it
        must not reach back into it.
        """
        cache = getattr(app, "token_cache", None)
        if not cache or not getattr(cache, "has_state_changed", False):
            return
        cache_path = _get_token_cache_path()
        tmp_path = cache_path.with_name(f"{cache_path.name}.tmp.{os.getpid()}")
        tmp_path.write_text(cache.serialize(), encoding="utf-8")
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, cache_path)


def _acquire_silent_by_tier(app: msal.PublicClientApplication, acc: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Silently acquire a token for ``acc``, widest consent tier first.

    Order: the tier that worked last time for this account (cached for
    ``_GRANTED_TIER_TTL_SECONDS``), then admin → standard → self. Once a tenant
    admin has consented org-wide, ``admin``/``standard`` succeed for every user
    without any prompt; before that, ``self`` keeps mail/calendar/files working.
    """
    key = str(acc.get("home_account_id") or acc.get("username") or "")
    now = time.monotonic()
    order: List[str] = list(SCOPE_TIER_ORDER)
    cached = _GRANTED_TIER_CACHE.get(key)
    if cached and now - cached[1] < _GRANTED_TIER_TTL_SECONDS and cached[0] in order:
        order.remove(cached[0])
        order.insert(0, cached[0])
    for tier in order:
        result = app.acquire_token_silent(SCOPE_TIERS[tier], account=acc)
        if result and "access_token" in result:
            _GRANTED_TIER_CACHE[key] = (tier, now)
            return result, tier
    _GRANTED_TIER_CACHE.pop(key, None)
    return None, None


def _granted_tier_for_account(app: msal.PublicClientApplication, acc: Dict[str, Any]) -> Optional[str]:
    """Return the widest tier ``acc`` can obtain silently (None = needs sign-in)."""
    _, tier = _acquire_silent_by_tier(app, acc)
    return tier


def _login_scopes_from_argv() -> List[str]:
    """CLI ``--login`` scope selection: ``--admin`` → all, ``--standard`` → tiers 0+1, else tier 0."""
    if "--admin" in sys.argv or bool(os.environ.get("M365_REQUEST_ADMIN_SCOPES")):
        return ALL_SCOPES
    if "--standard" in sys.argv:
        return STANDARD_SCOPES
    return LOGIN_SCOPES


def _get_access_token(account: Optional[str] = None) -> str:
    # 1. Try MSAL token cache with silent refresh first (single source of truth)
    app = _get_msal_app()
    accounts = app.get_accounts()

    if accounts:
        target_accounts = accounts
        if account and str(account).strip():
            ident = str(account).strip().lower()
            matched = [
                a for a in accounts
                if ident in (a.get("username") or "").lower()
                or ident in (a.get("name") or "").lower()
                or ident == (a.get("home_account_id") or "").lower()
            ]
            if matched:
                target_accounts = matched

        for acc in target_accounts:
            result, _tier = _acquire_silent_by_tier(app, acc)
            if result and "access_token" in result:
                _save_cache(app)
                return result["access_token"]

    # 2. Legacy fallback: explicit M365_ACCESS_TOKEN env var if set and no cached MSAL account exists
    direct_token = os.environ.get("M365_ACCESS_TOKEN")
    if direct_token:
        return direct_token

    # 2. Check if running in interactive --login CLI mode
    if "--login" in sys.argv:
        login_scopes = _login_scopes_from_argv()
        print("\n[M365 OAuth] Initiating interactive sign-in...", file=sys.stderr)
        try:
            result = app.acquire_token_interactive(scopes=login_scopes, port=8400)
            if "access_token" in result:
                _save_cache(app)
                print("[M365 OAuth] Sign-in successful! Token cached in ~/.hermes/m365_token_cache.bin", file=sys.stderr)
                return result["access_token"]
        except Exception as err:
            print(f"[M365 OAuth] Interactive loopback failed ({err}), trying device code flow...", file=sys.stderr)

        flow = app.initiate_device_flow(scopes=login_scopes)
        if "user_code" in flow:
            print(f"\n[M365 OAuth] Please open {flow['verification_uri']} and enter code: {flow['user_code']}\n", file=sys.stderr)
            result = app.acquire_token_by_device_flow(flow)
            if "access_token" in result:
                _save_cache(app)
                print("[M365 OAuth] Device code sign-in successful! Token cached.", file=sys.stderr)
                return result["access_token"]
            classified = _classify_auth_error(result)
            print(f"[M365 OAuth] Sign-in failed: {classified.message}", file=sys.stderr)
            if classified.admin_consent_required:
                print(f"[M365 OAuth] Tenant admin consent URL: {_build_admin_consent_url()}", file=sys.stderr)

    # 3. Running inside stdio MCP without cached token -> Return clear actionable error rather than blocking stdio!
    raise RuntimeError(
        "M365 authentication required. Call the m365_initiate_login tool "
        "(then m365_complete_login with the returned flow_data) to sign in "
        "interactively, or tell the user to open Hermes: Settings -> Providers "
        "-> Accounts -> 'Microsoft 365 (OAuth)' -> Connect, and follow the "
        "device-code instructions there. The default sign-in requests only "
        "self-consentable scopes; Teams chat, presence, shared mailboxes and "
        "To Do need a one-time tenant-admin consent "
        "(m365_generate_admin_consent_url)."
    )


def _consent_hint_for(endpoint: str) -> str:
    """Explain a Graph 403 in terms of the consent tier the endpoint needs."""
    tier = _tier_for_endpoint(endpoint)
    if tier == "admin":
        return (
            " (needs the admin tier: directory-wide read / SharePoint. Only a tenant "
            "administrator can sign in with scope_tier='admin' via m365_initiate_login, "
            "or grant it org-wide once: " + _build_admin_consent_url() + ")"
        )
    if tier == "standard":
        return (
            " (needs org-wide admin consent for Teams chat / presence / online meetings / "
            "shared mailboxes / To Do. Ask a tenant administrator to open this URL once; "
            "afterwards every user gets these permissions silently, no re-login needed: "
            + _build_admin_consent_url() + ")"
        )
    return " (the signed-in account lacks a permission for this call; sign in again via m365_initiate_login)"


def _graph_request(
    method: str,
    endpoint: str,
    json_data: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    extra_headers: Optional[Dict[str, str]] = None,
    account: Optional[str] = None,
) -> Any:
    token = _get_access_token(account=account)
    tz_name = _get_timezone_name()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Prefer": f'outlook.timezone="{tz_name}"',
    }
    if extra_headers:
        headers.update(extra_headers)

    effective_endpoint = endpoint
    if account and str(account).strip() and "@" in str(account):
        clean_acc = str(account).strip()
        if effective_endpoint.startswith("/me/"):
            effective_endpoint = f"/users/{clean_acc}/" + effective_endpoint[4:]
        elif effective_endpoint == "/me":
            effective_endpoint = f"/users/{clean_acc}"

    url = f"{GRAPH_API_BASE}{effective_endpoint}" if not effective_endpoint.startswith("http") else effective_endpoint

    with httpx.Client(timeout=30.0) as client:
        response = client.request(method, url, headers=headers, json=json_data, params=params)
        if response.status_code == 204:
            return {"success": True}
        if response.is_error:
            hint = ""
            if response.status_code == 403 or "Authorization_RequestDenied" in response.text:
                hint = _consent_hint_for(effective_endpoint)
            aadsts_hint = _translate_aadsts_error(response.text)
            raise RuntimeError(f"MS Graph API Error [{response.status_code}]: {response.text}{hint}{aadsts_hint}")
        return response.json()


def _graph_upload(method: str, endpoint_or_url: str, data: bytes, content_type: str) -> Any:
    """Like _graph_request but for raw binary bodies (file uploads), not JSON."""
    token = _get_access_token()
    headers = {
        "Authorization": token if token.lower().startswith("bearer ") else f"Bearer {token}",
        "Content-Type": content_type,
    }
    url = (
        endpoint_or_url
        if endpoint_or_url.startswith("http")
        else f"{GRAPH_API_BASE}{endpoint_or_url}"
    )
    with httpx.Client(timeout=120.0) as client:
        response = client.request(method, url, headers=headers, content=data)
        if response.is_error:
            raise RuntimeError(f"MS Graph API Error [{response.status_code}]: {response.text}")
        if response.status_code == 204 or not response.content:
            return {"success": True}
        return response.json()


def _graph_download_bytes(endpoint_or_url: str, account: Optional[str] = None) -> bytes:
    """Download raw binary bytes from MS Graph API (e.g. attachment content or OneDrive file)."""
    token = _get_access_token(account=account)
    headers = {
        "Authorization": token if token.lower().startswith("bearer ") else f"Bearer {token}",
    }
    url = (
        endpoint_or_url
        if endpoint_or_url.startswith("http")
        else f"{GRAPH_API_BASE}{endpoint_or_url}"
    )
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        response = client.get(url, headers=headers)
        if response.is_error:
            raise RuntimeError(f"MS Graph API Download Error [{response.status_code}]: {response.text}")
        return response.content


# Simple (single-request) OneDrive upload only works up to this size; larger
# files must go through a chunked upload session instead (see
# https://learn.microsoft.com/graph/api/driveitem-createuploadsession).
_ONEDRIVE_SIMPLE_UPLOAD_MAX_BYTES = 4 * 1024 * 1024
_ONEDRIVE_UPLOAD_CHUNK_SIZE = 5 * 1024 * 1024  # must be a multiple of 320 KiB
_ONEDRIVE_ATTACHMENTS_FOLDER = "HermesAttachments"

# MS Graph's sendMail only accepts attachments inlined as base64 in the JSON
# payload -- there is no separate upload-session path for plain sendMail (only
# for draft messages), so anything bigger than this needs OneDrive + a shared
# link instead of a direct attachment.
_MAIL_INLINE_ATTACHMENT_MAX_BYTES = 3 * 1024 * 1024


def _normalize_attachment_list(attachments: Union[None, str, List[Any]]) -> List[str]:
    """Normalize flexible attachment argument formats (str, list, JSON list str, comma-separated str) to a clean List[str]."""
    if not attachments:
        return []
    if isinstance(attachments, str):
        s = attachments.strip()
        if not s:
            return []
        if s.startswith("[") and s.endswith("]"):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if item]
            except Exception:
                pass
        if "," in s:
            return [p.strip() for p in s.split(",") if p.strip()]
        return [s]
    if isinstance(attachments, list):
        res = []
        for item in attachments:
            if isinstance(item, str):
                res.extend(_normalize_attachment_list(item))
            elif item is not None:
                res.append(str(item))
        return res
    return []


def _resolve_attachment_path(file_path: str) -> Path:
    clean_str = str(file_path).strip().strip("'\"")
    path = Path(clean_str).expanduser()
    if path.is_file():
        return path.resolve()

    # Try relative to user terminal/workspace CWD
    cwd = Path(os.getenv("TERMINAL_CWD") or os.getcwd()).resolve()
    cand_cwd = (cwd / clean_str).resolve()
    if cand_cwd.is_file():
        return cand_cwd

    # Try relative to Vault directories
    vault_env = os.getenv("HERMES_VAULT_PATH") or os.getenv("VAULT_PATH")
    vault_candidates = []
    if vault_env:
        vault_candidates.append(Path(vault_env).expanduser().resolve())
    vault_candidates.append(Path("~/Documents/AIMDS-Suite-Vault").expanduser().resolve())
    vault_candidates.append(Path("~/.hermes/vault").expanduser().resolve())

    for v in vault_candidates:
        cand_v = (v / clean_str).resolve()
        if cand_v.is_file():
            return cand_v

    raise ValueError(f"Attachment file not found: {file_path} (checked {path}, {cand_cwd})")


def _resolve_save_path(
    save_path: Optional[str],
    default_filename: str,
    subfolder: str = "m365_downloads",
) -> Path:
    """Resolve destination path for downloaded files, defaulting to AIMDS Suite Vault if present."""
    if save_path:
        out_file = Path(save_path).expanduser().resolve()
        out_file.parent.mkdir(parents=True, exist_ok=True)
        return out_file

    vault_env = os.getenv("HERMES_VAULT_PATH") or os.getenv("VAULT_PATH")
    candidate_vaults = []
    if vault_env:
        candidate_vaults.append(Path(vault_env).expanduser().resolve())

    default_aimds_vault = Path("~/Documents/AIMDS-Suite-Vault").expanduser().resolve()
    candidate_vaults.append(default_aimds_vault)

    hermes_home_vault = Path("~/.hermes/vault").expanduser().resolve()
    candidate_vaults.append(hermes_home_vault)

    for v in candidate_vaults:
        if v.exists() and v.is_dir():
            target_dir = v / subfolder
            target_dir.mkdir(parents=True, exist_ok=True)
            return target_dir / default_filename

    cwd = Path(os.getenv("TERMINAL_CWD") or os.getcwd()).resolve()
    target_dir = cwd / subfolder
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / default_filename


def _enrich_teams_message(
    msg: Dict[str, Any],
    chat_id: Optional[str] = None,
    team_id: Optional[str] = None,
    channel_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Enrich a Teams message with local timestamps and an attachments_summary."""
    if not isinstance(msg, dict):
        return msg

    _enrich_timestamps(msg)

    attachments_summary = []

    # 1. File & card attachments
    raw_atts = msg.get("attachments")
    if isinstance(raw_atts, list):
        for att in raw_atts:
            if isinstance(att, dict):
                content_type = att.get("contentType") or ""
                name = att.get("name") or att.get("id") or "Attachment"
                content_url = att.get("contentUrl") or ""
                attachments_summary.append({
                    "id": att.get("id"),
                    "name": name,
                    "contentType": content_type,
                    "contentUrl": content_url,
                    "type": "file_reference" if content_url or "reference" in content_type else "attachment",
                })

    # 2. Inline hosted contents (images in body)
    body_obj = msg.get("body") or {}
    body_content = body_obj.get("content") or "" if isinstance(body_obj, dict) else ""
    if "hostedContents" in body_content or "<img" in body_content:
        import re
        hc_matches = re.findall(r'hostedContents/([a-zA-Z0-9_-]+)/\$value', body_content)
        for hc_id in hc_matches:
            if not any(a.get("id") == hc_id or a.get("hosted_content_id") == hc_id for a in attachments_summary):
                attachments_summary.append({
                    "id": hc_id,
                    "name": f"inline_image_{hc_id[:8]}.png",
                    "type": "hosted_content",
                    "hosted_content_id": hc_id,
                })

    if attachments_summary:
        msg["attachments_summary"] = attachments_summary
        msg["has_attachments"] = True

    return msg


def _upload_file_to_onedrive(file_path: str, folder: str = _ONEDRIVE_ATTACHMENTS_FOLDER) -> Dict[str, Any]:
    """Upload a local file to the signed-in user's OneDrive and return the created driveItem
    (id, name, webUrl, ...), used as the basis for a Teams chat file attachment reference."""
    path = _resolve_attachment_path(file_path)
    file_size = path.stat().st_size
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    safe_folder = quote(folder.strip("/"), safe="")
    safe_filename = quote(path.name, safe="")
    item_path = f"/me/drive/root:/{safe_folder}/{safe_filename}:"

    if file_size <= _ONEDRIVE_SIMPLE_UPLOAD_MAX_BYTES:
        return _graph_upload("PUT", f"{item_path}/content", path.read_bytes(), content_type)

    session = _graph_request(
        "POST",
        f"{item_path}/createUploadSession",
        json_data={"item": {"@microsoft.graph.conflictBehavior": "rename", "name": path.name}},
    )
    upload_url = session.get("uploadUrl")
    if not upload_url:
        raise RuntimeError(f"Failed to create OneDrive upload session for '{path.name}': {session}")

    result: Optional[Dict[str, Any]] = None
    with path.open("rb") as fh, httpx.Client(timeout=120.0) as client:
        offset = 0
        while offset < file_size:
            chunk = fh.read(_ONEDRIVE_UPLOAD_CHUNK_SIZE)
            chunk_len = len(chunk)
            end = offset + chunk_len - 1
            resp = client.put(
                upload_url,
                headers={
                    "Content-Length": str(chunk_len),
                    "Content-Range": f"bytes {offset}-{end}/{file_size}",
                },
                content=chunk,
            )
            if resp.is_error:
                raise RuntimeError(
                    f"OneDrive chunked upload for '{path.name}' failed [{resp.status_code}]: {resp.text}"
                )
            offset += chunk_len
            if resp.status_code in (200, 201) and resp.content:
                result = resp.json()
    if result is None:
        raise RuntimeError(f"OneDrive chunked upload for '{path.name}' did not complete")
    return result


def _build_mail_attachment(file_path: str) -> Dict[str, Any]:
    """Build a MS Graph fileAttachment (inline base64) for m365_send_email."""
    path = _resolve_attachment_path(file_path)
    file_size = path.stat().st_size
    if file_size > _MAIL_INLINE_ATTACHMENT_MAX_BYTES:
        raise ValueError(
            f"Attachment '{path.name}' is {file_size / (1024 * 1024):.1f} MB, over the "
            f"{_MAIL_INLINE_ATTACHMENT_MAX_BYTES / (1024 * 1024):.0f} MB limit m365_send_email can "
            "inline directly. Upload it with m365_list_drive_files/OneDrive instead and share the "
            "link in the email body."
        )
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return {
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": path.name,
        "contentType": content_type,
        "contentBytes": base64.b64encode(path.read_bytes()).decode("ascii"),
    }


def _build_teams_attachments(file_paths: List[str]) -> Tuple[List[Dict[str, Any]], str]:
    """Upload local files to OneDrive and build the Teams chat-message attachment
    payload plus the matching `<attachment id="...">` tags to embed in the HTML body."""
    attachments: List[Dict[str, Any]] = []
    tags: List[str] = []
    for file_path in file_paths:
        item = _upload_file_to_onedrive(file_path)
        attachment_id = str(uuid.uuid4())
        attachments.append(
            {
                "id": attachment_id,
                "contentType": "reference",
                "contentUrl": item.get("webUrl"),
                "name": item.get("name") or Path(file_path).name,
            }
        )
        tags.append(f'<attachment id="{attachment_id}"></attachment>')
    return attachments, "".join(tags)


# ─── Tools ───────────────────────────────────────────────────────────────────


@mcp.tool()
def m365_list_accounts() -> str:
    """List all connected M365 accounts in the MSAL cache.

    Returns a JSON string listing all accounts with username, display name, and default indicator.
    """
    try:
        app = _get_msal_app()
        accounts = app.get_accounts()
        if not accounts:
            return json.dumps({
                "connected": False,
                "accounts": [],
                "message": "No M365 accounts connected. Use device flow to sign in.",
            })
        acc_list = []
        for idx, acc in enumerate(accounts):
            username = acc.get("username") or acc.get("preferred_username") or ""
            name = acc.get("name") or username
            acc_list.append({
                "index": idx,
                "username": username,
                "name": name,
                "home_account_id": acc.get("home_account_id") or "",
                "is_default": idx == 0,
            })
        return json.dumps({
            "connected": True,
            "count": len(acc_list),
            "accounts": acc_list,
        }, indent=2)
    except Exception as e:
        return json.dumps({"connected": False, "error": str(e)})


@mcp.tool()
def m365_generate_admin_consent_url(
    redirect_uri: str = "http://localhost:8400",
    use_default_scope: bool = False,
) -> Dict[str, Any]:
    """Generate the tenant-onboarding (admin consent) URL for the Microsoft 365 app.

    A tenant administrator opens the URL once and approves; afterwards every user
    of that organization can sign in with the default scopes and silently receives
    the org-consent tier (Teams chat, presence, online meetings, shared mailboxes,
    To Do) without re-authenticating. Works with the built-in IAMDS multi-tenant
    app — M365_CLIENT_ID is optional.

    Args:
        redirect_uri: Must be registered on the app registration. After consent
            the browser lands there with `admin_consent=True&tenant=<id>`; a
            "connection refused" page at that point is expected and harmless.
        use_default_scope: Request `https://graph.microsoft.com/.default`
            (exactly the permissions declared on the app registration) instead
            of the explicit scope list.
    """
    client_id = _resolve_client_id()
    tenant_id = _resolve_tenant_id()
    consent_url = _build_admin_consent_url(
        client_id=client_id,
        tenant_id=tenant_id,
        scopes=ALL_SCOPES,
        redirect_uri=redirect_uri,
        use_default_scope=use_default_scope,
    )
    return {
        "success": True,
        "client_id": client_id,
        "tenant_id": tenant_id,
        "scopes": ["https://graph.microsoft.com/.default"] if use_default_scope else list(ALL_SCOPES),
        "admin_consent_url": consent_url,
        "instructions": (
            "Send this URL to a tenant administrator (Global Administrator, Application "
            "Administrator or Cloud Application Administrator). They open it, sign in and "
            "click Accept once for the whole organization. The redirect afterwards may show "
            "a 'connection refused' page — that is expected. No user has to sign in again: "
            "the next Microsoft 365 call picks up the new permissions automatically.\n\n"
            f"{consent_url}"
        ),
    }


@mcp.tool()
def m365_initiate_login(request_admin_scopes: bool = False, scope_tier: Optional[str] = None) -> Dict[str, Any]:
    """Start the Microsoft 365 sign-in (device code flow) from Hermes.

    Args:
        scope_tier: Which permission set to request. Default "self" requests only
            scopes every tenant member can consent to themselves (mail, calendar,
            contacts, files) — this is the right choice for everyone, including
            admins, because org-level permissions arrive silently after a one-time
            tenant admin consent (m365_generate_admin_consent_url). "standard" adds
            Teams chat / presence / online meetings / shared mailboxes / To Do and
            shows "Need admin approval" to non-admins. "admin" adds directory-wide
            user search and SharePoint; only tenant administrators can complete it.
        request_admin_scopes: Legacy alias for scope_tier="admin".
    """
    tier = (scope_tier or "").strip().lower() or ("admin" if request_admin_scopes else "self")
    if tier not in SCOPE_TIERS:
        return {"error": f"Unknown scope_tier '{scope_tier}'. Use one of: self, standard, admin."}
    app = _get_msal_app()
    scopes = SCOPE_TIERS[tier]
    flow = app.initiate_device_flow(scopes=scopes)
    if "user_code" in flow:
        return {
            "status": "pending",
            "user_code": flow["user_code"],
            # Deprecated alias for `user_code`. It collided with the unrelated
            # `flow_data["device_code"]` secret and got callers passing the
            # wrong value to m365_complete_login. Drop after one release.
            "device_code": flow["user_code"],
            "verification_url": flow["verification_uri"],
            "requested_tier": tier,
            "requested_admin_scopes": tier == "admin",
            "message": (
                f"Please open {flow['verification_uri']} in your browser and enter the code: "
                f"**{flow['user_code']}**\n"
                "Once completed, call `m365_complete_login` with the `flow_data` object from "
                "this response passed back unchanged (not the code above), or call any M365 "
                "tool to verify login."
            ),
            "flow_data": flow,
        }
    classified = _classify_auth_error(flow)
    return {"error": f"Failed to initiate device flow: {classified.message}", **classified.to_dict(), "details": flow}


@mcp.tool()
def m365_complete_login(flow_data: Dict[str, Any]) -> Dict[str, Any]:
    """Complete the Microsoft 365 sign-in after the user entered the code in the browser.

    Args:
        flow_data: The `flow_data` object returned by `m365_initiate_login`,
            passed back unchanged. This is the whole MSAL device-flow dict, not
            the short code shown to the user.

    On success the result reports `granted_tier` (self | standard | admin). If it
    is "self", Teams chat / presence / shared mailboxes / To Do still need a
    one-time tenant-admin consent — `admin_consent_hint` carries the URL to hand
    to an administrator. Users never have to sign in again after that consent.
    """
    app = _get_msal_app()
    result = app.acquire_token_by_device_flow(flow_data)
    if "access_token" in result:
        _save_cache(app)
        username = result.get("id_token_claims", {}).get("preferred_username")
        granted_tier: Optional[str] = None
        try:
            accounts = app.get_accounts(username=username) if username else app.get_accounts()
            if accounts:
                granted_tier = _granted_tier_for_account(app, accounts[0])
        except Exception:
            granted_tier = None
        payload: Dict[str, Any] = {
            "success": True,
            "message": "Sign-in successful! Token cached in ~/.hermes/m365_token_cache.bin",
            "account": username,
            "granted_tier": granted_tier,
        }
        if granted_tier in (None, "self"):
            payload["admin_consent_hint"] = {
                "message": (
                    "Mail, calendar, contacts and files work now. Teams chat, presence, online "
                    "meetings, shared mailboxes and To Do need a one-time tenant-admin consent; "
                    "share this URL with an administrator. No re-login is needed afterwards."
                ),
                "admin_consent_url": _build_admin_consent_url(),
            }
        return payload
    classified = _classify_auth_error(result)
    payload = {
        "error": f"Sign-in incomplete or failed: {classified.message}",
        **classified.to_dict(),
        "details": result,
    }
    if classified.admin_consent_required:
        payload["admin_consent_url"] = _build_admin_consent_url()
        payload["next_step"] = (
            "Ask a tenant administrator to open admin_consent_url once, then run "
            "m365_initiate_login again (default scope_tier)."
        )
    return payload


@mcp.tool()
def m365_get_user_profile(account: Optional[str] = None) -> Dict[str, Any]:
    """Get the current authenticated user profile from Microsoft 365.

    Args:
        account: Optional account username, email, or account ID to use.
    """
    return _graph_request("GET", "/me", account=account)


@mcp.tool()
def m365_check_admin_status() -> Dict[str, Any]:
    """Check if the authenticated M365 user has Tenant/Directory Admin permissions.

    Requires the admin tier (m365_initiate_login(scope_tier="admin")) -- if the
    current sign-in has a lower tier, this returns a hint instead of a raw
    Graph permission error.
    """
    try:
        member_of = _graph_request("GET", "/me/memberOf")
    except RuntimeError as err:
        if "403" in str(err) or "InsufficientPrivileges" in str(err) or "Authorization_RequestDenied" in str(err):
            return {
                "error": "Checking admin status requires elevated (admin) scopes.",
                "recommendation": (
                    "Call m365_initiate_login(scope_tier='admin') and re-consent "
                    "(only meaningful if this account actually has tenant admin rights). "
                    "Legacy: request_admin_scopes=True."
                ),
            }
        raise
    roles = []
    is_admin = False

    for item in member_of.get("value", []):
        role_id = item.get("roleTemplateId") or item.get("id")
        display_name = item.get("displayName")
        if role_id in ADMIN_ROLE_IDS or display_name in ADMIN_ROLE_IDS.values():
            is_admin = True
            roles.append(display_name or ADMIN_ROLE_IDS.get(role_id, "Admin Role"))

    return {
        "is_admin": is_admin,
        "admin_roles": roles,
        "recommendation": "Admin consent can be granted for the tenant if needed." if is_admin else "User is a standard tenant member.",
    }


@mcp.tool()
def m365_send_email(
    to: List[str],
    subject: str,
    body: str,
    is_html: bool = True,
    save_to_sent_items: bool = True,
    attachments: Optional[List[str]] = None,
    account: Optional[str] = None,
) -> Dict[str, Any]:
    """Send an email using Outlook Mail. Ensures saveToSentItems is respected.

    Args:
        to: Recipient email addresses.
        subject: Email subject.
        body: Message content (HTML by default; plain text is auto-wrapped into
            paragraphs when is_html=True so line breaks render correctly in Outlook).
        is_html: 'True' (default) sends as HTML so formatting, line breaks, and
            signatures render correctly. Only set 'False' for plain-text-only mail.
        save_to_sent_items: Whether to keep a copy in Sent Items.
        attachments: Optional local file paths to attach. Each file is inlined as
            base64, so the combined size must stay under ~3 MB -- for larger files,
            upload to OneDrive first (m365_list_drive_files) and share the link instead.
        account: Optional M365 account username, email, or ID to send from.
    """
    recipients = [{"emailAddress": {"address": addr.strip()}} for addr in to]
    final_body = body
    if is_html:
        import re
        if not re.search(r"<(p|div|br|ul|ol|li|h[1-6])\b", body, re.IGNORECASE):
            paragraphs = body.split("\n\n")
            formatted_p = []
            for p in paragraphs:
                p_clean = p.strip().replace("\n", "<br/>")
                if p_clean:
                    formatted_p.append(f"<p>{p_clean}</p>")
            final_body = "".join(formatted_p) if formatted_p else body
    content_type = "HTML" if is_html else "Text"

    message: Dict[str, Any] = {
        "subject": subject,
        "body": {
            "contentType": content_type,
            "content": final_body,
        },
        "toRecipients": recipients,
    }
    if account and str(account).strip() and "@" in str(account):
        message["from"] = {"emailAddress": {"address": str(account).strip()}}
    norm_attachments = _normalize_attachment_list(attachments)
    if norm_attachments:
        message["attachments"] = [_build_mail_attachment(path) for path in norm_attachments]

    payload = {
        "message": message,
        "saveToSentItems": save_to_sent_items,
    }
    return _graph_request("POST", "/me/sendMail", json_data=payload, account=account)


@mcp.tool()
def m365_list_emails(
    top: int = 10,
    search: Optional[str] = None,
    folder: str = "inbox",
    account: Optional[str] = None,
) -> Dict[str, Any]:
    """List recent emails from an Outlook mail folder.

    Args:
        top: Max number of messages to return (capped at 50).
        search: Optional free-text search filter.
        folder: Well-known folder name, e.g. 'inbox' (default) or 'sentitems'
            (use 'sentitems' to inspect the user's own sent mail, for example
            to derive their email signature/closing and writing style).
        account: Optional M365 account username, email, or ID.
    """
    params = {"$top": min(top, 50), "$select": "id,subject,from,receivedDateTime,isRead,bodyPreview"}
    if search:
        params["$search"] = f'"{search}"'
    folder_segment = (folder or "inbox").strip() or "inbox"
    endpoint = "/me/messages" if folder_segment == "inbox" else f"/me/mailFolders/{folder_segment}/messages"
    res = _graph_request("GET", endpoint, params=params, account=account)
    if isinstance(res, dict) and "value" in res and isinstance(res["value"], list):
        for msg in res["value"]:
            if isinstance(msg, dict):
                _enrich_timestamps(msg)
    return res


@mcp.tool()
def m365_get_email(message_id: str, account: Optional[str] = None) -> Dict[str, Any]:
    """Get full details of a specific Outlook email message, including attachment summary if attachments are present."""
    msg = _graph_request("GET", f"/me/messages/{message_id}", account=account)
    if isinstance(msg, dict):
        _enrich_timestamps(msg)
        if msg.get("hasAttachments"):
            try:
                atts_res = _graph_request("GET", f"/me/messages/{message_id}/attachments", params={"$select": "id,name,contentType,size,isInline"}, account=account)
                msg["attachments_summary"] = [
                    {
                        "id": a.get("id"),
                        "name": a.get("name"),
                        "contentType": a.get("contentType"),
                        "size": a.get("size"),
                        "isInline": a.get("isInline", False),
                    }
                    for a in atts_res.get("value", [])
                ]
            except Exception as e:
                msg["attachments_summary_error"] = str(e)
    return msg


@mcp.tool()
def m365_list_email_attachments(message_id: str, account: Optional[str] = None) -> Dict[str, Any]:
    """List all attachments (files, images, documents) for a specific Outlook email message."""
    res = _graph_request("GET", f"/me/messages/{message_id}/attachments", account=account)
    attachments = []
    for att in res.get("value", []):
        attachments.append({
            "id": att.get("id"),
            "name": att.get("name"),
            "contentType": att.get("contentType"),
            "size": att.get("size"),
            "isInline": att.get("isInline", False),
            "type": att.get("@odata.type", "").replace("#microsoft.graph.", ""),
        })
    return {"message_id": message_id, "count": len(attachments), "attachments": attachments}


@mcp.tool()
def m365_download_email_attachment(
    message_id: str,
    attachment_id: str,
    save_path: Optional[str] = None,
    account: Optional[str] = None,
) -> Dict[str, Any]:
    """Download a specific email attachment from Outlook and save it to the local workspace/file system."""
    att = _graph_request("GET", f"/me/messages/{message_id}/attachments/{attachment_id}", account=account)
    name = att.get("name") or f"attachment_{attachment_id}"
    content_bytes = None

    if isinstance(att, dict) and att.get("contentBytes"):
        content_bytes = base64.b64decode(att["contentBytes"])
    else:
        content_bytes = _graph_download_bytes(f"/me/messages/{message_id}/attachments/{attachment_id}/$value", account=account)

    if not save_path:
        out_file = _resolve_save_path(None, name, subfolder="documents/m365_attachments")
    else:
        out_file = Path(save_path).expanduser().resolve()
        out_file.parent.mkdir(parents=True, exist_ok=True)

    out_file.write_bytes(content_bytes)
    return {
        "success": True,
        "message_id": message_id,
        "attachment_id": attachment_id,
        "name": name,
        "size_bytes": len(content_bytes),
        "saved_path": str(out_file),
    }


@mcp.tool()
def m365_download_email_attachments(
    message_id: str,
    save_dir: Optional[str] = None,
    include_inline: bool = False,
    account: Optional[str] = None,
) -> Dict[str, Any]:
    """Download all file attachments of an Outlook email into the Vault in one call.

    Saves every file attachment of the message under
    ``documents/m365_attachments/mail/<subject>/`` (or ``save_dir``) and returns the
    saved paths — use this when the user asks for "the attachment(s) of that mail".
    Inline images (signature logos) are skipped unless ``include_inline`` is set.

    Args:
        message_id: Outlook message id (from m365_list_emails / m365_get_email).
        save_dir: Optional local directory; defaults to the Vault.
        include_inline: Also save inline images embedded in the mail body.
        account: Optional M365 account username, email, or ID.
    """
    meta = _graph_request("GET", f"/me/messages/{message_id}", params={"$select": "subject,from,receivedDateTime,hasAttachments"}, account=account)
    subject = str((meta or {}).get("subject") or "mail")
    sender = (((meta or {}).get("from") or {}).get("emailAddress") or {}).get("name") or ""
    received = _format_timestamp_local((meta or {}).get("receivedDateTime")) or ""
    listing = _graph_request("GET", f"/me/messages/{message_id}/attachments", account=account)
    files: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    skipped = 0
    subfolder = f"documents/m365_attachments/mail/{_slugify_path_component(subject, fallback='mail')}"
    for att in (listing.get("value") or []) if isinstance(listing, dict) else []:
        if not isinstance(att, dict):
            continue
        att_type = str(att.get("@odata.type") or "").replace("#microsoft.graph.", "")
        name = att.get("name") or f"attachment_{att.get('id', '')[:8]}"
        if att.get("isInline") and not include_inline:
            skipped += 1
            continue
        if att_type == "itemAttachment":
            # Attached mail items are not files; report, don't fail.
            skipped += 1
            continue
        try:
            if att.get("contentBytes"):
                data = base64.b64decode(att["contentBytes"])
            elif att_type == "referenceAttachment" and att.get("sourceUrl"):
                data = _download_file_reference(att["sourceUrl"], name)
                if data is None:
                    raise RuntimeError("reference attachment could not be fetched via shares API")
            else:
                data = _graph_download_bytes(f"/me/messages/{message_id}/attachments/{att.get('id')}/$value", account=account)
        except Exception as exc:
            errors.append({"attachment_id": att.get("id"), "name": name, "error": str(exc)[:200]})
            continue
        if save_dir:
            out_dir = Path(save_dir).expanduser().resolve()
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / name
        else:
            out_file = _resolve_save_path(None, name, subfolder=subfolder)
        if out_file.exists():
            stem, suffix = out_file.stem, out_file.suffix
            counter = 2
            while out_file.exists():
                out_file = out_file.with_name(f"{stem} ({counter}){suffix}")
                counter += 1
        out_file.write_bytes(data)
        files.append({
            "name": name,
            "saved_path": str(out_file),
            "size_bytes": len(data),
            "content_type": att.get("contentType") or "",
            "attachment_id": att.get("id"),
        })
    result: Dict[str, Any] = {
        "message_id": message_id,
        "subject": subject,
        "from": sender,
        "received_at": received,
        "files": files,
        "count": len(files),
        "skipped": skipped,
        "errors": errors,
    }
    if not files:
        result["hint"] = (
            "No file attachments saved. Inline images and attached mail items are skipped by default "
            "(include_inline=True for images); check m365_list_email_attachments for what the mail carries."
        )
    return result


@mcp.tool()
def m365_list_calendars(top: int = 20) -> Dict[str, Any]:
    """List all available Outlook calendars (personal, shared, calendar groups, and M365 group/team calendars like URLAUB and OFFICEZEITEN)."""
    calendars = []
    seen_ids = set()

    # 1. Personal calendars (/me/calendars)
    try:
        params = {"$top": min(top, 50), "$select": "id,name,color,canEdit,isDefaultCalendar,owner"}
        res = _graph_request("GET", "/me/calendars", params=params)
        for c in res.get("value", []):
            cid = c.get("id")
            if cid and cid not in seen_ids:
                seen_ids.add(cid)
                c["source_type"] = "personal"
                calendars.append(c)
    except Exception:
        pass

    # 2. Calendar Groups (/me/calendarGroups)
    try:
        groups_res = _graph_request("GET", "/me/calendarGroups")
        for cg in groups_res.get("value", []):
            cg_id = cg.get("id")
            cg_name = cg.get("name")
            if not cg_id:
                continue
            try:
                cals_res = _graph_request("GET", f"/me/calendarGroups/{cg_id}/calendars", params={"$select": "id,name,color,canEdit,isDefaultCalendar,owner"})
                for c in cals_res.get("value", []):
                    cid = c.get("id")
                    if cid and cid not in seen_ids:
                        seen_ids.add(cid)
                        c["source_type"] = "calendar_group"
                        c["calendar_group_name"] = cg_name
                        c["calendar_group_id"] = cg_id
                        calendars.append(c)
            except Exception:
                pass
    except Exception:
        pass

    # 3. M365 Group / Team Calendars (/me/joinedTeams)
    try:
        teams_res = _graph_request("GET", "/me/joinedTeams")
        for t in teams_res.get("value", []):
            group_id = t.get("id")
            group_name = t.get("displayName")
            if not group_id:
                continue
            try:
                grp_cal = _graph_request("GET", f"/groups/{group_id}/calendar", params={"$select": "id,name,color,owner"})
                cid = grp_cal.get("id")
                c_name = grp_cal.get("name") or group_name
                if c_name.lower() == "calendar":
                    c_name = group_name
                entry = {
                    "id": cid or f"group:{group_id}",
                    "name": c_name,
                    "group_id": group_id,
                    "group_name": group_name,
                    "source_type": "group",
                    "canEdit": True,
                    "isDefaultCalendar": False,
                    "owner": {"name": group_name, "address": t.get("mail")},
                }
                gid_key = f"group:{group_id}"
                if gid_key not in seen_ids and cid not in seen_ids:
                    seen_ids.add(gid_key)
                    if cid:
                        seen_ids.add(cid)
                    calendars.append(entry)
            except Exception:
                pass
    except Exception:
        pass

    return {"value": calendars}


@mcp.tool()
def m365_get_events(
    calendar: Optional[str] = None,
    start_time_iso: Optional[str] = None,
    end_time_iso: Optional[str] = None,
    top: int = 20,
) -> Dict[str, Any]:
    """Get events from any Outlook calendar (default, shared by name 'URLAUB'/'Officezeiten', group/team calendars, calendar ID, or user email).

    Args:
        calendar: Optional calendar name (e.g. 'URLAUB', 'Officezeiten'), calendar ID, or user email address. Omit for default calendar.
        start_time_iso: Optional start date/time (ISO format) for date range filtering.
        end_time_iso: Optional end date/time (ISO format) for date range filtering.
        top: Max number of events to return.
    """
    target = (calendar or "").strip()
    matched_cal: Optional[Dict[str, Any]] = None
    matched_cal_name: Optional[str] = None
    target_user_email: Optional[str] = None

    if target:
        if "@" in target:
            target_user_email = target
        else:
            cals = m365_list_calendars(top=50)
            if "value" in cals and isinstance(cals["value"], list):
                target_lower = target.lower()
                for c in cals["value"]:
                    c_id = str(c.get("id") or "")
                    c_name = str(c.get("name") or "")
                    g_name = str(c.get("group_name") or "")
                    if (
                        c_id == target
                        or target_lower in c_name.lower()
                        or c_name.lower() in target_lower
                        or (g_name and target_lower in g_name.lower())
                    ):
                        matched_cal = c
                        matched_cal_name = c_name or g_name
                        break

    if target_user_email:
        base_path = f"/users/{target_user_email}/calendar"
    elif matched_cal:
        stype = matched_cal.get("source_type")
        if stype == "group" and matched_cal.get("group_id"):
            base_path = f"/groups/{matched_cal['group_id']}/calendar"
        elif stype == "calendar_group" and matched_cal.get("calendar_group_id") and matched_cal.get("id"):
            base_path = f"/me/calendarGroups/{matched_cal['calendar_group_id']}/calendars/{matched_cal['id']}"
        elif matched_cal.get("id"):
            base_path = f"/me/calendars/{matched_cal['id']}"
        else:
            base_path = f"/me/calendars/{target}"
    elif target:
        base_path = f"/me/calendars/{target}"
    else:
        base_path = "/me/calendar"

    params: Dict[str, Any] = {"$select": "id,subject,start,end,location,organizer,attendees,isAllDay,categories"}

    if start_time_iso or end_time_iso:
        if start_time_iso and not end_time_iso:
            s_raw = str(start_time_iso).strip()
            s_date = s_raw.split("T")[0].split(" ")[0]
            end_time_iso = f"{s_date}T23:59:59"
        elif end_time_iso and not start_time_iso:
            e_raw = str(end_time_iso).strip()
            e_date = e_raw.split("T")[0].split(" ")[0]
            start_time_iso = f"{e_date}T00:00:00"

        start_clean, _ = _normalize_datetime_input(start_time_iso)
        end_clean, _ = _normalize_datetime_input(end_time_iso)
        params["startDateTime"] = start_clean
        params["endDateTime"] = end_clean
        params["$top"] = min(top, 100)
        endpoint = f"{base_path}/calendarView"
    else:
        params["$top"] = min(top, 50)
        endpoint = f"{base_path}/events"

    try:
        res = _graph_request("GET", endpoint, params=params)
    except Exception as err:
        if target and not target_user_email and not matched_cal:
            try:
                fallback_ep = f"/groups/{target}/calendar/calendarView" if (start_time_iso and end_time_iso) else f"/groups/{target}/events"
                res = _graph_request("GET", fallback_ep, params=params)
            except Exception:
                raise err
        else:
            raise err

    if matched_cal_name:
        res["resolved_calendar_name"] = matched_cal_name

    if isinstance(res, dict) and "value" in res and isinstance(res["value"], list):
        for evt in res["value"]:
            if isinstance(evt, dict):
                if "start" in evt:
                    evt["start_local"] = _format_timestamp_local(evt.get("start"))
                    evt["start_iso_local"], _ = _normalize_datetime_input(
                        evt.get("start", {}).get("dateTime") if isinstance(evt.get("start"), dict) else evt.get("start")
                    )
                if "end" in evt:
                    evt["end_local"] = _format_timestamp_local(evt.get("end"))
                    evt["end_iso_local"], _ = _normalize_datetime_input(
                        evt.get("end", {}).get("dateTime") if isinstance(evt.get("end"), dict) else evt.get("end")
                    )
                evt["timezone"] = _get_timezone_name()

    return res


@mcp.tool()
def m365_list_events(top: int = 10) -> Dict[str, Any]:
    """List upcoming events in default Outlook Calendar (alias for m365_get_events)."""
    return m365_get_events(top=top)


@mcp.tool()
def m365_create_event(
    subject: str,
    start_time_iso: str,
    end_time_iso: str,
    time_zone: Optional[str] = None,
    attendees: Optional[List[str]] = None,
    body: Optional[str] = None,
    calendar: Optional[str] = None,
    is_all_day: bool = False,
    categories: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Create a new event or meeting in Outlook Calendar or a shared calendar (e.g. URLAUB / Officezeiten).

    Note: Datetimes are automatically normalized into local timezone format.
    Pass ISO strings (e.g. '2026-07-29T17:00:00' or '2026-07-29T15:00:00Z').

    Args:
        subject: Event title.
        start_time_iso: Start date/time in ISO format.
        end_time_iso: End date/time in ISO format.
        time_zone: Time zone name (defaults to system/configured timezone).
        attendees: List of attendee email addresses.
        body: Optional description text.
        calendar: Optional calendar name ('URLAUB', 'Officezeiten'), ID, or user email to create the event in.
        is_all_day: Set to True for all-day events (e.g. vacation / URLAUB entries).
        categories: Optional list of category tags.
    """
    tz_name = time_zone or _get_timezone_name()
    start_clean, tz_start = _normalize_datetime_input(start_time_iso, default_tz=tz_name)
    end_clean, tz_end = _normalize_datetime_input(end_time_iso, default_tz=tz_name)

    payload: Dict[str, Any] = {
        "subject": subject,
        "start": {"dateTime": start_clean, "timeZone": tz_start},
        "end": {"dateTime": end_clean, "timeZone": tz_end},
        "isAllDay": is_all_day,
    }
    if body:
        payload["body"] = {"contentType": "Text", "content": body}
    if attendees:
        payload["attendees"] = [
            {"emailAddress": {"address": a.strip()}, "type": "required"} for a in attendees
        ]
    if categories:
        payload["categories"] = categories

    target = (calendar or "").strip()
    target_cal_id: Optional[str] = None
    target_user_email: Optional[str] = None

    if target:
        if "@" in target:
            target_user_email = target
        else:
            cals = m365_list_calendars(top=50)
            if "value" in cals and isinstance(cals["value"], list):
                target_lower = target.lower()
                for c in cals["value"]:
                    c_id = str(c.get("id") or "")
                    c_name = str(c.get("name") or "")
                    if c_id == target or target_lower in c_name.lower() or c_name.lower() in target_lower:
                        target_cal_id = c_id
                        break
            if not target_cal_id:
                target_cal_id = target

    if target_user_email:
        endpoint = f"/users/{target_user_email}/calendar/events"
    elif target:
        cals = m365_list_calendars(top=50)
        matched_cal: Optional[Dict[str, Any]] = None
        if "value" in cals and isinstance(cals["value"], list):
            target_lower = target.lower()
            for c in cals["value"]:
                c_id = str(c.get("id") or "")
                c_name = str(c.get("name") or "")
                g_name = str(c.get("group_name") or "")
                if c_id == target or target_lower in c_name.lower() or c_name.lower() in target_lower or (g_name and target_lower in g_name.lower()):
                    matched_cal = c
                    break
        if matched_cal:
            stype = matched_cal.get("source_type")
            if stype == "group" and matched_cal.get("group_id"):
                endpoint = f"/groups/{matched_cal['group_id']}/events"
            elif stype == "calendar_group" and matched_cal.get("calendar_group_id") and matched_cal.get("id"):
                endpoint = f"/me/calendarGroups/{matched_cal['calendar_group_id']}/calendars/{matched_cal['id']}/events"
            elif matched_cal.get("id"):
                endpoint = f"/me/calendars/{matched_cal['id']}/events"
            else:
                endpoint = f"/me/calendars/{target}/events"
        else:
            endpoint = f"/me/calendars/{target}/events"
    else:
        endpoint = "/me/calendar/events"

    return _graph_request("POST", endpoint, json_data=payload)



# ─── Teams: compact records, recipient resolution, Markdown → HTML ──────────
#
# Why (AIS-286, session 20260903_095740): asked to "send X to Fischi via
# Teams" the agent looked for a chat id in memory, tried the wrong tool and
# finally asked the user for the chat URL — m365_list_chats was never
# called, the raw Graph objects were too noisy to reason about, the message
# went out as plain text with Markdown asterisks, and the register was a
# formal letter. Everything below exists so the model does not have to guess.

import html as _html
import re as _re

_MY_IDENTITY_CACHE: Dict[str, Any] = {}
_MY_IDENTITY_TTL_SECONDS = 600.0
_TEAMS_PREVIEW_CHARS = 160
_TEAMS_MESSAGE_TEXT_CHARS = 600


def _html_to_text(value: Any) -> str:
    """Strip HTML to readable text (block tags → newlines, entities decoded)."""
    if not value:
        return ""
    text = str(value)
    text = _re.sub(r"<\s*br\s*/?>", "\n", text, flags=_re.IGNORECASE)
    text = _re.sub(r"</\s*(p|div|li|h[1-6]|tr)\s*>", "\n", text, flags=_re.IGNORECASE)
    text = _re.sub(r"<li\b[^>]*>", "• ", text, flags=_re.IGNORECASE)
    text = _re.sub(r"<[^>]+>", "", text)
    text = _html.unescape(text)
    text = text.replace(" ", " ")
    text = _re.sub(r"[ \t]+", " ", text)
    text = _re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _truncate(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _compact_member(member: Any) -> Dict[str, Any]:
    if not isinstance(member, dict):
        return {}
    email = member.get("email") or member.get("userPrincipalName") or member.get("mail") or ""
    return {
        "displayName": member.get("displayName") or "",
        "email": email,
        "user_id": member.get("userId") or member.get("id") or "",
    }


def _compact_chat(chat: Any) -> Dict[str, Any]:
    """Reduce a Graph chat object to what a model needs to pick a recipient."""
    if not isinstance(chat, dict):
        return {}
    members = [_compact_member(m) for m in (chat.get("members") or []) if isinstance(m, dict)]
    preview = chat.get("lastMessagePreview") or {}
    last_message: Optional[Dict[str, Any]] = None
    if isinstance(preview, dict) and preview:
        body = preview.get("body") or {}
        sender = ((preview.get("from") or {}).get("user") or {}).get("displayName") or ""
        if not sender and (preview.get("from") or {}).get("application"):
            sender = ((preview.get("from") or {}).get("application") or {}).get("displayName") or "app"
        last_message = {
            "from": sender,
            "preview": _truncate(_html_to_text(body.get("content") if isinstance(body, dict) else ""), _TEAMS_PREVIEW_CHARS),
            "at": _format_timestamp_local(preview.get("createdDateTime")) or preview.get("createdDateTime") or "",
        }
    return {
        "chat_id": chat.get("id") or "",
        "chat_type": chat.get("chatType") or "",
        "topic": chat.get("topic") or "",
        "members": [m for m in members if m.get("displayName") or m.get("email")],
        "last_message": last_message,
        "updated_at": _format_timestamp_local(chat.get("lastUpdatedDateTime")) or chat.get("lastUpdatedDateTime") or "",
        "web_url": chat.get("webUrl") or "",
    }


def _compact_message(msg: Any) -> Dict[str, Any]:
    """Reduce a Graph chatMessage to sender, local time and readable text."""
    if not isinstance(msg, dict):
        return {}
    body = msg.get("body") or {}
    frm = msg.get("from") or {}
    user = frm.get("user") or {}
    application = frm.get("application") or {}
    sender = user.get("displayName") or application.get("displayName") or ""
    content = body.get("content") if isinstance(body, dict) else ""
    content_type = (body.get("contentType") if isinstance(body, dict) else "") or "text"
    text = _html_to_text(content) if str(content_type).lower() == "html" else str(content or "").strip()
    out: Dict[str, Any] = {
        "id": msg.get("id") or "",
        "from": sender,
        "from_user_id": user.get("id") or "",
        "at": _format_timestamp_local(msg.get("createdDateTime")) or msg.get("createdDateTime") or "",
        "text": _truncate(text, _TEAMS_MESSAGE_TEXT_CHARS),
        "message_type": msg.get("messageType") or "message",
    }
    if msg.get("has_attachments") or msg.get("attachments_summary"):
        out["has_attachments"] = True
        out["attachments_summary"] = msg.get("attachments_summary") or []
    if msg.get("replyToId"):
        out["reply_to_id"] = msg.get("replyToId")
    return out


def _looks_like_html(text: str) -> bool:
    return bool(_re.search(r"<(p|div|br|ul|ol|li|h[1-6]|b|strong|i|em|a|table|code|pre)\b", text or "", _re.IGNORECASE))


def _md_inline_to_html(text: str) -> str:
    """Inline Markdown → HTML (escaped first, so user text never becomes markup)."""
    esc = _html.escape(text, quote=False)
    esc = _re.sub(r"`([^`]+)`", r"<code>\1</code>", esc)
    esc = _re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', esc)
    esc = _re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc)
    esc = _re.sub(r"__(.+?)__", r"<strong>\1</strong>", esc)
    esc = _re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", esc)
    esc = _re.sub(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])", r"<em>\1</em>", esc)
    esc = _re.sub(r"~~(.+?)~~", r"<s>\1</s>", esc)
    return esc


def _markdown_to_teams_html(text: str) -> str:
    """Render the Markdown a model writes in chat into the HTML Teams renders.

    Supports paragraphs, line breaks, bold/italic/strike/inline code, links,
    bullet and numbered lists, headings (as bold paragraphs) and quotes.
    Already-HTML input is returned unchanged so hand-written markup keeps
    working.
    """
    if not text:
        return ""
    if _looks_like_html(text):
        return text
    lines = text.replace("\r\n", "\n").split("\n")
    out: List[str] = []
    para: List[str] = []
    list_tag: Optional[str] = None

    def flush_para() -> None:
        if para:
            out.append("<p>" + "<br>".join(_md_inline_to_html(ln) for ln in para) + "</p>")
            para.clear()

    def close_list() -> None:
        nonlocal list_tag
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = None

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            flush_para()
            close_list()
            continue
        bullet = _re.match(r"^[-*•]\s+(.*)$", stripped)
        numbered = _re.match(r"^\d+[.)]\s+(.*)$", stripped)
        heading = _re.match(r"^#{1,6}\s+(.*)$", stripped)
        quote = _re.match(r"^>\s?(.*)$", stripped)
        if bullet or numbered:
            flush_para()
            tag = "ul" if bullet else "ol"
            if list_tag != tag:
                close_list()
                out.append(f"<{tag}>")
                list_tag = tag
            out.append(f"<li>{_md_inline_to_html((bullet or numbered).group(1))}</li>")
            continue
        close_list()
        if heading:
            flush_para()
            out.append(f"<p><strong>{_md_inline_to_html(heading.group(1))}</strong></p>")
            continue
        if quote:
            flush_para()
            out.append(f"<blockquote>{_md_inline_to_html(quote.group(1))}</blockquote>")
            continue
        para.append(stripped)
    flush_para()
    close_list()
    return "".join(out)


def _my_identity(account: Optional[str] = None) -> Dict[str, str]:
    """Return {id, upn, displayName} of the signed-in user (cached 10 min)."""
    key = (account or "").strip().lower()
    cached = _MY_IDENTITY_CACHE.get(key)
    if cached and time.monotonic() - cached[1] < _MY_IDENTITY_TTL_SECONDS:
        return cached[0]
    me = _graph_request("GET", "/me", params={"$select": "id,displayName,mail,userPrincipalName"}, account=account)
    ident = {
        "id": str((me or {}).get("id") or ""),
        "upn": str((me or {}).get("userPrincipalName") or (me or {}).get("mail") or ""),
        "displayName": str((me or {}).get("displayName") or ""),
    }
    _MY_IDENTITY_CACHE[key] = (ident, time.monotonic())
    return ident


def _fetch_my_chats(top: int = 50, pages: int = 2, account: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch the user's recent chats with members + last message (≤ ``pages`` pages)."""
    params: Dict[str, Any] = {"$top": min(max(int(top), 1), 50), "$expand": "members,lastMessagePreview"}
    chats: List[Dict[str, Any]] = []
    try:
        res = _graph_request("GET", "/me/chats", params=params, account=account)
    except Exception:
        res = _graph_request("GET", "/me/chats", params={"$top": params["$top"]}, account=account)
    for _ in range(max(1, pages)):
        if not isinstance(res, dict):
            break
        chats.extend(c for c in (res.get("value") or []) if isinstance(c, dict))
        next_link = res.get("@odata.nextLink")
        if not next_link or len(chats) >= params["$top"] * pages:
            break
        try:
            res = _graph_request("GET", next_link, account=account)
        except Exception:
            break
    for c in chats:
        if not c.get("members"):
            try:
                members_res = _graph_request("GET", f"/me/chats/{c.get('id')}/members", account=account)
                c["members"] = members_res.get("value", []) if isinstance(members_res, dict) else []
            except Exception:
                c["members"] = []
    return chats


def _norm_name(value: Any) -> str:
    text = _html.unescape(str(value or "")).lower().strip()
    text = text.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return _re.sub(r"[^a-z0-9@._\- ]+", " ", text).strip()


def _score_chat_candidate(chat: Dict[str, Any], query: str, me: Dict[str, str], prefer: str) -> Tuple[int, str, str]:
    """Score how well ``chat`` matches a recipient/topic query.

    Returns ``(score, reason, match_key)``; ``match_key`` identifies *who or
    what* matched (a member's email/name or the topic) so that the same
    person appearing in a 1:1 chat and in a group chat does not count as an
    ambiguous match.
    """
    q = _norm_name(query)
    if not q:
        return 0, "", ""
    q_tokens = [t for t in q.replace(",", " ").split() if t]
    my_id = (me.get("id") or "").lower()
    my_upn = _norm_name(me.get("upn"))
    others = []
    for m in chat.get("members") or []:
        if not isinstance(m, dict):
            continue
        uid = str(m.get("userId") or m.get("id") or "").lower()
        email = _norm_name(m.get("email") or m.get("userPrincipalName") or m.get("mail"))
        if (my_id and uid == my_id) or (my_upn and email == my_upn):
            continue
        others.append((_norm_name(m.get("displayName")), email))
    chat_type = str(chat.get("chatType") or "").lower()
    topic = _norm_name(chat.get("topic"))
    best = 0
    reason = ""
    match_key = ""
    # Nickname stem: "Fischi" → "fisch", "Andi" → "and", "Tommy" → "tomm".
    nick_stem = q[:-1] if len(q_tokens) == 1 and len(q) >= 4 and q[-1] in "iy" else ""
    if nick_stem.endswith("ie"):
        nick_stem = nick_stem[:-1]
    for name, email in others:
        name_tokens = name.split()
        if "@" in q and email and q == email:
            score, why = 100, f"exact email {email}"
        elif name and q == name:
            score, why = 90, f"full name {name}"
        elif email and q == email.split("@")[0]:
            score, why = 85, f"mail alias {email}"
        elif len(q_tokens) >= 2 and all(any(nt.startswith(t) for nt in name_tokens) for t in q_tokens):
            score, why = 80, f"first+last name {name}"
        elif len(q_tokens) == 1 and name_tokens and any(nt == q for nt in name_tokens):
            score, why = 60, f"name token {name}"
        elif len(q_tokens) == 1 and any(nt.startswith(q) for nt in name_tokens) and len(q) >= 3:
            score, why = 45, f"name prefix {name}"
        elif nick_stem and any(nt.startswith(nick_stem) for nt in name_tokens):
            score, why = 42, f"nickname of {name}"
        elif len(q_tokens) == 1 and len(q) >= 4 and any(q in nt for nt in name_tokens):
            score, why = 35, f"name contains {name}"
        else:
            continue
        if chat_type == "oneonone":
            score += 8
        if score > best:
            best, reason, match_key = score, why, f"person:{email or name}"
    if topic:
        if q == topic:
            t_score, t_why = 88, f"topic {chat.get('topic')}"
        elif all(t in topic for t in q_tokens):
            t_score, t_why = 70, f"topic contains {chat.get('topic')}"
        elif any(len(t) >= 4 and t in topic for t in q_tokens):
            t_score, t_why = 40, f"topic partially {chat.get('topic')}"
        else:
            t_score, t_why = 0, ""
        if t_score > best:
            best, reason, match_key = t_score, t_why, f"topic:{topic}"
    preview = chat.get("lastMessagePreview") or {}
    sender = _norm_name(((preview.get("from") or {}).get("user") or {}).get("displayName")) if isinstance(preview, dict) else ""
    if best == 0 and sender and q in sender:
        best, reason, match_key = 30, f"last sender {sender}", f"person:{sender}"
    if best and prefer in ("oneonone", "group") and chat_type != prefer:
        best -= 15
    return best, reason, match_key


def _resolve_teams_recipient(query: str, prefer: str = "any", top: int = 25, account: Optional[str] = None) -> Dict[str, Any]:
    """Rank the user's chats for ``query`` and decide unique | ambiguous | none."""
    prefer_norm = (prefer or "any").strip().lower().replace("_", "").replace("-", "")
    if prefer_norm in ("1:1", "11", "direct", "dm", "oneonone"):
        prefer_norm = "oneonone"
    elif prefer_norm not in ("group", "any"):
        prefer_norm = "any"
    me = _my_identity(account=account)
    chats = _fetch_my_chats(top=50, pages=2, account=account)
    scored: List[Tuple[int, str, str, Dict[str, Any]]] = []
    for chat in chats:
        score, reason, key = _score_chat_candidate(chat, query, me, prefer_norm)
        if score > 0:
            scored.append((score, reason, key, chat))
    # Highest score first; on ties the most recently active chat wins.
    scored.sort(key=lambda item: str(item[3].get("lastUpdatedDateTime") or ""), reverse=True)
    scored.sort(key=lambda item: -item[0])
    candidates = []
    keys: List[str] = []
    for score, reason, key, chat in scored[: max(1, min(int(top), 50))]:
        compact = _compact_chat(chat)
        compact["score"] = score
        compact["match_reason"] = reason
        candidates.append(compact)
        keys.append(key)
    if not candidates:
        resolution = "none"
    else:
        top_score, top_key = candidates[0]["score"], keys[0]
        # A competitor is a *different* person/topic scoring close to the top.
        # The same person in a 1:1 chat and in a group chat is not ambiguous —
        # the 1:1 chat wins (it carries the +8 direct-chat boost).
        competitors = [
            c for c, k in zip(candidates[1:], keys[1:])
            if k != top_key and c["score"] >= top_score - 15
        ]
        if top_score >= 40 and not competitors:
            resolution = "unique"
        elif top_score >= 80 and not competitors:
            resolution = "unique"
        else:
            resolution = "ambiguous"
    result: Dict[str, Any] = {
        "query": query,
        "prefer": prefer_norm,
        "resolution": resolution,
        "candidates": candidates,
        "chats_scanned": len(chats),
    }
    if resolution == "unique":
        result["chat_id"] = candidates[0]["chat_id"]
        result["next_step"] = "Send with m365_send_chat_message(chat_id=...) or pass the same `to` and let it resolve."
    elif resolution == "ambiguous":
        result["next_step"] = (
            "Several chats match. Show the candidates (members, topic, last message) to the user and "
            "ask which one, or refine the query with a full name / email / topic."
        )
    else:
        result["next_step"] = "No existing chat matches."
        if "@" in query or len(query.split()) >= 2:
            result["direct_chat_hint"] = {
                "tool": "m365_get_or_create_direct_chat",
                "user_id_or_upn": query,
                "note": "Start a new 1:1 chat with this person (resolved via existing chats first, then the directory).",
            }
    return result


def _clean_query_text(value: Optional[str]) -> str:
    return str(value or "").strip()


_TEAMS_LINK_RE = _re.compile(
    r"https?://teams\.(?:microsoft|cloud\.microsoft)\.com/l/(chat|message)/([^/?#\s]+)(?:/([^/?#\s]+))?",
    _re.IGNORECASE,
)


def _parse_teams_link(text: Optional[str]) -> Optional[Dict[str, Optional[str]]]:
    """Extract chat_id (and message_id) from a Teams deep link.

    Handles ``https://teams.microsoft.com/l/chat/<chatId>/0?...`` and
    ``https://teams.microsoft.com/l/message/<chatId>/<messageId>?...``; the
    chat id is URL-encoded in links (``19%3A...%40thread.v2``). Returns None
    when ``text`` is not a Teams link.
    """
    if not text:
        return None
    m = _TEAMS_LINK_RE.search(str(text))
    if not m:
        return None
    from urllib.parse import unquote

    kind, chat_id, tail = m.group(1).lower(), unquote(m.group(2)), m.group(3)
    message_id = unquote(tail) if (kind == "message" and tail) else None
    return {"chat_id": chat_id, "message_id": message_id, "kind": kind}


def _coerce_chat_ref(value: Optional[str]) -> Optional[str]:
    """Accept a raw chat id or a Teams deep link wherever a chat id is expected."""
    text = _clean_query_text(value)
    if not text:
        return None
    parsed = _parse_teams_link(text)
    return parsed["chat_id"] if parsed else text


def _slugify_path_component(value: str, fallback: str = "chat") -> str:
    text = _html.unescape(str(value or "")).strip()
    text = _re.sub(r"[^\w\-. ]+", "_", text, flags=_re.UNICODE)
    text = _re.sub(r"[\s_]+", "_", text).strip(" ._")
    return (text[:60].rstrip(" ._") or fallback)


@mcp.tool()
def m365_list_chats(
    top: int = 10,
    expand_members: bool = True,
    search_query: Optional[str] = None,
    chat_type: Optional[str] = None,
    account: Optional[str] = None,
    raw: bool = False,
) -> Dict[str, Any]:
    """List recent Microsoft Teams chats (1:1, group, meeting) in a compact form.

    To find the chat for a person or topic, prefer `m365_find_chat` (ranked,
    with a unique/ambiguous verdict). This tool is for browsing recent chats.

    Args:
        top: Max number of recent chats to inspect or return (capped at 50).
        expand_members: 'True' (default) includes participants (displayName, email).
        search_query: Optional substring filter on member names, emails, topic or last message.
        chat_type: Optional filter by chat type ('oneOnOne', 'group', or 'meeting').
        account: Optional M365 account username, email, or ID.
        raw: Return the unmodified Graph objects instead of compact records.
    """
    params = {"$top": min(top, 50)}
    if expand_members:
        params["$expand"] = "members,lastMessagePreview"

    try:
        res = _graph_request("GET", "/me/chats", params=params, account=account)
    except Exception:
        # Fallback without $expand if tenant or Graph API rejects expand query
        fallback_params = {"$top": min(top, 50)}
        res = _graph_request("GET", "/me/chats", params=fallback_params, account=account)

    chats = res.get("value", []) if isinstance(res, dict) and "value" in res else []

    # If members were not expanded inline by Graph, fetch members per chat if requested or searching
    if chats and (expand_members or search_query):
        for c in chats:
            if not isinstance(c, dict):
                continue
            chat_id = c.get("id")
            if not chat_id:
                continue
            if "members" not in c or not c["members"]:
                try:
                    members_res = _graph_request("GET", f"/me/chats/{chat_id}/members", account=account)
                    c["members"] = members_res.get("value", []) if isinstance(members_res, dict) else []
                except Exception:
                    pass

    # Filter by chat_type if specified
    if chat_type and chats:
        ct_clean = chat_type.strip().lower()
        chats = [c for c in chats if isinstance(c, dict) and str(c.get("chatType", "")).lower() == ct_clean]

    # Filter by search_query if specified (matches member name, email, chat topic, or preview message)
    if search_query and chats:
        q_clean = search_query.strip().lower()
        filtered = []
        for c in chats:
            if not isinstance(c, dict):
                continue
            topic = str(c.get("topic") or "").lower()
            chat_id = str(c.get("id") or "").lower()
            preview = str((c.get("lastMessagePreview") or {}).get("body", {}).get("content") or "").lower()

            members = c.get("members") or []
            member_match = False
            for m in members:
                if isinstance(m, dict):
                    display_name = str(m.get("displayName") or "").lower()
                    email = str(m.get("email") or m.get("userPrincipalName") or "").lower()
                    if q_clean in display_name or q_clean in email:
                        member_match = True
                        break

            if q_clean in topic or q_clean in chat_id or q_clean in preview or member_match:
                filtered.append(c)
        chats = filtered

    if isinstance(res, dict):
        for c in chats:
            if isinstance(c, dict):
                _enrich_timestamps(c)
        res["value"] = chats
        res["count"] = len(chats)
    if raw or not isinstance(res, dict):
        return res
    return {"count": len(chats), "chats": [_compact_chat(c) for c in chats if isinstance(c, dict)]}


@mcp.tool()
def m365_find_chat(
    query: str,
    prefer: str = "any",
    top: int = 25,
    account: Optional[str] = None,
) -> Dict[str, Any]:
    """Find the Teams chat for a person, nickname, email or group topic — no directory rights needed.

    Ranks the signed-in user's recent chats by member names / emails / topic
    (exact email > full name > first+last name > single name token > topic)
    and returns a verdict:

    - `resolution: "unique"`  → `chat_id` is safe to use.
    - `resolution: "ambiguous"` → show `candidates` (members, topic, last message)
      to the user and ask; never pick one silently.
    - `resolution: "none"` → `direct_chat_hint` says how to start a 1:1 chat when
      the query is an email or full name.

    Args:
        query: Person name, nickname, email address or group-chat topic.
        prefer: "oneOnOne" (direct chats), "group", or "any" (default).
        top: Max candidates to return (capped at 50).
        account: Optional M365 account username, email, or ID.
    """
    query_clean = _clean_query_text(query)
    if not query_clean:
        return {"error": "query is required (person name, email or chat topic)."}
    linked = _parse_teams_link(query_clean)
    if linked:
        # A pasted Teams link already names the chat — no ranking needed.
        chat_id = linked["chat_id"]
        try:
            raw = _graph_request("GET", f"/me/chats/{chat_id}", params={"$expand": "members"}, account=account)
        except Exception as exc:
            return {"query": query_clean, "resolution": "none", "candidates": [], "error": f"chat from link not accessible: {exc}"}
        compact = _compact_chat(raw if isinstance(raw, dict) else {"id": chat_id})
        compact["chat_id"] = compact.get("chat_id") or chat_id
        compact["score"] = 100
        compact["match_reason"] = "teams link"
        result = {"query": query_clean, "prefer": "any", "resolution": "unique", "candidates": [compact], "chat_id": chat_id, "chats_scanned": 0}
        if linked.get("message_id"):
            result["message_id"] = linked["message_id"]
        return result
    return _resolve_teams_recipient(query_clean, prefer=prefer, top=top, account=account)


@mcp.tool()
def m365_send_chat_message(
    chat_id: Optional[str] = None,
    content: Optional[str] = None,
    content_type: str = "html",
    message: Optional[str] = None,
    body: Optional[str] = None,
    text: Optional[str] = None,
    attachments: Optional[List[str]] = None,
    to: Optional[str] = None,
    dry_run: bool = False,
    account: Optional[str] = None,
) -> Dict[str, Any]:
    """Send a Microsoft Teams chat message to a person or chat.

    Recipient: pass `to` (name, nickname, email or group topic) and the tool
    resolves the chat via `m365_find_chat`; it sends only when the match is
    unique and otherwise returns the candidates without sending. Never reuse a
    chat_id from memory or an earlier session — resolve it fresh with `to`.

    Formatting: write `content` as the same Markdown you showed the user
    (bold, lists, links, paragraphs). It is rendered to the HTML Teams
    displays, so what was approved in chat is what arrives. Hand-written HTML
    is passed through unchanged. The result carries `rendered_html` and
    `plain_text` so you can confirm exactly what was sent.

    Args:
        chat_id: Teams chat id. Optional when `to` is given.
        content: Message text (Markdown or HTML).
        content_type: 'html' (default, Markdown is rendered) or 'text' (sent verbatim).
        message: Alias for content.
        body: Alias for content.
        text: Alias for content.
        attachments: Local file paths (absolute, or relative to the Vault / workspace) — e.g. a path returned by m365_download_chat_files or m365_download_email_attachments. Uploaded to OneDrive and linked as file cards (forces HTML).
        to: Recipient name / email / group topic; resolved to a chat before sending.
        dry_run: Resolve the recipient and render the message but do not send.
        account: Optional M365 account username, email, or ID.
    """
    resolved_content = content if content is not None else (message if message is not None else (body if body is not None else text))
    if resolved_content is None:
        raise ValueError("m365_send_chat_message requires the message text via 'content' (or its aliases: message/body/text).")

    recipient: Optional[Dict[str, Any]] = None
    chat_type = ""
    resolution: Optional[Dict[str, Any]] = None
    target_chat_id = _coerce_chat_ref(chat_id) or ""
    to_clean = _clean_query_text(to)
    if not target_chat_id and to_clean and _parse_teams_link(to_clean):
        target_chat_id = _coerce_chat_ref(to_clean) or ""
    if not target_chat_id:
        if not to_clean:
            raise ValueError("m365_send_chat_message needs either 'to' (name / email / topic) or 'chat_id'.")
        resolution = _resolve_teams_recipient(to_clean, prefer="any", top=5, account=account)
        if resolution["resolution"] != "unique":
            return {
                "sent": False,
                "error": (
                    "recipient ambiguous — ask the user which chat is meant"
                    if resolution["resolution"] == "ambiguous"
                    else "recipient not found among your Teams chats"
                ),
                "to": to_clean,
                **resolution,
            }
        target_chat_id = resolution["chat_id"]
    if resolution is not None:
        top_candidate = resolution["candidates"][0]
        chat_type = top_candidate.get("chat_type") or ""
        me = _my_identity(account=account)
        others = [
            m for m in top_candidate.get("members") or []
            if (m.get("user_id") or "").lower() != (me.get("id") or "").lower()
            and _norm_name(m.get("email")) != _norm_name(me.get("upn"))
        ]
        recipient = {
            "chat_id": target_chat_id,
            "chat_type": chat_type,
            "topic": top_candidate.get("topic") or "",
            "members": others or top_candidate.get("members") or [],
            "match_reason": top_candidate.get("match_reason") or "",
        }

    norm_attachments = _normalize_attachment_list(attachments)
    ct = "html" if norm_attachments else (content_type or "html").lower()
    if ct == "html":
        final_content = _markdown_to_teams_html(resolved_content)
        if not final_content:
            final_content = resolved_content
    else:
        final_content = resolved_content
    plain_text = _html_to_text(final_content) if ct == "html" else resolved_content

    payload: Dict[str, Any] = {
        "body": {
            "contentType": "html" if ct == "html" else "text",
            "content": final_content,
        }
    }
    result: Dict[str, Any] = {
        "chat_id": target_chat_id,
        "content_type": payload["body"]["contentType"],
        "rendered_html": final_content if ct == "html" else None,
        "plain_text": plain_text,
    }
    if recipient:
        result["recipient"] = recipient
        result["chat_type"] = chat_type
    if dry_run:
        result["sent"] = False
        result["dry_run"] = True
        result["attachments"] = norm_attachments
        return result
    if norm_attachments:
        attachment_payload, attachment_tags = _build_teams_attachments(norm_attachments)
        payload["attachments"] = attachment_payload
        payload["body"]["content"] = final_content + attachment_tags
    graph_res = _graph_request("POST", f"/me/chats/{target_chat_id}/messages", json_data=payload, account=account)
    if isinstance(graph_res, dict):
        result.update({k: v for k, v in graph_res.items() if k not in result})
        result["message_id"] = graph_res.get("id")
    result["sent"] = True
    return result


@mcp.tool()
def m365_list_drive_files(folder_id: Optional[str] = None, top: int = 20) -> Dict[str, Any]:
    """List files and folders in OneDrive."""
    endpoint = f"/me/drive/items/{folder_id}/children" if folder_id else "/me/drive/root/children"
    params = {"$top": min(top, 50)}
    res = _graph_request("GET", endpoint, params=params)
    if isinstance(res, dict) and "value" in res and isinstance(res["value"], list):
        for item in res["value"]:
            if isinstance(item, dict):
                _enrich_timestamps(item)
    return res


@mcp.tool()
def m365_search_drive_files(query: str) -> Dict[str, Any]:
    """Search files in OneDrive by keyword."""
    res = _graph_request("GET", f"/me/drive/root/search(q='{query}')")
    if isinstance(res, dict) and "value" in res and isinstance(res["value"], list):
        for item in res["value"]:
            if isinstance(item, dict):
                _enrich_timestamps(item)
    return res


@mcp.tool()
def m365_get_drive_file(file_id: str) -> Dict[str, Any]:
    """Get file metadata and download URL from OneDrive."""
    res = _graph_request("GET", f"/me/drive/items/{file_id}")
    if isinstance(res, dict):
        _enrich_timestamps(res)
    return res


def _share_token(url: str) -> str:
    """Encode a sharing/web URL for the Graph shares API (``/shares/u!<token>``)."""
    b64 = base64.urlsafe_b64encode(url.encode("utf-8")).decode("utf-8").rstrip("=")
    return f"u!{b64}"


@mcp.tool()
def m365_download_drive_file(
    file_id: str,
    save_path: Optional[str] = None,
    account: Optional[str] = None,
) -> Dict[str, Any]:
    """Download a file from OneDrive or SharePoint into the Vault (documents/m365_downloads/).

    ``file_id`` is a drive item id OR a SharePoint/OneDrive URL (a file's
    webUrl, a sharing link, or the contentUrl of a Teams chat attachment) —
    URLs are resolved through the Graph shares API, so files in another
    person's OneDrive work as long as they were shared with you. A Teams
    attachment id is neither. For the files posted in a Teams chat prefer
    ``m365_download_chat_files``. Returns ``saved_path``.
    """
    ident = (file_id or "").strip()
    if not ident:
        return {"error": "file_id is required: a drive item id or a SharePoint/OneDrive URL"}
    is_url = ident.lower().startswith(("http://", "https://"))
    if is_url:
        base = f"/shares/{_share_token(ident)}/driveItem"
        source = "url"
    else:
        base = f"/me/drive/items/{ident}"
        source = "item_id"
    meta = _graph_request("GET", base, account=account)
    if isinstance(meta, dict) and meta.get("error"):
        return meta
    name = (meta.get("name") if isinstance(meta, dict) else None) or (
        _re.sub(r"[?#].*$", "", ident).rsplit("/", 1)[-1] if is_url else f"drive_file_{ident}"
    )
    content_bytes = _graph_download_bytes(f"{base}/content", account=account)

    if not save_path:
        out_file = _resolve_save_path(None, name, subfolder="documents/m365_downloads")
    else:
        out_file = Path(save_path).expanduser().resolve()
        out_file.parent.mkdir(parents=True, exist_ok=True)

    out_file.write_bytes(content_bytes)
    return {
        "success": True,
        "file_id": (meta.get("id") if isinstance(meta, dict) else None) or ident,
        "source": source,
        "name": name,
        "size_bytes": len(content_bytes),
        "saved_path": str(out_file),
    }




@mcp.tool()
def m365_search_users(query: str, top: int = 10) -> Dict[str, Any]:
    """Search tenant user directory by display name, email, or userPrincipalName.

    Requires the admin tier (default sign-in doesn't request it) -- if this
    fails, a tenant administrator can either sign in with
    m365_initiate_login(scope_tier="admin") or grant the tier org-wide via
    m365_generate_admin_consent_url. For Teams recipients prefer the chat
    membership lookup, which works for every user.
    """
    clean_q = query.replace("'", "''")
    filter_expr = f"startswith(displayName,'{clean_q}') or startswith(mail,'{clean_q}') or startswith(userPrincipalName,'{clean_q}')"
    params = {
        "$top": min(top, 50),
        "$select": "id,displayName,mail,userPrincipalName,jobTitle,department",
        "$filter": filter_expr,
    }
    try:
        return _graph_request("GET", "/users", params=params)
    except Exception:
        # Fallback to $search with ConsistencyLevel: eventual header if $filter fails
        search_params = {
            "$top": min(top, 50),
            "$select": "id,displayName,mail,userPrincipalName,jobTitle,department",
            "$search": f'"{clean_q}"',
        }
        return _graph_request("GET", "/users", params=search_params, extra_headers={"ConsistencyLevel": "eventual"})


@mcp.tool()
def m365_get_chat_members(chat_id: str, raw: bool = False) -> Dict[str, Any]:
    """Get the members (participants) of a Microsoft Teams chat as {displayName, email, user_id}."""
    res = _graph_request("GET", f"/me/chats/{chat_id}/members")
    if raw or not isinstance(res, dict):
        return res
    members = [_compact_member(m) for m in (res.get("value") or []) if isinstance(m, dict)]
    return {"chat_id": chat_id, "count": len(members), "members": members}


@mcp.tool()
def m365_get_or_create_direct_chat(user_id_or_upn: str) -> Dict[str, Any]:
    """Get or create a 1:1 Teams chat with a person by name, email/UPN or Graph user id.

    Resolution order (works without directory rights): an existing 1:1 chat
    whose member matches → directory search (admin tier, skipped when
    forbidden) → the email/UPN itself. Returns the existing chat when one is
    found instead of creating a duplicate.
    """
    ident = _clean_query_text(user_id_or_upn)
    if not ident:
        return {"error": "user_id_or_upn is required (name, email/UPN or Graph user id)."}
    me = _my_identity()
    my_id = me.get("id")

    # 1. Existing 1:1 chat with that person (no directory permission needed).
    try:
        resolution = _resolve_teams_recipient(ident, prefer="oneOnOne", top=3)
    except Exception:
        resolution = {"resolution": "none", "candidates": []}
    if resolution.get("resolution") == "unique" and resolution["candidates"][0].get("chat_type", "").lower() == "oneonone":
        found = resolution["candidates"][0]
        return {"id": found["chat_id"], "chatType": "oneOnOne", "existing": True, "members": found.get("members", []), "match_reason": found.get("match_reason", "")}
    if resolution.get("resolution") == "ambiguous":
        return {
            "error": "several existing chats match — ask the user which person is meant",
            "candidates": resolution.get("candidates", []),
        }
    other_user: Optional[str] = None
    if resolution.get("resolution") == "unique":
        # Group chat matched: take the member id from it when it is a single other person.
        others = [m for m in resolution["candidates"][0].get("members", []) if (m.get("user_id") or "").lower() != (my_id or "").lower()]
        if len(others) == 1 and others[0].get("user_id"):
            other_user = others[0]["user_id"]

    # 2. Directory search (admin tier) — optional.
    if not other_user:
        try:
            search_res = m365_search_users(ident, top=1)
            users = search_res.get("value", []) if isinstance(search_res, dict) else []
            if users:
                other_user = users[0].get("id")
        except Exception:
            users = []
    # 3. Direct lookup / raw UPN.
    if not other_user and "@" in ident:
        try:
            user_by_upn = _graph_request("GET", f"/users/{ident}")
            if isinstance(user_by_upn, dict) and "id" in user_by_upn:
                other_user = user_by_upn["id"]
        except Exception:
            pass
        if not other_user:
            other_user = ident
    if not other_user:
        if _re.fullmatch(r"[0-9a-fA-F-]{32,36}", ident):
            other_user = ident
        else:
            return {
                "error": (
                    f"Could not resolve '{ident}': no existing chat with that person and no directory "
                    "access. Ask for the person's email address (or start the chat once in Teams)."
                ),
            }

    payload = {
        "chatType": "oneOnOne",
        "members": [
            {
                "@odata.type": "#microsoft.graph.aadUserConversationMember",
                "roles": ["owner"],
                "user@odata.bind": f"{GRAPH_API_BASE}/users/{my_id}",
            },
            {
                "@odata.type": "#microsoft.graph.aadUserConversationMember",
                "roles": ["owner"],
                "user@odata.bind": f"{GRAPH_API_BASE}/users/{other_user}",
            },
        ],
    }
    created = _graph_request("POST", "/chats", json_data=payload)
    if isinstance(created, dict):
        created.setdefault("existing", False)
    return created


@mcp.tool()
def m365_list_contacts(top: int = 20, search: Optional[str] = None) -> Dict[str, Any]:
    """List personal contacts from Outlook Contacts."""
    params = {"$top": min(top, 50)}
    if search:
        params["$search"] = f'"{search}"'
    return _graph_request("GET", "/me/contacts", params=params)


@mcp.tool()
def m365_list_sharepoint_sites(search: Optional[str] = None, top: int = 10) -> Dict[str, Any]:
    """List or search SharePoint sites in the tenant.

    Requires admin-consented scopes (default sign-in doesn't request them) --
    if this fails, call m365_initiate_login(request_admin_scopes=True) first
    (only meaningful if the signed-in account has tenant admin rights).
    """
    if search:
        endpoint = f"/sites?search={search}"
    else:
        endpoint = "/sites?search=*"
    return _graph_request("GET", endpoint, params={"$top": min(top, 50)})


@mcp.tool()
def m365_list_sharepoint_drives(site_id: str) -> Dict[str, Any]:
    """List document libraries (drives) for a SharePoint site."""
    return _graph_request("GET", f"/sites/{site_id}/drives")


@mcp.tool()
def m365_list_sharepoint_files(
    site_id: str,
    drive_id: str,
    folder_id: Optional[str] = None,
    top: int = 20,
) -> Dict[str, Any]:
    """List files in a SharePoint document library drive."""
    if folder_id:
        endpoint = f"/sites/{site_id}/drives/{drive_id}/items/{folder_id}/children"
    else:
        endpoint = f"/sites/{site_id}/drives/{drive_id}/root/children"
    res = _graph_request("GET", endpoint, params={"$top": min(top, 50)})
    if isinstance(res, dict) and "value" in res and isinstance(res["value"], list):
        for item in res["value"]:
            if isinstance(item, dict):
                _enrich_timestamps(item)
    return res


@mcp.tool()
def m365_search_sharepoint_files(site_id: str, query: str) -> Dict[str, Any]:
    """Search for files within a SharePoint site."""
    res = _graph_request("GET", f"/sites/{site_id}/drive/root/search(q='{query}')")
    if isinstance(res, dict) and "value" in res and isinstance(res["value"], list):
        for item in res["value"]:
            if isinstance(item, dict):
                _enrich_timestamps(item)
    return res


# ─── Teams Channels & Activity Feed Tools ───────────────────────────────────


@mcp.tool()
def m365_list_chat_messages(chat_id: str, top: int = 10, raw: bool = False) -> Dict[str, Any]:
    """List recent messages of a Teams chat as compact records {from, at, text, has_attachments}.

    Args:
        chat_id: The chat id (from m365_find_chat / m365_list_chats) or a Teams chat link.
        top: Number of most recent messages (capped at 50).
        raw: Return the unmodified Graph objects (with attachments_summary) instead.
    """
    chat_id = _coerce_chat_ref(chat_id) or chat_id
    params = {"$top": min(top, 50)}
    res = _graph_request("GET", f"/me/chats/{chat_id}/messages", params=params)
    if isinstance(res, dict) and "value" in res and isinstance(res["value"], list):
        res["value"] = [_enrich_teams_message(msg, chat_id=chat_id) for msg in res["value"]]
    if raw or not isinstance(res, dict):
        return res
    messages = [_compact_message(m) for m in (res.get("value") or []) if isinstance(m, dict)]
    messages = [m for m in messages if m.get("message_type", "message") == "message"]
    return {"chat_id": chat_id, "count": len(messages), "messages": messages}


_GREETING_RE = _re.compile(
    r"^(hi|hey|hallo|hello|moin moin|moin|servus|gr(ü|ue)(ß|ss) dich|gr(ü|ue)(ß|ss) gott|guten morgen|guten tag|good morning|"
    r"liebe[r]?|sehr geehrte[r]?|dear|na)\b",
    _re.IGNORECASE,
)
_SIGNOFF_RE = _re.compile(
    r"(?:^|[\s,.!])(vg|lg|bg|mfg|viele gr(ü|ue)(ß|ss)e|liebe gr(ü|ue)(ß|ss)e|beste gr(ü|ue)(ß|ss)e|sch(ö|oe)ne gr(ü|ue)(ß|ss)e|gru(ß|ss)|gr(ü|ue)(ß|ss)e|"
    r"danke( dir| euch| ihnen)?|merci|thanks|thx|cheers|best regards|kind regards|regards|best|bis (gleich|sp(ä|ae)ter|morgen|dann)|ciao|tsch(ü|ue)ss)"
    r"[\s!.,:)]*(?:[\wäöüÄÖÜß\-]+[\s!.,)]*){0,3}$",
    _re.IGNORECASE,
)
_EMOJI_RE = _re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\U0001F1E6-\U0001F1FF]")
_DU_RE = _re.compile(r"\b(du|dir|dich|dein|deine|deinen|deinem|deiner|euch|ihr)\b", _re.IGNORECASE)
_SIE_RE = _re.compile(r"\b(Sie|Ihnen|Ihr|Ihre|Ihrer|Ihrem|Ihren)\b")
_DE_STOPWORDS = {"und", "nicht", "ich", "das", "die", "der", "ist", "wir", "mit", "auch", "noch", "mal", "bitte", "danke", "kann", "wenn", "dann", "für", "fuer", "habe", "hab"}
_EN_STOPWORDS = {"the", "and", "you", "for", "with", "that", "this", "please", "thanks", "can", "will", "have", "just", "let", "know"}

_TEAMS_REGISTER_DEFAULTS = {
    "language": "match the user's request",
    "greeting": "none or a short first-name greeting",
    "address": "du",
    "sign_off": "none",
    "signature": False,
    "attribution": False,
    "typical_length_words": 25,
    "emoji": "rare",
    "formality": "casual",
    "tech_details": False,
    "notes": (
        "Teams is chat, not mail: no letter salutation, no closing formula, no signature, no "
        "implementation details, no claims about data or access the recipient cannot see."
    ),
}


def _derive_chat_style(my_messages: List[str]) -> Dict[str, Any]:
    """Derive a compact style profile from the user's own messages in a chat."""
    if not my_messages:
        return {"source_messages": 0, "defaults": dict(_TEAMS_REGISTER_DEFAULTS), "profile": None, "examples": []}
    greetings: Dict[str, int] = {}
    signoffs: Dict[str, int] = {}
    du_hits = sie_hits = 0
    emoji_msgs = 0
    exclamations = 0
    de_score = en_score = 0
    lengths: List[int] = []
    for text in my_messages:
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        if not lines:
            continue
        first, last = lines[0], lines[-1]
        m = _GREETING_RE.match(first)
        if m:
            greetings[m.group(1).lower()] = greetings.get(m.group(1).lower(), 0) + 1
        tail = " ".join(lines[-2:])[-80:] if len(lines) > 1 else last[-60:]
        sm = _SIGNOFF_RE.search(tail)
        if sm and (len(lines) > 1 or len(last.split()) > 3):
            key = sm.group(1).lower()
            signoffs[key] = signoffs.get(key, 0) + 1
        du_hits += len(_DU_RE.findall(text))
        sie_hits += len(_SIE_RE.findall(text))
        if _EMOJI_RE.search(text):
            emoji_msgs += 1
        exclamations += text.count("!")
        words = _re.findall(r"[\wäöüÄÖÜß']+", text)
        lengths.append(len(words))
        lowered = {w.lower() for w in words}
        de_score += len(lowered & _DE_STOPWORDS)
        en_score += len(lowered & _EN_STOPWORDS)
    n = max(1, len(my_messages))
    avg_len = round(sum(lengths) / max(1, len(lengths)))
    greeting = max(greetings, key=greetings.get) if greetings else "none"
    greeting_ratio = (greetings.get(greeting, 0) / n) if greetings else 0.0
    sign_off = max(signoffs, key=signoffs.get) if signoffs else "none"
    signoff_ratio = (signoffs.get(sign_off, 0) / n) if signoffs else 0.0
    if sie_hits > du_hits and sie_hits >= 2:
        address = "Sie"
    elif du_hits > 0:
        address = "du"
    else:
        address = "unknown"
    language = "de" if de_score >= en_score and de_score > 0 else ("en" if en_score > 0 else "unknown")
    emoji_ratio = emoji_msgs / n
    formality = "formal" if address == "Sie" or (greeting_ratio > 0.6 and signoff_ratio > 0.6 and avg_len > 60) else ("casual" if address == "du" or emoji_ratio > 0.2 or avg_len < 30 else "neutral")
    return {
        "source_messages": len(my_messages),
        "profile": {
            "language": language,
            "greeting": greeting if greeting_ratio >= 0.4 else "none",
            "greeting_frequency": round(greeting_ratio, 2),
            "address": address,
            "sign_off": sign_off if signoff_ratio >= 0.4 else "none",
            "sign_off_frequency": round(signoff_ratio, 2),
            "signature": False,
            "typical_length_words": avg_len,
            "emoji": "often" if emoji_ratio > 0.4 else ("sometimes" if emoji_ratio > 0.1 else "rare"),
            "exclamation_per_message": round(exclamations / n, 2),
            "formality": formality,
        },
        "defaults": dict(_TEAMS_REGISTER_DEFAULTS),
    }


@mcp.tool()
def m365_get_chat_style(
    chat_id: Optional[str] = None,
    to: Optional[str] = None,
    sample: int = 30,
    account: Optional[str] = None,
) -> Dict[str, Any]:
    """Derive how the user actually writes in a specific Teams chat (per-recipient "flavour").

    Reads the user's own recent messages in that chat and returns a compact
    style profile: language, greeting, du/Sie, sign-off, typical length, emoji
    use, formality, plus up to three short examples. With no history it returns
    the Teams register defaults (short, no letter salutation, no closing
    formula, no signature, no technical details). Save the profile as a
    `person` memory note ("Teams style with <Name>") and reuse it next time.

    Args:
        chat_id: Chat id; or pass `to` (name / email / topic) to resolve it.
        to: Recipient to resolve when chat_id is not known.
        sample: How many recent messages to inspect (capped at 50).
        account: Optional M365 account username, email, or ID.
    """
    target = _coerce_chat_ref(chat_id) or ""
    recipient: Optional[Dict[str, Any]] = None
    if not target and _parse_teams_link(_clean_query_text(to)):
        target = _coerce_chat_ref(to) or ""
    if not target:
        to_clean = _clean_query_text(to)
        if not to_clean:
            return {"error": "Pass chat_id or to (name / email / topic)."}
        resolution = _resolve_teams_recipient(to_clean, prefer="any", top=5, account=account)
        if resolution["resolution"] != "unique":
            return {"error": f"recipient {resolution['resolution']}", **resolution}
        target = resolution["chat_id"]
        recipient = resolution["candidates"][0]
    me = _my_identity(account=account)
    res = _graph_request("GET", f"/me/chats/{target}/messages", params={"$top": min(max(int(sample), 1), 50)}, account=account)
    raw_messages = (res.get("value") or []) if isinstance(res, dict) else []
    mine: List[str] = []
    theirs = 0
    for msg in raw_messages:
        if not isinstance(msg, dict) or (msg.get("messageType") or "message") != "message":
            continue
        user = (msg.get("from") or {}).get("user") or {}
        compact = _compact_message(msg)
        if not compact.get("text"):
            continue
        is_me = (str(user.get("id") or "").lower() == (me.get("id") or "").lower()) or (
            not user.get("id") and _norm_name(user.get("displayName")) == _norm_name(me.get("displayName"))
        )
        if is_me:
            mine.append(compact["text"])
        else:
            theirs += 1
    style = _derive_chat_style(mine)
    style["chat_id"] = target
    style["their_messages_seen"] = theirs
    style["examples"] = [_truncate(t, 200) for t in mine[:3]]
    if recipient:
        style["recipient"] = {k: recipient.get(k) for k in ("chat_type", "topic", "members", "match_reason")}
    style["how_to_use"] = (
        "Draft in this register. If profile is null, use defaults. Persist the profile as a person "
        "memory note titled 'Teams style with <Name>' (tag teams-style) so it is not re-derived."
    )
    return style


@mcp.tool()
def m365_list_joined_teams(top: int = 20) -> Dict[str, Any]:
    """List all Microsoft Teams that the current user is a member of."""
    res = _graph_request("GET", "/me/joinedTeams")
    if isinstance(res, dict) and "value" in res and isinstance(res["value"], list):
        res["value"] = res["value"][:min(top, 50)]
    return res


@mcp.tool()
def m365_list_team_channels(team_id: str) -> Dict[str, Any]:
    """List all channels in a specific Microsoft Team."""
    return _graph_request("GET", f"/teams/{team_id}/channels")


@mcp.tool()
def m365_list_channel_messages(team_id: str, channel_id: str, top: int = 10) -> Dict[str, Any]:
    """List recent messages in a specific Microsoft Teams channel."""
    params = {"$top": min(top, 50)}
    res = _graph_request("GET", f"/teams/{team_id}/channels/{channel_id}/messages", params=params)
    if isinstance(res, dict) and "value" in res and isinstance(res["value"], list):
        res["value"] = [_enrich_teams_message(msg, team_id=team_id, channel_id=channel_id) for msg in res["value"]]
    return res


@mcp.tool()
def m365_list_teams_message_attachments(
    message_id: str,
    chat_id: Optional[str] = None,
    team_id: Optional[str] = None,
    channel_id: Optional[str] = None,
) -> Dict[str, Any]:
    """List file attachments and inline images (hosted contents) for a specific Microsoft Teams chat or channel message.

    Args:
        message_id: The ID of the Teams message.
        chat_id: The Teams chat ID (for 1:1 or group chats).
        team_id: The Team ID (required if querying a channel message).
        channel_id: The Channel ID (required if querying a channel message).
    """
    chat_id = _coerce_chat_ref(chat_id)
    if not chat_id and not (team_id and channel_id):
        raise ValueError("Either chat_id OR both team_id and channel_id must be provided.")

    if chat_id:
        endpoint = f"/me/chats/{chat_id}/messages/{message_id}"
        hc_endpoint = f"/me/chats/{chat_id}/messages/{message_id}/hostedContents"
    else:
        endpoint = f"/teams/{team_id}/channels/{channel_id}/messages/{message_id}"
        hc_endpoint = f"/teams/{team_id}/channels/{channel_id}/messages/{message_id}/hostedContents"

    msg = _graph_request("GET", endpoint)
    attachments = []

    raw_atts = msg.get("attachments") if isinstance(msg, dict) else []
    if isinstance(raw_atts, list):
        for att in raw_atts:
            if isinstance(att, dict):
                attachments.append({
                    "id": att.get("id"),
                    "name": att.get("name") or "Unnamed Attachment",
                    "contentType": att.get("contentType"),
                    "contentUrl": att.get("contentUrl"),
                    "type": "file_reference",
                })

    try:
        hc_res = _graph_request("GET", hc_endpoint)
        if isinstance(hc_res, dict) and "value" in hc_res and isinstance(hc_res["value"], list):
            for hc in hc_res["value"]:
                if isinstance(hc, dict):
                    hc_id = hc.get("id")
                    attachments.append({
                        "id": hc_id,
                        "name": f"inline_image_{hc_id[:8]}.png" if hc_id else "inline_image.png",
                        "contentType": hc.get("contentType") or "image/png",
                        "type": "hosted_content",
                        "hosted_content_id": hc_id,
                    })
    except Exception:
        pass

    return {
        "message_id": message_id,
        "chat_id": chat_id,
        "team_id": team_id,
        "channel_id": channel_id,
        "attachments_count": len(attachments),
        "attachments": attachments,
    }


@mcp.tool()
def m365_download_teams_message_attachment(
    message_id: str,
    chat_id: Optional[str] = None,
    team_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    hosted_content_id: Optional[str] = None,
    attachment_id: Optional[str] = None,
    attachment_name: Optional[str] = None,
    content_url: Optional[str] = None,
    save_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Download a Teams message attachment (inline image/hostedContent or file reference) to the local workspace/Vault.

    Args:
        message_id: The ID of the Teams message.
        chat_id: The Teams chat ID (for 1:1 or group chats).
        team_id: The Team ID (required if channel message).
        channel_id: The Channel ID (required if channel message).
        hosted_content_id: ID of the hosted content (inline image), if downloading an inline image.
        attachment_id: ID of the attachment in the message's attachments array.
        attachment_name: Name of the attachment file.
        content_url: Direct SharePoint / OneDrive content URL from the attachment object.
        save_path: Optional local destination path. Defaults to Vault or ./attachments/<filename>.
    """
    chat_id = _coerce_chat_ref(chat_id)
    if not chat_id and not (team_id and channel_id):
        raise ValueError("Either chat_id OR both team_id and channel_id must be provided.")

    content_bytes = None
    filename = attachment_name or "teams_attachment"

    # Case 1: Hosted Content (Inline image)
    if hosted_content_id:
        if chat_id:
            hc_url = f"/me/chats/{chat_id}/messages/{message_id}/hostedContents/{hosted_content_id}/$value"
        else:
            hc_url = f"/teams/{team_id}/channels/{channel_id}/messages/{message_id}/hostedContents/{hosted_content_id}/$value"
        content_bytes = _graph_download_bytes(hc_url)
        if not attachment_name:
            filename = f"inline_image_{hosted_content_id[:8]}.png"

    # Case 2: File attachment lookup or content_url
    else:
        target_url = content_url
        if not target_url and (attachment_id or attachment_name):
            atts_info = m365_list_teams_message_attachments(
                message_id=message_id, chat_id=chat_id, team_id=team_id, channel_id=channel_id
            )
            for att in atts_info.get("attachments", []):
                if (attachment_id and att.get("id") == attachment_id) or \
                   (attachment_name and att.get("name") == attachment_name):
                    if att.get("type") == "hosted_content":
                        hosted_content_id = att.get("hosted_content_id")
                        if chat_id:
                            hc_url = f"/me/chats/{chat_id}/messages/{message_id}/hostedContents/{hosted_content_id}/$value"
                        else:
                            hc_url = f"/teams/{team_id}/channels/{channel_id}/messages/{message_id}/hostedContents/{hosted_content_id}/$value"
                        content_bytes = _graph_download_bytes(hc_url)
                        if not attachment_name:
                            filename = att.get("name") or "inline_image.png"
                        break
                    else:
                        target_url = att.get("contentUrl")
                        filename = att.get("name") or filename
                        break

        if content_bytes is None and target_url:
            b64_url = base64.urlsafe_b64encode(target_url.encode("utf-8")).decode("utf-8").rstrip("=")
            share_token = f"u!{b64_url}"
            try:
                content_bytes = _graph_download_bytes(f"/shares/{share_token}/driveItem/content")
            except Exception:
                if filename:
                    try:
                        search_res = _graph_download_bytes(f"/me/drive/root:/Microsoft Teams Chat Files/{filename}:/content")
                        if search_res:
                            content_bytes = search_res
                    except Exception:
                        pass

    if content_bytes is None:
        raise RuntimeError(f"Failed to locate or download attachment for Teams message '{message_id}'. Ensure a valid hosted_content_id, attachment_id, attachment_name, or content_url is provided.")

    out_file = _resolve_save_path(save_path, filename, subfolder="documents/m365_attachments")
    out_file.write_bytes(content_bytes)
    return {
        "success": True,
        "message_id": message_id,
        "filename": filename,
        "saved_path": str(out_file),
        "size_bytes": len(content_bytes),
    }


def _download_file_reference(content_url: str, filename: str) -> Optional[bytes]:
    """Download a Teams file reference (SharePoint/OneDrive URL) via the shares API."""
    if not content_url:
        return None
    b64_url = base64.urlsafe_b64encode(content_url.encode("utf-8")).decode("utf-8").rstrip("=")
    try:
        return _graph_download_bytes(f"/shares/u!{b64_url}/driveItem/content")
    except Exception:
        if filename:
            try:
                return _graph_download_bytes(f"/me/drive/root:/Microsoft Teams Chat Files/{filename}:/content")
            except Exception:
                return None
    return None


@mcp.tool()
def m365_download_chat_files(
    chat_id: Optional[str] = None,
    to: Optional[str] = None,
    last: int = 5,
    include_images: bool = False,
    save_dir: Optional[str] = None,
    account: Optional[str] = None,
) -> Dict[str, Any]:
    """Download the files shared in a Teams chat (chat link, chat id or person) into the Vault.

    Scans the last ``last`` messages of the chat for file attachments (Word,
    PDF, Excel, … shared via OneDrive/SharePoint) and, optionally, inline
    images, downloads each into ``documents/m365_attachments/<chat>/`` of the
    Vault (or ``save_dir``) and returns the saved paths together with sender
    and time. Use this when the user asks for "the document from the chat" —
    pass the pasted Teams link as ``chat_id`` or the person/topic as ``to``.

    Args:
        chat_id: Chat id or a Teams chat/message link (https://teams.microsoft.com/l/chat/... or /l/message/...).
        to: Person name, nickname, email or group topic when no id/link is at hand.
        last: How many recent messages to scan (1-50, default 5).
        include_images: Also download inline images (screenshots pasted into the chat).
        save_dir: Optional local directory; defaults to the Vault.
        account: Optional M365 account username, email, or ID.
    """
    linked = _parse_teams_link(_clean_query_text(chat_id)) or _parse_teams_link(_clean_query_text(to))
    target = _coerce_chat_ref(chat_id) or ""
    recipient: Optional[Dict[str, Any]] = None
    if not target and _parse_teams_link(_clean_query_text(to)):
        target = _coerce_chat_ref(to) or ""
    if not target:
        to_clean = _clean_query_text(to)
        if not to_clean:
            return {"error": "Pass chat_id (id or Teams link) or to (name / email / topic)."}
        resolution = _resolve_teams_recipient(to_clean, prefer="any", top=5, account=account)
        if resolution["resolution"] != "unique":
            return {"error": f"recipient {resolution['resolution']}", **resolution}
        target = resolution["chat_id"]
        recipient = resolution["candidates"][0]

    top = min(max(int(last or 5), 1), 50)
    res = _graph_request("GET", f"/me/chats/{target}/messages", params={"$top": top}, account=account)
    raw_messages = [m for m in ((res.get("value") or []) if isinstance(res, dict) else []) if isinstance(m, dict)]
    only_message_id = linked.get("message_id") if linked else None

    # Folder name: group topic or the other person's name.
    folder_label = ""
    try:
        chat_meta = recipient or _compact_chat(_graph_request("GET", f"/me/chats/{target}", params={"$expand": "members"}, account=account))
        me = _my_identity(account=account)
        others = [m.get("displayName") for m in chat_meta.get("members", []) if m.get("displayName") and _norm_name(m.get("email")) != _norm_name(me.get("upn"))]
        folder_label = chat_meta.get("topic") or ", ".join(others[:2])
    except Exception:
        folder_label = ""
    subfolder = f"documents/m365_attachments/{_slugify_path_component(folder_label, fallback=target[:24])}"

    scanned = 0
    files: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for msg in raw_messages:
        if (msg.get("messageType") or "message") != "message":
            continue
        if only_message_id and str(msg.get("id")) != str(only_message_id):
            continue
        scanned += 1
        enriched = _enrich_teams_message(dict(msg), chat_id=target)
        compact = _compact_message(enriched)
        for att in enriched.get("attachments_summary") or []:
            att_type = att.get("type")
            name = att.get("name") or "attachment"
            if att_type == "hosted_content":
                if not include_images:
                    continue
                hc_id = att.get("hosted_content_id") or att.get("id")
                try:
                    data = _graph_download_bytes(f"/me/chats/{target}/messages/{msg.get('id')}/hostedContents/{hc_id}/$value", account=account)
                except Exception as exc:
                    errors.append({"message_id": msg.get("id"), "name": name, "error": str(exc)[:200]})
                    continue
            elif att_type in ("file_reference", "attachment"):
                content_type = str(att.get("contentType") or "")
                if att.get("contentUrl") is None and "reference" not in content_type:
                    continue  # adaptive cards, message references etc.
                data = _download_file_reference(att.get("contentUrl") or "", name)
                if data is None:
                    errors.append({"message_id": msg.get("id"), "name": name, "error": "download failed (shares API and Teams Chat Files folder)"})
                    continue
            else:
                continue
            if save_dir:
                out_dir = Path(save_dir).expanduser().resolve()
                out_dir.mkdir(parents=True, exist_ok=True)
                out_file = out_dir / name
            else:
                out_file = _resolve_save_path(None, name, subfolder=subfolder)
            if out_file.exists():
                stem, suffix = out_file.stem, out_file.suffix
                counter = 2
                while out_file.exists():
                    out_file = out_file.with_name(f"{stem} ({counter}){suffix}")
                    counter += 1
            out_file.write_bytes(data)
            files.append({
                "name": name,
                "saved_path": str(out_file),
                "size_bytes": len(data),
                "content_type": att.get("contentType") or "",
                "from": compact.get("from") or "",
                "at": compact.get("at") or "",
                "message_id": msg.get("id"),
                "source_url": att.get("contentUrl") or "",
            })

    result: Dict[str, Any] = {
        "chat_id": target,
        "messages_scanned": scanned,
        "files": files,
        "count": len(files),
        "errors": errors,
    }
    if recipient:
        result["recipient"] = {k: recipient.get(k) for k in ("chat_type", "topic", "members")}
    if not files:
        result["hint"] = (
            "No downloadable files in the scanned messages. Increase `last`, set include_images=True "
            "for pasted screenshots, or use m365_list_chat_messages to see which message carries the file."
        )
    return result


@mcp.tool()
def m365_get_activity_feed(top_chats: int = 5, top_messages_per_chat: int = 3) -> Dict[str, Any]:
    """Get an aggregated Activity Feed across recent 1:1 DMs, group chats, and joined Teams channels.
    
    Inspects recent active chats and teams channels to return a combined overview of recent messages.
    """
    activity = {
        "recent_chats": [],
        "team_channels": [],
        "errors": [],
    }

    # 1. Fetch recent chats
    try:
        chats_res = _graph_request("GET", "/me/chats", params={"$top": min(top_chats, 15)})
        chats = chats_res.get("value", [])
        for c in chats:
            chat_id = c.get("id")
            topic = c.get("topic") or c.get("chatType")
            if not chat_id:
                continue
            try:
                msgs_res = _graph_request(
                    "GET",
                    f"/me/chats/{chat_id}/messages",
                    params={"$top": min(top_messages_per_chat, 10)},
                )
                msgs = msgs_res.get("value", [])
                activity["recent_chats"].append({
                    "chat_id": chat_id,
                    "topic": topic,
                    "chat_type": c.get("chatType"),
                    "last_updated": _format_timestamp_local(c.get("lastUpdatedDateTime")),
                    "recent_messages": [
                        {
                            "id": m.get("id"),
                            "from": m.get("from", {}).get("user", {}).get("displayName"),
                            "created_at": _format_timestamp_local(m.get("createdDateTime")),
                            "body_preview": m.get("body", {}).get("content", "")[:200],
                        }
                        for m in msgs if m.get("messageType") == "message"
                    ],
                })
            except Exception as err:
                activity["errors"].append(f"Chat {chat_id} messages error: {err}")
    except Exception as err:
        activity["errors"].append(f"List chats error: {err}")

    # 2. Fetch joined teams & their primary/active channels
    try:
        teams_res = _graph_request("GET", "/me/joinedTeams")
        teams = teams_res.get("value", [])[:10]
        for t in teams:
            team_id = t.get("id")
            team_name = t.get("displayName")
            if not team_id:
                continue
            try:
                channels_res = _graph_request("GET", f"/teams/{team_id}/channels")
                channels = channels_res.get("value", [])
                for ch in channels[:3]:  # Top channels per team
                    ch_id = ch.get("id")
                    ch_name = ch.get("displayName")
                    if not ch_id:
                        continue
                    try:
                        ch_msgs_res = _graph_request(
                            "GET",
                            f"/teams/{team_id}/channels/{ch_id}/messages",
                            params={"$top": min(top_messages_per_chat, 5)},
                        )
                        ch_msgs = ch_msgs_res.get("value", [])
                        activity["team_channels"].append({
                            "team_id": team_id,
                            "team_name": team_name,
                            "channel_id": ch_id,
                            "channel_name": ch_name,
                            "recent_messages": [
                                {
                                    "id": m.get("id"),
                                    "from": m.get("from", {}).get("user", {}).get("displayName"),
                                    "created_at": _format_timestamp_local(m.get("createdDateTime")),
                                    "body_preview": m.get("body", {}).get("content", "")[:200],
                                }
                                for m in ch_msgs if m.get("messageType") == "message"
                            ],
                        })
                    except Exception:
                        pass
            except Exception as err:
                activity["errors"].append(f"Team {team_name} channels error: {err}")
    except Exception as err:
        activity["errors"].append(f"List teams error: {err}")

    return activity


@mcp.tool()
def m365_list_teams_calls(
    top_chats: int = 15,
    top_messages_per_chat: int = 20,
    search_query: Optional[str] = None,
) -> Dict[str, Any]:
    """Query recent Teams 1:1 calls, group calls, and online meeting calls across chat history and calendar.

    Extracts call history (start time, duration, participants, call type: groupCall / oneOnOne / meeting)
    from Teams system messages and online meetings so you know when and with whom calls took place.

    Args:
        top_chats: Max number of recent chats to inspect for call events.
        top_messages_per_chat: Max messages per chat to inspect.
        search_query: Optional filter string to match participant name or call topic.
    """
    calls = []
    seen_call_ids = set()

    # 1. Scan chats for call event details
    try:
        chats_res = _graph_request("GET", "/me/chats", params={"$top": min(top_chats, 30)})
        chats = chats_res.get("value", []) if isinstance(chats_res, dict) else []
        for c in chats:
            chat_id = c.get("id")
            topic = c.get("topic") or c.get("chatType") or "Teams Chat"
            chat_type = c.get("chatType")
            if not chat_id:
                continue

            try:
                msgs_res = _graph_request(
                    "GET",
                    f"/me/chats/{chat_id}/messages",
                    params={"$top": min(top_messages_per_chat, 50)},
                )
                msgs = msgs_res.get("value", []) if isinstance(msgs_res, dict) else []

                for m in msgs:
                    if not isinstance(m, dict):
                        continue

                    evt_detail = m.get("eventDetail")
                    msg_type = m.get("messageType")
                    body_content = (m.get("body") or {}).get("content", "")

                    # Check for explicit eventDetail (callEnded, callStarted, etc.)
                    if isinstance(evt_detail, dict):
                        odata_type = str(evt_detail.get("@odata.type") or "").lower()
                        is_call = "call" in odata_type or "meeting" in odata_type
                        if is_call:
                            call_id = m.get("id")
                            if call_id and call_id in seen_call_ids:
                                continue
                            if call_id:
                                seen_call_ids.add(call_id)

                            dur_seconds = evt_detail.get("callDuration")
                            dur_str = ""
                            if isinstance(dur_seconds, (int, float)):
                                mins = int(dur_seconds // 60)
                                secs = int(dur_seconds % 60)
                                dur_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
                            elif isinstance(dur_seconds, str):
                                dur_str = dur_seconds

                            participants = []
                            raw_parts = evt_detail.get("callParticipants") or []
                            if isinstance(raw_parts, list):
                                for p in raw_parts:
                                    if isinstance(p, dict):
                                        p_user = p.get("user") or p.get("target") or {}
                                        p_name = p_user.get("displayName") or p_user.get("id")
                                        if p_name and p_name not in participants:
                                            participants.append(p_name)

                            initiator = (evt_detail.get("initiator") or {}).get("user", {}).get("displayName") or m.get("from", {}).get("user", {}).get("displayName") or "Unknown"

                            calls.append({
                                "chat_id": chat_id,
                                "chat_topic": topic,
                                "chat_type": chat_type,
                                "call_type": evt_detail.get("callEventType") or ("groupCall" if chat_type == "group" else "oneOnOne"),
                                "event_type": odata_type.split(".")[-1].replace("MessageDetail", ""),
                                "created_at_utc": m.get("createdDateTime"),
                                "created_at_local": _format_timestamp_local(m.get("createdDateTime")),
                                "duration": dur_str or "N/A",
                                "duration_seconds": dur_seconds,
                                "initiator": initiator,
                                "participants": participants,
                            })

                    # Fallback check for call notification system messages in body
                    elif msg_type == "systemEventMessage" or "call" in body_content.lower():
                        if "started a call" in body_content.lower() or "call ended" in body_content.lower() or "group call" in body_content.lower():
                            call_id = m.get("id")
                            if call_id and call_id not in seen_call_ids:
                                seen_call_ids.add(call_id)
                                calls.append({
                                    "chat_id": chat_id,
                                    "chat_topic": topic,
                                    "chat_type": chat_type,
                                    "call_type": "groupCall" if chat_type == "group" else "oneOnOne",
                                    "event_type": "systemCallNotice",
                                    "created_at_utc": m.get("createdDateTime"),
                                    "created_at_local": _format_timestamp_local(m.get("createdDateTime")),
                                    "body_summary": body_content[:150],
                                    "from": m.get("from", {}).get("user", {}).get("displayName") or "System",
                                })
            except Exception:
                pass
    except Exception:
        pass

    # 2. Check scheduled / online meetings in calendar with Teams provider
    try:
        from datetime import datetime, timedelta
        now = datetime.now()
        start_search = (now - timedelta(days=14)).isoformat()
        end_search = (now + timedelta(days=7)).isoformat()
        cal_events = m365_get_events(start_time_iso=start_search, end_time_iso=end_search, top=50)

        for evt in cal_events.get("value", []):
            if not isinstance(evt, dict):
                continue
            is_teams = evt.get("isOnlineMeeting") or "teams" in str(evt.get("location", {})).lower() or "join.teams" in str(evt).lower()
            if is_teams:
                evt_id = evt.get("id")
                if evt_id and evt_id not in seen_call_ids:
                    seen_call_ids.add(evt_id)
                    attendees_list = [
                        a.get("emailAddress", {}).get("name") or a.get("emailAddress", {}).get("address")
                        for a in evt.get("attendees", []) if isinstance(a, dict)
                    ]
                    calls.append({
                        "event_id": evt_id,
                        "chat_topic": evt.get("subject"),
                        "call_type": "scheduledMeeting",
                        "event_type": "onlineMeeting",
                        "start_time_local": evt.get("start_local") or _format_timestamp_local(evt.get("start")),
                        "end_time_local": evt.get("end_local") or _format_timestamp_local(evt.get("end")),
                        "organizer": (evt.get("organizer") or {}).get("emailAddress", {}).get("name"),
                        "participants": attendees_list,
                        "is_online_meeting": True,
                    })
    except Exception:
        pass

    # Optional search filtering
    if search_query:
        q_lower = search_query.lower()
        filtered = []
        for c in calls:
            topic_match = q_lower in str(c.get("chat_topic", "")).lower()
            initiator_match = q_lower in str(c.get("initiator", "")).lower()
            part_match = any(q_lower in str(p).lower() for p in c.get("participants", []))
            if topic_match or initiator_match or part_match:
                filtered.append(c)
        calls = filtered

    return {
        "count": len(calls),
        "calls": calls,
    }


@mcp.tool()
def m365_get_user_presence(user_id_or_upn: Optional[str] = None) -> Dict[str, Any]:
    """Get real-time presence, call, and availability status (e.g. InACall, InAMeeting, Busy, Available, Offline).

    Args:
        user_id_or_upn: Optional user ID or email/UPN. If omitted, returns current user's presence.
    """
    if user_id_or_upn and str(user_id_or_upn).strip():
        uid = str(user_id_or_upn).strip()
        if "@" in uid:
            search_res = m365_search_users(uid, top=1)
            users = search_res.get("value", [])
            if users:
                uid = users[0].get("id")
        endpoint = f"/users/{uid}/presence"
    else:
        endpoint = "/me/presence"

    return _graph_request("GET", endpoint)


@mcp.tool()
def m365_get_schedule(
    schedules: List[str],
    start_time_iso: str,
    end_time_iso: str,
    availability_view_interval: int = 30,
) -> Dict[str, Any]:
    """Get free/busy schedule availability for one or more users (colleagues/teammates).

    Used to find common free time slots for scheduling meetings without inspecting private details.

    Args:
        schedules: List of user email addresses or UPNs to check availability for.
        start_time_iso: Start date/time in ISO format.
        end_time_iso: End date/time in ISO format.
        availability_view_interval: Minutes per slot in the availability view (default: 30).
    """
    tz_name = _get_timezone_name()
    start_clean, _ = _normalize_datetime_input(start_time_iso)
    end_clean, _ = _normalize_datetime_input(end_time_iso)

    payload = {
        "schedules": [s.strip() for s in schedules],
        "startTime": {"dateTime": start_clean, "timeZone": tz_name},
        "endTime": {"dateTime": end_clean, "timeZone": tz_name},
        "availabilityViewInterval": availability_view_interval,
    }
    res = _graph_request("POST", "/me/calendar/getSchedule", json_data=payload)

    if isinstance(res, dict) and "value" in res and isinstance(res["value"], list):
        for item in res["value"]:
            if isinstance(item, dict) and "scheduleItems" in item and isinstance(item["scheduleItems"], list):
                for s_item in item["scheduleItems"]:
                    if isinstance(s_item, dict):
                        if "start" in s_item:
                            s_item["start_local"] = _format_timestamp_local(s_item.get("start"))
                        if "end" in s_item:
                            s_item["end_local"] = _format_timestamp_local(s_item.get("end"))

    return res


@mcp.tool()
def m365_list_todo_tasks(
    list_id: Optional[str] = None,
    top: int = 20,
) -> Dict[str, Any]:
    """List task lists and tasks in Microsoft To Do / Outlook Tasks.

    Args:
        list_id: Optional task list ID. If omitted, returns all task lists and tasks from the default list.
        top: Max number of tasks to return.
    """
    if not list_id:
        lists_res = _graph_request("GET", "/me/todo/lists")
        lists = lists_res.get("value", []) if isinstance(lists_res, dict) else []
        default_list = next((l for l in lists if l.get("wellknownListName") == "defaultList"), lists[0] if lists else None)
        target_list_id = default_list.get("id") if default_list else None
    else:
        target_list_id = list_id
        lists = []

    tasks = []
    if target_list_id:
        endpoint = f"/me/todo/lists/{target_list_id}/tasks"
        params = {"$top": min(top, 50)}
        tasks_res = _graph_request("GET", endpoint, params=params)
        tasks = tasks_res.get("value", []) if isinstance(tasks_res, dict) else []

        for t in tasks:
            if isinstance(t, dict):
                _enrich_timestamps(t)

    return {
        "lists": lists,
        "active_list_id": target_list_id,
        "count": len(tasks),
        "tasks": tasks,
    }


@mcp.tool()
def m365_create_todo_task(
    title: str,
    list_id: Optional[str] = None,
    due_date_iso: Optional[str] = None,
    body: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new task in Microsoft To Do / Outlook Tasks.

    Args:
        title: Task title/summary.
        list_id: Optional task list ID (defaults to user's default To Do list).
        due_date_iso: Optional due date/time in ISO format.
        body: Optional task notes/description.
    """
    if not list_id:
        lists_res = _graph_request("GET", "/me/todo/lists")
        lists = lists_res.get("value", []) if isinstance(lists_res, dict) else []
        default_list = next((l for l in lists if l.get("wellknownListName") == "defaultList"), lists[0] if lists else None)
        target_list_id = default_list.get("id") if default_list else "tasks"
    else:
        target_list_id = list_id

    tz_name = _get_timezone_name()
    payload: Dict[str, Any] = {"title": title}

    if body:
        payload["body"] = {"contentType": "text", "content": body}

    if due_date_iso:
        clean_due, tz_due = _normalize_datetime_input(due_date_iso)
        payload["dueDateTime"] = {"dateTime": clean_due, "timeZone": tz_due}

    return _graph_request("POST", f"/me/todo/lists/{target_list_id}/tasks", json_data=payload)


@mcp.tool()
def m365_get_mailbox_settings() -> Dict[str, Any]:
    """Get Outlook mailbox settings including Out-Of-Office / Automatic Reply status, working hours, and language."""
    return _graph_request("GET", "/me/mailboxSettings")


if __name__ == "__main__":
    if "--login" in sys.argv:
        token = _get_access_token()
        print(f"Token acquired successfully ({len(token)} chars). You can now use MSOffice365MCP in Hermes.")
    else:
        mcp.run()
