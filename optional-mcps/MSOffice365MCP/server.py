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
    "User.Read.All",
    "Mail.ReadWrite",
    "Mail.Send",
    "Calendars.ReadWrite",
    "Chat.ReadWrite",
    "Files.ReadWrite.All",
    "Directory.Read.All",
    "Contacts.ReadWrite",
    "Sites.ReadWrite.All",
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
    extra_headers: Optional[Dict[str, str]] = None,
) -> Any:
    token = _get_access_token()
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
def m365_initiate_login() -> Dict[str, Any]:
    """Initiate interactive Microsoft 365 OAuth sign-in flow directly via Device Code Flow or Browser Link from Hermes."""
    app = _get_msal_app()
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" in flow:
        return {
            "status": "pending",
            "device_code": flow["user_code"],
            "verification_url": flow["verification_uri"],
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
    return {"error": "Sign-in incomplete or failed", "details": result}


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
def m365_list_calendars(top: int = 20) -> Dict[str, Any]:
    """List all available Outlook calendars (personal, shared, office hours, vacation/URLAUB calendars)."""
    params = {"$top": min(top, 50), "$select": "id,name,color,canEdit,isDefaultCalendar,owner"}
    return _graph_request("GET", "/me/calendars", params=params)


@mcp.tool()
def m365_get_events(
    calendar: Optional[str] = None,
    start_time_iso: Optional[str] = None,
    end_time_iso: Optional[str] = None,
    top: int = 20,
) -> Dict[str, Any]:
    """Get events from any Outlook calendar (default, shared by name 'URLAUB'/'Officezeiten', calendar ID, or user email).

    Args:
        calendar: Optional calendar name (e.g. 'URLAUB', 'Officezeiten'), calendar ID, or user email address. Omit for default calendar.
        start_time_iso: Optional start date/time (ISO format) for date range filtering.
        end_time_iso: Optional end date/time (ISO format) for date range filtering.
        top: Max number of events to return.
    """
    target = (calendar or "").strip()
    matched_cal_id: Optional[str] = None
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
                    if c_id == target or target_lower in c_name.lower() or c_name.lower() in target_lower:
                        matched_cal_id = c_id
                        matched_cal_name = c_name
                        break
            if not matched_cal_id:
                matched_cal_id = target

    if target_user_email:
        base_path = f"/users/{target_user_email}/calendar"
    elif matched_cal_id:
        base_path = f"/me/calendars/{matched_cal_id}"
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

    res = _graph_request("GET", endpoint, params=params)
    if matched_cal_name:
        res["resolved_calendar_name"] = matched_cal_name
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
    elif target_cal_id:
        endpoint = f"/me/calendars/{target_cal_id}/events"
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
    content: str,
    content_type: str = "html",
) -> Dict[str, Any]:
    """Send a message to a Microsoft Teams chat.

    Args:
        chat_id: The Teams chat ID.
        content: Message content (text or HTML).
        content_type: 'html' (default) or 'text'. When 'html', Teams renders rich text, line breaks, and paragraphs.
    """
    ct = content_type.lower()
    final_content = content
    if ct == "html":
        import re
        if not re.search(r"<(p|div|br|ul|ol|li|h[1-6])\b", content, re.IGNORECASE):
            paragraphs = content.split("\n\n")
            formatted_p = []
            for p in paragraphs:
                p_clean = p.strip().replace("\n", "<br/>")
                if p_clean:
                    formatted_p.append(f"<p>{p_clean}</p>")
            final_content = "".join(formatted_p) if formatted_p else content

    payload = {
        "body": {
            "contentType": "html" if ct == "html" else "text",
            "content": final_content,
        }
    }
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
def m365_search_users(query: str, top: int = 10) -> Dict[str, Any]:
    """Search tenant user directory by display name, email, or userPrincipalName."""
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
    """Get or create a 1:1 Teams direct chat with a tenant user by ID or email/UPN."""
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
    """List or search SharePoint sites in the tenant."""
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
    return _graph_request("GET", f"/me/chats/{chat_id}/messages", params=params)


@mcp.tool()
def m365_list_joined_teams(top: int = 20) -> Dict[str, Any]:
    """List all Microsoft Teams that the current user is a member of."""
    params = {"$top": min(top, 50)}
    return _graph_request("GET", "/me/joinedTeams", params=params)


@mcp.tool()
def m365_list_team_channels(team_id: str) -> Dict[str, Any]:
    """List all channels in a specific Microsoft Team."""
    return _graph_request("GET", f"/teams/{team_id}/channels")


@mcp.tool()
def m365_list_channel_messages(team_id: str, channel_id: str, top: int = 10) -> Dict[str, Any]:
    """List recent messages in a specific Microsoft Teams channel."""
    params = {"$top": min(top, 50)}
    return _graph_request("GET", f"/teams/{team_id}/channels/{channel_id}/messages", params=params)


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
                    "last_updated": c.get("lastUpdatedDateTime"),
                    "recent_messages": [
                        {
                            "id": m.get("id"),
                            "from": m.get("from", {}).get("user", {}).get("displayName"),
                            "created_at": m.get("createdDateTime"),
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
        teams_res = _graph_request("GET", "/me/joinedTeams", params={"$top": 10})
        teams = teams_res.get("value", [])
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
                                    "created_at": m.get("createdDateTime"),
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
