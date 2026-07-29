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
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import msal
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP Server
mcp = FastMCP("MSOffice365MCP")

# Constants
GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

# Scopes every regular tenant member can consent to themselves (Microsoft
# Graph does not require a tenant admin to approve these as delegated
# permissions). Covers Mail/Calendar/Teams/OneDrive/Contacts -- the vast
# majority of what this MCP is used for.
BASE_SCOPES = [
    "User.Read",
    "Mail.ReadWrite",
    "Mail.Send",
    "Calendars.ReadWrite",
    "Chat.ReadWrite",
    "Files.ReadWrite.All",
    "Contacts.ReadWrite",
]

# Scopes Microsoft classifies as requiring tenant-admin consent, because
# they grant read/write access beyond the signed-in user (directory-wide
# user search, org-wide SharePoint). Requesting these in the default
# consent screen blocks sign-in entirely for non-admin users -- reproduced
# via a customer test where a brand-new (non-admin) account got stuck on
# an admin-approval wall just to send mail. Only requested when the caller
# opts in (m365_initiate_login(request_admin_scopes=True)) or when a tenant
# admin has already granted them org-wide (m365_generate_admin_consent_url),
# in which case silent token acquisition below picks them up automatically
# without any extra prompt.
ADMIN_SCOPES = [
    "User.Read.All",
    "Directory.Read.All",
    "Sites.ReadWrite.All",
]

ALL_SCOPES = BASE_SCOPES + ADMIN_SCOPES

# Backwards-compatible alias: some tools (admin consent URL generation) and
# any external callers still importing SCOPES directly should get the full
# superset, since that tool is specifically for granting everything at once.
SCOPES = ALL_SCOPES

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


try:
    from hermes_cli.m365_auth import (
        get_m365_token_cache_path as _get_token_cache_path,
        get_msal_app as _get_msal_app,
        save_msal_cache as _save_msal_cache,
        translate_aadsts_error as _translate_aadsts_error,
    )

    def _save_cache(app: msal.PublicClientApplication) -> None:
        """Persist the MSAL token cache atomically."""
        _save_msal_cache(app, cache_path=_get_token_cache_path())
except ImportError:
    def _translate_aadsts_error(err: str) -> str:
        return ""

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
        client_id = custom_client_id or "41c29967-8ee6-4fac-b484-e87460272bda"  # Microsoft Intune / Office multi-tenant app ID
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
        """Persist the MSAL token cache atomically."""
        _save_msal_cache(app, cache_path=_get_token_cache_path())


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
            # Try the full scope superset first: if a tenant admin has
            # already granted org-wide consent (m365_generate_admin_consent_url),
            # this silently succeeds with zero extra prompts for every user
            # in that tenant. Otherwise fall back to the base (non-admin)
            # scopes so mail/calendar/chat/files keep working even when
            # nobody has admin rights.
            result = app.acquire_token_silent(ALL_SCOPES, account=acc)
            if not result or "access_token" not in result:
                result = app.acquire_token_silent(BASE_SCOPES, account=acc)
            if result and "access_token" in result:
                _save_cache(app)
                return result["access_token"]

    # 2. Legacy fallback: explicit M365_ACCESS_TOKEN env var if set and no cached MSAL account exists
    direct_token = os.environ.get("M365_ACCESS_TOKEN")
    if direct_token:
        return direct_token

    # 2. Check if running in interactive --login CLI mode
    if "--login" in sys.argv:
        request_admin_scopes = "--admin" in sys.argv or bool(os.environ.get("M365_REQUEST_ADMIN_SCOPES"))
        login_scopes = ALL_SCOPES if request_admin_scopes else BASE_SCOPES
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

    # 3. Running inside stdio MCP without cached token -> Return clear actionable error rather than blocking stdio!
    raise RuntimeError(
        "M365 authentication required. Call the m365_initiate_login tool "
        "(then m365_complete_login with the returned device code) to sign "
        "in interactively, or tell the user to open Hermes: Einstellungen "
        "-> Anbieter -> Konten -> 'Microsoft 365 (OAuth)' -> Connect, and "
        "follow the printed device-code instructions there. Tenant admins "
        "who also want directory search / SharePoint tools can pass "
        "request_admin_scopes=True to m365_initiate_login."
    )


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
    url = f"{GRAPH_API_BASE}{endpoint}" if not endpoint.startswith("http") else endpoint

    with httpx.Client(timeout=30.0) as client:
        response = client.request(method, url, headers=headers, json=json_data, params=params)
        if response.status_code == 204:
            return {"success": True}
        if response.is_error:
            hint = ""
            if response.status_code == 403 or "Authorization_RequestDenied" in response.text:
                hint = (
                    " (this typically means the signed-in account is missing an admin-consented "
                    "scope -- see m365_initiate_login's request_admin_scopes option)"
                )
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


