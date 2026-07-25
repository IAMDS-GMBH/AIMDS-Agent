"""Microsoft 365 MCP Server (Outlook Mail & Calendar, Teams, OneDrive).

Provides access to Microsoft 365 services via MS Graph API with MSAL OAuth authentication,
auto-discovery, and admin role detection.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import msal
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP Server
mcp = FastMCP("MSOffice365MCP")

# Constants
GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
SCOPES = [
    "User.Read",
    "Mail.ReadWrite",
    "Mail.Send",
    "Calendars.ReadWrite",
    "Chat.ReadWrite",
    "Files.ReadWrite.All",
    "Directory.Read.All",
]

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


def _get_token_cache_path() -> Path:
    hermes_home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    cache_dir = Path(hermes_home)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "m365_token_cache.bin"


def _get_msal_app() -> msal.PublicClientApplication:
    client_id = (
        os.environ.get("M365_CLIENT_ID")
        or os.environ.get("OUTLOOK_CLIENT_ID")
        or os.environ.get("TEAMS_CLIENT_ID")
        or "1950a258-227b-4e31-a9cf-717495945fc2"  # Azure PowerShell multi-tenant client ID
    )
    tenant_id = (
        os.environ.get("M365_TENANT_ID")
        or os.environ.get("OUTLOOK_TENANT_ID")
        or os.environ.get("TEAMS_TENANT_ID")
        or "organizations"
    )
    if tenant_id == "common":
        tenant_id = "organizations"
    authority = f"https://login.microsoftonline.com/{tenant_id}"

    cache = msal.SerializableTokenCache()
    cache_path = _get_token_cache_path()
    if cache_path.exists():
        cache.deserialize(cache_path.read_text(encoding="utf-8"))

    app = msal.PublicClientApplication(
        client_id=client_id,
        authority=authority,
        token_cache=cache,
    )
    return app


def _save_cache(app: msal.PublicClientApplication) -> None:
    cache = app.token_cache
    if cache.has_state_changed:
        cache_path = _get_token_cache_path()
        cache_path.write_text(cache.serialize(), encoding="utf-8")


def _get_access_token() -> str:
    # 1. Check direct env token
    direct_token = os.environ.get("M365_ACCESS_TOKEN")
    if direct_token:
        return direct_token

    app = _get_msal_app()
    accounts = app.get_accounts()

    if accounts:
        for acc in accounts:
            result = app.acquire_token_silent(SCOPES, account=acc)
            if result and "access_token" in result:
                _save_cache(app)
                return result["access_token"]

    # 2. Check if running in interactive --login CLI mode
    if "--login" in sys.argv:
        print("\n[M365 OAuth] Initiating interactive sign-in...", file=sys.stderr)
        try:
            result = app.acquire_token_interactive(scopes=SCOPES, port=8400)
            if "access_token" in result:
                _save_cache(app)
                print("[M365 OAuth] Sign-in successful! Token cached in ~/.hermes/m365_token_cache.bin", file=sys.stderr)
                return result["access_token"]
        except Exception as err:
            print(f"[M365 OAuth] Interactive loopback failed ({err}), trying device code flow...", file=sys.stderr)

        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" in flow:
            print(f"\n[M365 OAuth] Please open {flow['verification_uri']} and enter code: {flow['user_code']}\n", file=sys.stderr)
            result = app.acquire_token_by_device_flow(flow)
            if "access_token" in result:
                _save_cache(app)
                print("[M365 OAuth] Device code sign-in successful! Token cached.", file=sys.stderr)
                return result["access_token"]

    # 3. Running inside stdio MCP without cached token -> Return clear actionable error rather than blocking stdio!
    raise RuntimeError(
        "M365 authentication required. Please run: "
        "'/Users/johanneshuchler/_GitHubRepos/hermes-agent/.venv/bin/python optional-mcps/MSOffice365MCP/server.py --login' "
        "in your terminal once to complete M365 sign-in."
    )


def _graph_request(
    method: str,
    endpoint: str,
    json_data: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Any:
    token = _get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    url = f"{GRAPH_API_BASE}{endpoint}" if not endpoint.startswith("http") else endpoint

    with httpx.Client(timeout=30.0) as client:
        response = client.request(method, url, headers=headers, json=json_data, params=params)
        if response.status_code == 204:
            return {"success": True}
        if response.is_error:
            raise RuntimeError(f"MS Graph API Error [{response.status_code}]: {response.text}")
        return response.json()


# ─── Tools ───────────────────────────────────────────────────────────────────


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
def m365_get_user_profile() -> Dict[str, Any]:
    """Get the current authenticated user profile from Microsoft 365."""
    return _graph_request("GET", "/me")


@mcp.tool()
def m365_check_admin_status() -> Dict[str, Any]:
    """Check if the authenticated M365 user has Tenant/Directory Admin permissions."""
    member_of = _graph_request("GET", "/me/memberOf")
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
    is_html: bool = False,
    save_to_sent_items: bool = True,
) -> Dict[str, Any]:
    """Send an email using Outlook Mail. Ensures saveToSentItems is respected."""
    recipients = [{"emailAddress": {"address": addr.strip()}} for addr in to]
    content_type = "HTML" if is_html else "Text"

    payload = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": content_type,
                "content": body,
            },
            "toRecipients": recipients,
        },
        "saveToSentItems": save_to_sent_items,
    }
    return _graph_request("POST", "/me/sendMail", json_data=payload)


@mcp.tool()
def m365_list_emails(top: int = 10, search: Optional[str] = None) -> Dict[str, Any]:
    """List recent emails from Outlook inbox."""
    params = {"$top": min(top, 50), "$select": "id,subject,from,receivedDateTime,isRead,bodyPreview"}
    if search:
        params["$search"] = f'"{search}"'
    return _graph_request("GET", "/me/messages", params=params)


@mcp.tool()
def m365_get_email(message_id: str) -> Dict[str, Any]:
    """Get full details of a specific Outlook email message."""
    return _graph_request("GET", f"/me/messages/{message_id}")


@mcp.tool()
def m365_list_events(top: int = 10) -> Dict[str, Any]:
    """List upcoming calendar events in Outlook Calendar."""
    params = {"$top": min(top, 50), "$select": "id,subject,start,end,location,organizer,attendees"}
    return _graph_request("GET", "/me/calendar/events", params=params)


@mcp.tool()
def m365_create_event(
    subject: str,
    start_time_iso: str,
    end_time_iso: str,
    time_zone: Optional[str] = None,
    attendees: Optional[List[str]] = None,
    body: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new event or meeting in Outlook Calendar."""
    tz_name = time_zone or _get_timezone_name()
    payload: Dict[str, Any] = {
        "subject": subject,
        "start": {"dateTime": start_time_iso, "timeZone": tz_name},
        "end": {"dateTime": end_time_iso, "timeZone": tz_name},
    }
    if body:
        payload["body"] = {"contentType": "Text", "content": body}
    if attendees:
        payload["attendees"] = [
            {"emailAddress": {"address": a.strip()}, "type": "required"} for a in attendees
        ]

    return _graph_request("POST", "/me/calendar/events", json_data=payload)


@mcp.tool()
def m365_list_chats(top: int = 10) -> Dict[str, Any]:
    """List recent Microsoft Teams chats."""
    params = {"$top": min(top, 50)}
    return _graph_request("GET", "/me/chats", params=params)


@mcp.tool()
def m365_send_chat_message(chat_id: str, content: str) -> Dict[str, Any]:
    """Send a message to a Microsoft Teams chat."""
    payload = {"body": {"content": content}}
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


if __name__ == "__main__":
    if "--login" in sys.argv:
        token = _get_access_token()
        print(f"Token acquired successfully ({len(token)} chars). You can now use MSOffice365MCP in Hermes.")
    else:
        mcp.run()