def _resolve_attachment_path(file_path: str) -> Path:
    path = Path(file_path).expanduser()
    if not path.is_file():
        raise ValueError(f"Attachment file not found: {file_path}")
    return path


def _upload_file_to_onedrive(file_path: str, folder: str = _ONEDRIVE_ATTACHMENTS_FOLDER) -> Dict[str, Any]:
    """Upload a local file to the signed-in user's OneDrive and return the created driveItem
    (id, name, webUrl, ...), used as the basis for a Teams chat file attachment reference."""
    path = _resolve_attachment_path(file_path)
    file_size = path.stat().st_size
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    safe_folder = folder.strip("/")
    item_path = f"/me/drive/root:/{safe_folder}/{path.name}:"

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
) -> Dict[str, Any]:
    """Generate an Admin Consent URL for tenant administrators to grant organization-wide permissions for the configured App Registration."""
    client_id = (
        os.environ.get("M365_CLIENT_ID")
        or os.environ.get("OUTLOOK_CLIENT_ID")
        or os.environ.get("TEAMS_CLIENT_ID")
    )
    tenant_id = (
        os.environ.get("M365_TENANT_ID")
        or os.environ.get("OUTLOOK_TENANT_ID")
        or os.environ.get("TEAMS_TENANT_ID")
        or "common"
    )

    if not client_id:
        return {
            "error": "M365_CLIENT_ID environment variable is not set. Please set M365_CLIENT_ID and M365_TENANT_ID first."
        }

    import urllib.parse
    scopes_str = " ".join(SCOPES)
    consent_url = (
        f"https://login.microsoftonline.com/{tenant_id}/v2.0/adminconsent"
        f"?client_id={client_id}"
        f"&scope={urllib.parse.quote(scopes_str)}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
    )

    return {
        "success": True,
        "client_id": client_id,
        "tenant_id": tenant_id,
        "admin_consent_url": consent_url,
        "instructions": (
            "Open this URL in a browser as a Tenant Administrator (Global Admin / App Admin) "
            "to grant permissions for all users in the tenant with a single click:\n\n"
            f"{consent_url}"
        ),
    }


@mcp.tool()
def m365_initiate_login(request_admin_scopes: bool = False) -> Dict[str, Any]:
    """Initiate interactive Microsoft 365 OAuth sign-in flow directly via Device Code Flow or Browser Link from Hermes.

    Args:
        request_admin_scopes: Most users should leave this False -- it requests
            only the scopes every tenant member can consent to themselves
            (mail, calendar, chat, files, contacts). Set True only when the
            signed-in user is a tenant admin and wants directory-wide user
            search / SharePoint tools too; Microsoft will show an admin-consent
            prompt for those extra scopes during sign-in.
    """
    app = _get_msal_app()
    scopes = ALL_SCOPES if request_admin_scopes else BASE_SCOPES
    flow = app.initiate_device_flow(scopes=scopes)
    if "user_code" in flow:
        return {
            "status": "pending",
            "device_code": flow["user_code"],
            "verification_url": flow["verification_uri"],
            "requested_admin_scopes": request_admin_scopes,
            "message": (
                f"Please open {flow['verification_uri']} in your browser and enter the code: "
                f"**{flow['user_code']}**\n"
                "Once completed, run `m365_complete_login` or call any M365 tool to verify login."
            ),
            "flow_data": flow,
        }
    return {"error": "Failed to initiate device flow", "details": flow}


@mcp.tool()
def m365_complete_login(flow_data: Dict[str, Any]) -> Dict[str, Any]:
    """Complete the Microsoft 365 OAuth sign-in flow after the user entered the code in browser."""
    app = _get_msal_app()
    result = app.acquire_token_by_device_flow(flow_data)
    if "access_token" in result:
        _save_cache(app)
        return {
            "success": True,
            "message": "Sign-in successful! Token cached in ~/.hermes/m365_token_cache.bin",
            "account": result.get("id_token_claims", {}).get("preferred_username"),
        }
    err_text = str(result)
    hint = _translate_aadsts_error(err_text)
    return {"error": f"Sign-in incomplete or failed.{hint}", "details": result}


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

    Requires the admin-only scopes (see m365_initiate_login's
    request_admin_scopes) -- if the current sign-in only has the base
    scopes, this returns a hint to re-login with elevated scopes instead
    of a raw Graph permission error.
    """
    try:
        member_of = _graph_request("GET", "/me/memberOf")
    except RuntimeError as err:
        if "403" in str(err) or "InsufficientPrivileges" in str(err) or "Authorization_RequestDenied" in str(err):
            return {
                "error": "Checking admin status requires elevated (admin) scopes.",
                "recommendation": (
                    "Call m365_initiate_login(request_admin_scopes=True) and re-consent "
                    "(only meaningful if this account actually has tenant admin rights)."
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
    if attachments:
        message["attachments"] = [_build_mail_attachment(path) for path in attachments]

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
            if isinstance(msg, dict) and "receivedDateTime" in msg:
                msg["receivedDateTime_local"] = _format_timestamp_local(msg.get("receivedDateTime"))
    return res


@mcp.tool()
def m365_get_email(message_id: str, account: Optional[str] = None) -> Dict[str, Any]:
    """Get full details of a specific Outlook email message, including attachment summary if attachments are present."""
    msg = _graph_request("GET", f"/me/messages/{message_id}", account=account)
    if isinstance(msg, dict):
        if "receivedDateTime" in msg:
            msg["receivedDateTime_local"] = _format_timestamp_local(msg.get("receivedDateTime"))
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
        out_dir = Path(os.getenv("TERMINAL_CWD") or os.getcwd()) / "attachments"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / name
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

    if start_time_iso and end_time_iso:
        params["startDateTime"] = start_time_iso
        params["endDateTime"] = end_time_iso
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
                if "end" in evt:
                    evt["end_local"] = _format_timestamp_local(evt.get("end"))

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
    payload: Dict[str, Any] = {
        "subject": subject,
        "start": {"dateTime": start_time_iso, "timeZone": tz_name},
        "end": {"dateTime": end_time_iso, "timeZone": tz_name},
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


@mcp.tool()
def m365_list_chats(top: int = 10) -> Dict[str, Any]:
    """List recent Microsoft Teams chats."""
    params = {"$top": min(top, 50)}
    return _graph_request("GET", "/me/chats", params=params)


@mcp.tool()
def m365_send_chat_message(
    chat_id: str,
    content: Optional[str] = None,
    content_type: str = "html",
    message: Optional[str] = None,
    body: Optional[str] = None,
    text: Optional[str] = None,
    attachments: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Send a message to a Microsoft Teams chat.

    Args:
        chat_id: The Teams chat ID.
        content: Message content (text or HTML).
        content_type: 'html' (default) or 'text'. When 'html', Teams renders rich text, line breaks, and paragraphs.
        message: Alias for content (accepted so a wrong first guess still succeeds).
        body: Alias for content (accepted so a wrong first guess still succeeds).
        text: Alias for content (accepted so a wrong first guess still succeeds).
        attachments: Optional local file paths to attach. Each file is uploaded to
            the signed-in user's OneDrive (folder 'HermesAttachments') and linked
            into the chat message as a file card, same as Teams' own "Attach" button.
            Forces content_type to HTML regardless of what was requested.
    """
    resolved_content = content if content is not None else (message if message is not None else (body if body is not None else text))
    if resolved_content is None:
        raise ValueError("m365_send_chat_message requires the message text via 'content' (or its aliases: message/body/text).")

    ct = "html" if attachments else content_type.lower()
    final_content = resolved_content
    if ct == "html":
        import re
        if not re.search(r"<(p|div|br|ul|ol|li|h[1-6])\b", resolved_content, re.IGNORECASE):
            paragraphs = resolved_content.split("\n\n")
            formatted_p = []
            for p in paragraphs:
                p_clean = p.strip().replace("\n", "<br/>")
                if p_clean:
                    formatted_p.append(f"<p>{p_clean}</p>")
            final_content = "".join(formatted_p) if formatted_p else resolved_content

    payload: Dict[str, Any] = {
        "body": {
            "contentType": "html" if ct == "html" else "text",
            "content": final_content,
        }
    }
    if attachments:
        attachment_payload, attachment_tags = _build_teams_attachments(attachments)
        payload["attachments"] = attachment_payload
        payload["body"]["content"] = final_content + attachment_tags
    return _graph_request("POST", f"/me/chats/{chat_id}/messages", json_data=payload)


@mcp.tool()
def m365_list_drive_files(folder_id: Optional[str] = None, top: int = 20) -> Dict[str, Any]:
    """List files and folders in OneDrive."""
    endpoint = f"/me/drive/items/{folder_id}/children" if folder_id else "/me/drive/root/children"
    params = {"$top": min(top, 50)}
    return _graph_request("GET", endpoint, params=params)


@mcp.tool()
def m365_search_drive_files(query: str) -> Dict[str, Any]:
    """Search files in OneDrive by keyword."""
    return _graph_request("GET", f"/me/drive/root/search(q='{query}')")


@mcp.tool()
def m365_get_drive_file(file_id: str) -> Dict[str, Any]:
    """Get file metadata and download URL from OneDrive."""
    return _graph_request("GET", f"/me/drive/items/{file_id}")


@mcp.tool()
def m365_download_drive_file(
    file_id: str,
    save_path: Optional[str] = None,
    account: Optional[str] = None,
) -> Dict[str, Any]:
    """Download a file from OneDrive or SharePoint to the local file system."""
    meta = _graph_request("GET", f"/me/drive/items/{file_id}", account=account)
    name = meta.get("name") or f"drive_file_{file_id}"
    content_bytes = _graph_download_bytes(f"/me/drive/items/{file_id}/content", account=account)

    if not save_path:
        out_dir = Path(os.getenv("TERMINAL_CWD") or os.getcwd()) / "downloads"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / name
    else:
        out_file = Path(save_path).expanduser().resolve()
        out_file.parent.mkdir(parents=True, exist_ok=True)

    out_file.write_bytes(content_bytes)
    return {
        "success": True,
        "file_id": file_id,
        "name": name,
        "size_bytes": len(content_bytes),
        "saved_path": str(out_file),
    }




@mcp.tool()
def m365_search_users(query: str, top: int = 10) -> Dict[str, Any]:
    """Search tenant user directory by display name, email, or userPrincipalName.

    Requires admin-consented scopes (default sign-in doesn't request them) --
    if this fails, call m365_initiate_login(request_admin_scopes=True) first
    (only meaningful if the signed-in account has tenant admin rights).
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
def m365_get_chat_members(chat_id: str) -> Dict[str, Any]:
    """Get the members (participants) of a Microsoft Teams chat."""
    return _graph_request("GET", f"/me/chats/{chat_id}/members")


@mcp.tool()
def m365_get_or_create_direct_chat(user_id_or_upn: str) -> Dict[str, Any]:
    """Get or create a 1:1 Teams direct chat with a tenant user by ID or email/UPN.

    Looking up another user by email/UPN requires admin-consented scopes --
    if this fails, call m365_initiate_login(request_admin_scopes=True) first,
    or pass the other user's Graph user ID directly instead of their email.
    """
    me_profile = _graph_request("GET", "/me")
    my_id = me_profile.get("id")

    other_user = user_id_or_upn
    if "@" in user_id_or_upn:
        search_res = m365_search_users(user_id_or_upn, top=1)
        users = search_res.get("value", [])
        if users:
            other_user = users[0].get("id")
        else:
            # Fallback: get user directly by UPN
            user_by_upn = _graph_request("GET", f"/users/{user_id_or_upn}")
            if "id" in user_by_upn:
                other_user = user_by_upn["id"]

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
    return _graph_request("POST", "/chats", json_data=payload)


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
    return _graph_request("GET", endpoint, params={"$top": min(top, 50)})


@mcp.tool()
def m365_search_sharepoint_files(site_id: str, query: str) -> Dict[str, Any]:
    """Search for files within a SharePoint site."""
    return _graph_request("GET", f"/sites/{site_id}/drive/root/search(q='{query}')")


# ─── Teams Channels & Activity Feed Tools ───────────────────────────────────


@mcp.tool()
def m365_list_chat_messages(chat_id: str, top: int = 10) -> Dict[str, Any]:
    """List recent messages in a specific Microsoft Teams chat (1:1 or group chat)."""
    params = {"$top": min(top, 50)}
    res = _graph_request("GET", f"/me/chats/{chat_id}/messages", params=params)
    if isinstance(res, dict) and "value" in res and isinstance(res["value"], list):
        for msg in res["value"]:
            if isinstance(msg, dict) and "createdDateTime" in msg:
                msg["createdDateTime_local"] = _format_timestamp_local(msg.get("createdDateTime"))
    return res


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
        for msg in res["value"]:
            if isinstance(msg, dict) and "createdDateTime" in msg:
                msg["createdDateTime_local"] = _format_timestamp_local(msg.get("createdDateTime"))
    return res


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

if __name__ == "__main__":
    if "--login" in sys.argv:
        token = _get_access_token()
        print(f"Token acquired successfully ({len(token)} chars). You can now use MSOffice365MCP in Hermes.")
    else:
        mcp.run()
