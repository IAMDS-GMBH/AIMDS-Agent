"""Outlook / Microsoft Graph inbox read tool.

Lets the agent fetch and summarize emails from the user's Outlook inbox
on demand, using the same delegated token cache as the Outlook gateway
adapter (``~/.hermes/outlook_token.json``).

Credentials are resolved (in order):
  1. ``platforms.outlook.extra`` in ``config.yaml``
  2. ``OUTLOOK_TENANT_ID`` / ``OUTLOOK_CLIENT_ID`` / ``OUTLOOK_CLIENT_SECRET``
     environment variables

Authentication flow:
  - If a valid token/refresh exists in the cache, emails are fetched silently.
  - If no token cache exists, the tool returns a device-code auth prompt
    (URL + code) immediately — the agent surfaces these to the user in chat.
    The user opens the URL, enters the code, and then calls the tool again.
    On the second call the token is cached and emails are returned.

The tool is gated on ``OUTLOOK_TENANT_ID`` and ``OUTLOOK_CLIENT_ID`` being
available — the same minimum requirement as the delegated gateway adapter.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from tools.registry import registry

logger = logging.getLogger(__name__)

# All Outlook tools now request the same combined scope
# (``tools.microsoft_graph_auth.DEFAULT_DELEGATED_SCOPE``) so a single
# sign-in/refresh-token covers mail, shared mailbox, and calendar. These
# aliases are kept only so any external/legacy references keep working —
# every call site below passes ``scope=None`` (i.e. the combined default).
OUTLOOK_CALENDAR_READ_SCOPE = None
OUTLOOK_CALENDAR_WRITE_SCOPE = None
OUTLOOK_SHARED_MAIL_SCOPE = None
OUTLOOK_CONTACTS_SCOPE = None

# ---------------------------------------------------------------------------
# Credential helpers (mirrors adapter.py logic, no coupling)
# ---------------------------------------------------------------------------

def _outlook_creds_from_config() -> dict[str, str]:
    """Return Outlook credentials from config.yaml platforms.outlook.extra."""
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        extra = (
            cfg.get("platforms", {})
               .get("outlook", {})
               .get("extra", {})
        ) or {}
        return {
            "tenant_id": str(extra.get("tenant_id") or "").strip(),
            "client_id": str(extra.get("client_id") or "").strip(),
            "client_secret": str(extra.get("client_secret") or "").strip(),
        }
    except Exception:
        return {"tenant_id": "", "client_id": "", "client_secret": ""}


def _get_outlook_creds() -> dict[str, str]:
    cfg = _outlook_creds_from_config()

    def _env(key: str) -> str:
        # Try process env first, then ~/.hermes/.env file
        val = os.getenv(key, "")
        if not val:
            try:
                from hermes_cli.config import get_env_value
                val = get_env_value(key) or ""
            except Exception:
                pass
        return val.strip()

    return {
        "tenant_id": cfg["tenant_id"] or _env("OUTLOOK_TENANT_ID"),
        "client_id": cfg["client_id"] or _env("OUTLOOK_CLIENT_ID"),
        "client_secret": cfg["client_secret"] or _env("OUTLOOK_CLIENT_SECRET"),
    }


def _check_outlook_tool_requirements() -> bool:
    creds = _get_outlook_creds()
    return bool(creds["tenant_id"] and creds["client_id"])


_INTERACTIVE_AUTH_FLOWS = {"auto", "loopback", "device_code"}


def outlook_interactive_auth_flow() -> str:
    """Resolve the configured interactive auth flow: auto | loopback | device_code.

    Resolution order mirrors ``_get_outlook_creds``: ``config.yaml``
    ``platforms.outlook.extra.interactive_auth_flow`` first, then the
    ``OUTLOOK_INTERACTIVE_AUTH_FLOW`` env var / ``.env`` value, defaulting to
    ``"auto"`` (try the loopback browser sign-in first, fall back to device
    code if a local listener can't be bound).
    """
    raw = ""
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        extra = cfg.get("platforms", {}).get("outlook", {}).get("extra", {}) or {}
        raw = str(extra.get("interactive_auth_flow") or "").strip()
    except Exception:
        raw = ""

    if not raw:
        raw = os.getenv("OUTLOOK_INTERACTIVE_AUTH_FLOW", "")
        if not raw:
            try:
                from hermes_cli.config import get_env_value
                raw = get_env_value("OUTLOOK_INTERACTIVE_AUTH_FLOW") or ""
            except Exception:
                raw = ""

    raw = raw.strip().lower() or "auto"
    return raw if raw in _INTERACTIVE_AUTH_FLOWS else "auto"


def _enable_outlook_toolset_for_cli() -> tuple[bool, str | None]:
    """Ensure ``outlook`` toolset is enabled for CLI-surface sessions."""
    try:
        from hermes_cli.config import load_config, save_config

        config = load_config()
        platform_toolsets = config.get("platform_toolsets")
        if not isinstance(platform_toolsets, dict):
            platform_toolsets = {}
            config["platform_toolsets"] = platform_toolsets

        cli_toolsets = platform_toolsets.get("cli")
        if not isinstance(cli_toolsets, list):
            cli_toolsets = []
            platform_toolsets["cli"] = cli_toolsets

        if "outlook" in cli_toolsets:
            return False, None

        cli_toolsets.append("outlook")
        save_config(config)
        return True, None
    except Exception as exc:
        return False, str(exc)


def _auto_enable_outlook_toolset_if_token_ready() -> tuple[bool, str | None]:
    """Enable the outlook toolset once delegated auth is already usable.

    Returns (changed, error) and no-ops when the token cache is not ready yet.
    """
    if not _has_valid_token_cache():
        return False, None
    return _enable_outlook_toolset_for_cli()


def _has_valid_token_cache() -> bool:
    """Return True if a usable token/refresh already exists on disk."""
    try:
        from hermes_constants import get_hermes_home
        cache_path = get_hermes_home() / "outlook_token.json"
        if not cache_path.exists():
            return False
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        # A refresh token is enough — it will silently renew the access token
        if data.get("refresh_token"):
            return True
        # Or an unexpired access token
        expires_at = float(data.get("expires_at", 0))
        return expires_at > time.time() + 120
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Async: initiate device code (no polling — returns prompt immediately)
# ---------------------------------------------------------------------------

async def _start_device_code_async(scope: str | None = None) -> dict[str, Any]:
    """Request a device code from Microsoft and return the auth prompt info."""
    import httpx
    from tools.microsoft_graph_auth import (
        GraphDelegatedCredentials,
        DEFAULT_DELEGATED_SCOPE,
    )
    delegated_scope = (scope or DEFAULT_DELEGATED_SCOPE).strip() or DEFAULT_DELEGATED_SCOPE
    creds_raw = _get_outlook_creds()
    creds = GraphDelegatedCredentials(
        tenant_id=creds_raw["tenant_id"],
        client_id=creds_raw["client_id"],
        client_secret=creds_raw["client_secret"] or None,
        scope=delegated_scope,
    )
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.post(
            creds.device_code_url,
            data={"client_id": creds.client_id, "scope": delegated_scope},
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Device code request failed: {resp.text}")
        payload = resp.json()

    expires_in = int(payload.get("expires_in", 900))
    return {
        "device_code": payload["device_code"],
        "user_code": payload["user_code"],
        "verification_uri": payload["verification_uri"],
        "expires_in_seconds": expires_in,
        "poll_interval": int(payload.get("interval", 5)),
    }


# ---------------------------------------------------------------------------
# Async: poll for token after user has authenticated
# ---------------------------------------------------------------------------

async def _poll_device_code_async(device_code: str, scope: str | None = None) -> bool:
    """Poll once for a token. Saves cache on success. Returns True if authed."""
    import httpx
    from tools.microsoft_graph_auth import (
        GraphDelegatedCredentials,
        DEFAULT_DELEGATED_SCOPE,
    )
    from hermes_constants import get_hermes_home

    delegated_scope = (scope or DEFAULT_DELEGATED_SCOPE).strip() or DEFAULT_DELEGATED_SCOPE
    creds_raw = _get_outlook_creds()
    creds = GraphDelegatedCredentials(
        tenant_id=creds_raw["tenant_id"],
        client_id=creds_raw["client_id"],
        client_secret=creds_raw["client_secret"] or None,
        scope=delegated_scope,
    )
    token_data: dict[str, str] = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "client_id": creds.client_id,
        "device_code": device_code,
    }
    if creds.client_secret:
        token_data["client_secret"] = creds.client_secret

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.post(creds.token_url, data=token_data)
        result = resp.json()

    error = result.get("error")
    if error in ("authorization_pending", "slow_down"):
        return False
    if error:
        raise RuntimeError(f"Token poll failed: {result.get('error_description', error)}")

    access_token = result.get("access_token", "").strip()
    refresh_token = result.get("refresh_token", "").strip()
    if not access_token or not refresh_token:
        raise RuntimeError("Missing access_token or refresh_token in response.")

    expires_in = int(result.get("expires_in", 3600))
    cache_path = get_hermes_home() / "outlook_token.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": time.time() + max(0, expires_in),
            "token_type": result.get("token_type", "Bearer"),
        }, indent=2),
        encoding="utf-8",
    )
    try:
        cache_path.chmod(0o600)
    except Exception:
        pass
    return True


_FOLDER_MAP = {
    "inbox": "inbox",
    "sent": "sentitems",
    "drafts": "drafts",
    "deleted": "deleteditems",
    "archive": "archive",
}


def _time_range_bounds(time_range: str) -> tuple[str, str] | None:
    """Return (start_iso, end_iso) UTC bounds for a named time_range, or None for 'all'."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    normalized = (time_range or "today").strip().lower()
    if normalized == "today":
        start = today_start
        end = now
    elif normalized == "yesterday":
        start = today_start - timedelta(days=1)
        end = today_start
    elif normalized == "this_week":
        start = today_start - timedelta(days=today_start.weekday())  # Monday
        end = now
    else:
        return None

    def _fmt(dt: datetime) -> str:
        return dt.isoformat().replace("+00:00", "Z")

    return _fmt(start), _fmt(end)


def _new_graph_client(scope: str | None = None) -> tuple[Any, dict[str, str]]:
    """Build a (client, creds) pair for a delegated Graph call."""
    from tools.microsoft_graph_auth import (
        GraphDelegatedCredentials,
        GraphDeviceCodeProvider,
    )
    from tools.microsoft_graph_client import MicrosoftGraphClient

    creds_raw = _get_outlook_creds()
    kwargs: dict[str, Any] = {
        "tenant_id": creds_raw["tenant_id"],
        "client_id": creds_raw["client_id"],
        "client_secret": creds_raw["client_secret"] or None,
    }
    if scope:
        kwargs["scope"] = scope
    creds = GraphDelegatedCredentials(**kwargs)
    provider = GraphDeviceCodeProvider(creds)
    client = MicrosoftGraphClient(provider, user_agent="Hermes-Outlook/1.0")
    return client, creds_raw


# ---------------------------------------------------------------------------
# Core async fetch logic
# ---------------------------------------------------------------------------

async def _fetch_emails_async(
    count: int,
    folder: str,
    unread_only: bool,
    include_body: bool,
    time_range: str = "today",
) -> list[dict[str, Any]]:
    client, _ = _new_graph_client()

    select_fields = ["id", "subject", "receivedDateTime", "isRead",
                     "from", "toRecipients", "hasAttachments", "importance"]
    if include_body:
        select_fields.append("bodyPreview")

    filters: list[str] = []
    if unread_only:
        filters.append("isRead eq false")
    bounds = _time_range_bounds(time_range)
    if bounds is not None:
        start_iso, end_iso = bounds
        filters.append(f"receivedDateTime ge {start_iso}")
        filters.append(f"receivedDateTime le {end_iso}")

    params: dict[str, Any] = {
        "$top": min(max(1, count), 50),
        "$orderby": "receivedDateTime desc",
        "$select": ",".join(select_fields),
    }
    if filters:
        params["$filter"] = " and ".join(filters)

    graph_folder = _FOLDER_MAP.get(folder.lower(), "inbox")
    path = f"/me/mailFolders/{graph_folder}/messages"

    resp = await client.get_json(path, params=params)
    return resp.get("value", [])


async def _fetch_email_by_id_async(message_id: str) -> dict[str, Any]:
    """Fetch a single email with its full body (not just a preview)."""
    client, _ = _new_graph_client()
    select_fields = [
        "id", "subject", "receivedDateTime", "isRead", "from", "toRecipients",
        "ccRecipients", "hasAttachments", "importance", "body", "conversationId",
        "webLink",
    ]
    params = {"$select": ",".join(select_fields)}
    resp = await client.get_json(f"/me/messages/{message_id}", params=params)
    return resp


async def _search_emails_async(
    query: str,
    count: int,
    search_in: str,
    folder: str,
) -> list[dict[str, Any]]:
    """Search emails by keyword in subject and/or body via Graph $search."""
    client, _ = _new_graph_client()

    select_fields = ["id", "subject", "receivedDateTime", "isRead",
                     "from", "toRecipients", "hasAttachments", "importance", "bodyPreview"]

    graph_folder = _FOLDER_MAP.get(folder.lower(), "inbox")
    path = f"/me/mailFolders/{graph_folder}/messages"

    safe_query = query.replace('"', '\\"')
    normalized_scope = (search_in or "both").strip().lower()
    if normalized_scope == "subject":
        search_expr = f'subject:"{safe_query}"'
    elif normalized_scope == "body":
        search_expr = f'body:"{safe_query}"'
    else:
        search_expr = f'"{safe_query}"'  # searches subject + body + sender by default

    params: dict[str, Any] = {
        "$search": search_expr,
        "$top": min(max(1, count), 50),
        "$select": ",".join(select_fields),
    }
    headers = {"ConsistencyLevel": "eventual"}

    resp = await client.get_json(path, params=params, headers=headers)
    return resp.get("value", [])


async def _fetch_shared_mail_async(
    mailbox: str,
    count: int,
    folder: str,
    unread_only: bool,
    include_body: bool,
) -> list[dict[str, Any]]:
    """Read messages from a shared mailbox the delegated user has access to."""
    client, _ = _new_graph_client(scope=OUTLOOK_SHARED_MAIL_SCOPE)

    select_fields = ["id", "subject", "receivedDateTime", "isRead",
                     "from", "toRecipients", "hasAttachments", "importance"]
    if include_body:
        select_fields.append("bodyPreview")

    params: dict[str, Any] = {
        "$top": min(max(1, count), 50),
        "$orderby": "receivedDateTime desc",
        "$select": ",".join(select_fields),
    }
    if unread_only:
        params["$filter"] = "isRead eq false"

    graph_folder = _FOLDER_MAP.get(folder.lower(), "inbox")
    path = f"/users/{mailbox}/mailFolders/{graph_folder}/messages"

    resp = await client.get_json(path, params=params)
    return resp.get("value", [])


async def _verify_sent_email_async(subject: str) -> dict[str, Any]:
    """Best-effort check that a just-sent message actually landed in Sent
    Items. Graph's ``/me/sendMail`` is fire-and-forget (202, empty body) —
    it gives no confirmation the message was actually delivered/saved, so a
    stale or insufficiently-scoped cached access token could be silently
    accepted by Graph's request validation yet fail to actually deliver.
    Polls briefly since the Sent Items copy can lag the API response by a
    second or two.
    """
    client, _ = _new_graph_client()
    safe_subject = subject.replace("'", "''")
    params = {
        "$filter": f"subject eq '{safe_subject}'",
        "$orderby": "sentDateTime desc",
        "$top": 1,
        "$select": "id,sentDateTime,webLink",
    }
    # 5 attempts / 1.5s apart (~7.5s max wait) rather than 3 (~3s): the
    # Sent Items copy has been observed to lag past the shorter window under
    # load, producing a false "could not verify" result even though the mail
    # was actually delivered — the tool would then push a manual "please
    # check your Sent folder" ask onto the user instead of just confirming
    # it itself a little later.
    for attempt in range(5):
        if attempt:
            await asyncio.sleep(1.5)
        try:
            resp = await client.get_json("/me/mailFolders/sentitems/messages", params=params)
        except Exception as exc:
            logger.warning("[Outlook] Could not verify Sent Items copy: %s", exc)
            return {"verified": False, "verification_error": str(exc)}
        items = resp.get("value") or []
        if items:
            return {"verified": True, "sent_item_id": items[0].get("id", ""), "web_link": items[0].get("webLink", "")}
    return {"verified": False}


async def _send_new_email_async(
    to: list[str],
    subject: str,
    body: str,
    cc: list[str],
    bcc: list[str],
    reply_to_message_id: str,
) -> dict[str, Any]:
    """Send a brand-new email, or reply to an existing message thread."""
    client, _ = _new_graph_client()

    if reply_to_message_id:
        payload: dict[str, Any] = {"comment": body}
        if to or cc or bcc:
            # Graph's reply endpoint keeps the original recipients unless we
            # override the message body — only send extra recipients if asked.
            message: dict[str, Any] = {}
            if cc:
                message["ccRecipients"] = [
                    {"emailAddress": {"address": addr}} for addr in cc
                ]
            if bcc:
                message["bccRecipients"] = [
                    {"emailAddress": {"address": addr}} for addr in bcc
                ]
            if message:
                payload["message"] = message
        await client.post_json(
            f"/me/messages/{reply_to_message_id}/reply", json_body=payload
        )
        # Replies don't have a distinct new subject to verify against —
        # Graph accepting the request without a 4xx is the best signal we have.
        return {"verified": None}

    message = {
        "subject": subject,
        "body": {"contentType": "Text", "content": body},
        "toRecipients": [{"emailAddress": {"address": addr}} for addr in to],
    }
    if cc:
        message["ccRecipients"] = [{"emailAddress": {"address": addr}} for addr in cc]
    if bcc:
        message["bccRecipients"] = [{"emailAddress": {"address": addr}} for addr in bcc]

    await client.post_json(
        "/me/sendMail", json_body={"message": message, "saveToSentItems": True}
    )
    return await _verify_sent_email_async(subject)


def _format_email(msg: dict[str, Any], include_body: bool) -> dict[str, Any]:
    sender = msg.get("from", {}).get("emailAddress", {})
    return {
        "id": msg.get("id", ""),
        "subject": msg.get("subject") or "(no subject)",
        "from": f"{sender.get('name', '')} <{sender.get('address', '')}>".strip(),
        "received": msg.get("receivedDateTime", ""),
        "is_read": msg.get("isRead", True),
        "has_attachments": msg.get("hasAttachments", False),
        "importance": msg.get("importance", "normal"),
        **({"body_preview": msg.get("bodyPreview", "")} if include_body else {}),
    }


async def _fetch_calendar_entries_async(
    count: int,
    days_ahead: int,
    include_body_preview: bool,
    timezone_name: str,
) -> list[dict[str, Any]]:
    from tools.microsoft_graph_auth import (
        GraphDelegatedCredentials,
        GraphDeviceCodeProvider,
    )
    from tools.microsoft_graph_client import MicrosoftGraphClient

    creds_raw = _get_outlook_creds()
    creds = GraphDelegatedCredentials(
        tenant_id=creds_raw["tenant_id"],
        client_id=creds_raw["client_id"],
        client_secret=creds_raw["client_secret"] or None,
        scope=OUTLOOK_CALENDAR_READ_SCOPE,
    )
    provider = GraphDeviceCodeProvider(creds)
    client = MicrosoftGraphClient(provider, user_agent="Hermes-Outlook/1.0")

    now = datetime.now(timezone.utc)
    start_iso = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    end_iso = (now + timedelta(days=max(1, min(days_ahead, 30)))).replace(
        microsecond=0
    ).isoformat().replace("+00:00", "Z")

    select_fields = [
        "id",
        "subject",
        "start",
        "end",
        "isAllDay",
        "location",
        "organizer",
        "attendees",
        "webLink",
    ]
    if include_body_preview:
        select_fields.append("bodyPreview")

    params: dict[str, Any] = {
        "startDateTime": start_iso,
        "endDateTime": end_iso,
        "$top": min(max(1, count), 50),
        "$orderby": "start/dateTime",
        "$select": ",".join(select_fields),
    }
    headers = {"Prefer": f'outlook.timezone="{timezone_name}"'}
    resp = await client.get_json("/me/calendarView", params=params, headers=headers)
    return resp.get("value", [])


def _format_calendar_entry(entry: dict[str, Any], include_body_preview: bool) -> dict[str, Any]:
    organizer = ((entry.get("organizer") or {}).get("emailAddress") or {})
    location = entry.get("location") or {}
    start = entry.get("start") or {}
    end = entry.get("end") or {}
    result = {
        "id": entry.get("id", ""),
        "subject": entry.get("subject") or "(no subject)",
        "start": {
            "date_time": start.get("dateTime", ""),
            "time_zone": start.get("timeZone", ""),
        },
        "end": {
            "date_time": end.get("dateTime", ""),
            "time_zone": end.get("timeZone", ""),
        },
        "is_all_day": bool(entry.get("isAllDay", False)),
        "location": location.get("displayName", ""),
        "organizer": {
            "name": organizer.get("name", ""),
            "email": organizer.get("address", ""),
        },
        "web_link": entry.get("webLink", ""),
    }
    if include_body_preview:
        result["body_preview"] = entry.get("bodyPreview", "")
    return result


# ---------------------------------------------------------------------------
# Calendar write (create / update / delete) async logic
# ---------------------------------------------------------------------------

async def _get_calendar_entry_async(event_id: str) -> dict[str, Any]:
    """Fetch the current state of a single calendar event (for previews/undo)."""
    client, _ = _new_graph_client(scope=OUTLOOK_CALENDAR_WRITE_SCOPE)
    return await client.get_json(f"/me/events/{event_id}")


def _build_calendar_event_body(
    subject: str,
    start_datetime: str,
    end_datetime: str,
    timezone_name: str,
    location: str,
    body: str,
    attendees: list[str],
    is_all_day: bool,
) -> dict[str, Any]:
    event: dict[str, Any] = {}
    if subject:
        event["subject"] = subject
    if start_datetime:
        event["start"] = {"dateTime": start_datetime, "timeZone": timezone_name}
    if end_datetime:
        event["end"] = {"dateTime": end_datetime, "timeZone": timezone_name}
    if location:
        event["location"] = {"displayName": location}
    if body:
        event["body"] = {"contentType": "Text", "content": body}
    if attendees:
        event["attendees"] = [
            {"emailAddress": {"address": addr}, "type": "required"} for addr in attendees
        ]
    if is_all_day:
        event["isAllDay"] = True
    return event


async def _create_calendar_entry_async(event_body: dict[str, Any]) -> dict[str, Any]:
    client, _ = _new_graph_client(scope=OUTLOOK_CALENDAR_WRITE_SCOPE)
    return await client.post_json("/me/events", json_body=event_body)


async def _update_calendar_entry_async(
    event_id: str, event_body: dict[str, Any]
) -> dict[str, Any]:
    client, _ = _new_graph_client(scope=OUTLOOK_CALENDAR_WRITE_SCOPE)
    return await client.patch_json(f"/me/events/{event_id}", json_body=event_body)


async def _delete_calendar_entry_async(event_id: str) -> dict[str, Any]:
    client, _ = _new_graph_client(scope=OUTLOOK_CALENDAR_WRITE_SCOPE)
    return await client.delete(f"/me/events/{event_id}")


# ---------------------------------------------------------------------------
# Contacts (read / write) async logic
# ---------------------------------------------------------------------------

_CONTACT_SELECT_FIELDS = [
    "id",
    "displayName",
    "givenName",
    "surname",
    "companyName",
    "jobTitle",
    "emailAddresses",
    "businessPhones",
    "mobilePhone",
    "personalNotes",
]


async def _fetch_contacts_async(count: int, search: str) -> list[dict[str, Any]]:
    client, _ = _new_graph_client(scope=OUTLOOK_CONTACTS_SCOPE)
    params: dict[str, Any] = {
        "$top": min(max(1, count), 100),
        "$select": ",".join(_CONTACT_SELECT_FIELDS),
        "$orderby": "displayName",
    }
    if search:
        # Graph's /me/contacts doesn't support $filter on free text — use
        # $search instead, which requires the ConsistencyLevel header.
        safe_search = search.replace('"', '\\"')
        params["$search"] = f'"{safe_search}"'
        params.pop("$orderby", None)
        headers = {"ConsistencyLevel": "eventual"}
        resp = await client.get_json("/me/contacts", params=params, headers=headers)
    else:
        resp = await client.get_json("/me/contacts", params=params)
    return resp.get("value", [])


def _format_contact(contact: dict[str, Any]) -> dict[str, Any]:
    emails = contact.get("emailAddresses") or []
    return {
        "id": contact.get("id", ""),
        "display_name": contact.get("displayName") or "",
        "given_name": contact.get("givenName") or "",
        "surname": contact.get("surname") or "",
        "company_name": contact.get("companyName") or "",
        "job_title": contact.get("jobTitle") or "",
        "emails": [e.get("address", "") for e in emails if e.get("address")],
        "business_phones": contact.get("businessPhones") or [],
        "mobile_phone": contact.get("mobilePhone") or "",
        "notes": contact.get("personalNotes") or "",
    }


async def _get_contact_async(contact_id: str) -> dict[str, Any]:
    """Fetch the current state of a single contact (for previews/undo)."""
    client, _ = _new_graph_client(scope=OUTLOOK_CONTACTS_SCOPE)
    return await client.get_json(
        f"/me/contacts/{contact_id}", params={"$select": ",".join(_CONTACT_SELECT_FIELDS)}
    )


def _build_contact_body(
    display_name: str,
    given_name: str,
    surname: str,
    company_name: str,
    job_title: str,
    emails: list[str],
    business_phone: str,
    mobile_phone: str,
    notes: str,
) -> dict[str, Any]:
    contact: dict[str, Any] = {}
    if display_name:
        contact["displayName"] = display_name
    if given_name:
        contact["givenName"] = given_name
    if surname:
        contact["surname"] = surname
    if company_name:
        contact["companyName"] = company_name
    if job_title:
        contact["jobTitle"] = job_title
    if emails:
        contact["emailAddresses"] = [{"address": addr} for addr in emails]
    if business_phone:
        contact["businessPhones"] = [business_phone]
    if mobile_phone:
        contact["mobilePhone"] = mobile_phone
    if notes:
        contact["personalNotes"] = notes
    return contact


async def _create_contact_async(contact_body: dict[str, Any]) -> dict[str, Any]:
    client, _ = _new_graph_client(scope=OUTLOOK_CONTACTS_SCOPE)
    return await client.post_json("/me/contacts", json_body=contact_body)


async def _update_contact_async(contact_id: str, contact_body: dict[str, Any]) -> dict[str, Any]:
    client, _ = _new_graph_client(scope=OUTLOOK_CONTACTS_SCOPE)
    return await client.patch_json(f"/me/contacts/{contact_id}", json_body=contact_body)


async def _delete_contact_async(contact_id: str) -> dict[str, Any]:
    client, _ = _new_graph_client(scope=OUTLOOK_CONTACTS_SCOPE)
    return await client.delete(f"/me/contacts/{contact_id}")


def _run_async(coro: Any, timeout: float = 120) -> Any:
    """Run a coroutine safely regardless of whether a loop is already running.

    Only falls back to ``asyncio.run(coro)`` when there is genuinely no usable
    event loop yet (``asyncio.get_event_loop()`` itself raising). Errors
    raised by the coroutine's own body (e.g. AADSTS token-poll failures) must
    propagate as-is and must never be retried against an already-consumed
    coroutine object.
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return asyncio.run(coro)

    if loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result(timeout=timeout)
    return loop.run_until_complete(coro)


# ---------------------------------------------------------------------------
# Shared interactive-auth guard (used by every tool handler below)
# ---------------------------------------------------------------------------

def _save_outlook_token_cache(
    access_token: str, refresh_token: str, expires_in: int, token_type: str = "Bearer"
) -> None:
    """Persist a token to ~/.hermes/outlook_token.json (same schema/location
    used by the device-code path and by GraphDeviceCodeProvider/
    GraphLoopbackAuthProvider), so every consumer keeps working unchanged
    regardless of which interactive auth flow produced the token."""
    from hermes_constants import get_hermes_home

    cache_path = get_hermes_home() / "outlook_token.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": time.time() + max(0, expires_in),
            "token_type": token_type,
        }, indent=2),
        encoding="utf-8",
    )
    try:
        cache_path.chmod(0o600)
    except Exception:
        pass


def _start_interactive_auth(scope: str | None, label: str, note: str = "") -> dict[str, Any]:
    """Start a fresh interactive sign-in and return an ``auth_required`` tool
    response, honoring ``OUTLOOK_INTERACTIVE_AUTH_FLOW`` (auto/loopback/
    device_code — see ``outlook_interactive_auth_flow()``). ``note`` is
    prepended to the message, e.g. to explain a previous attempt expired or
    failed and a new one was started automatically — this is what lets the
    model recover from an expired/invalid device code (AADSTS7000014 and
    similar) without dead-ending on a raw error, and without ever having to
    improvise its own HTTP/curl request.
    """
    prefix = f"{note} " if note else ""
    flow_mode = outlook_interactive_auth_flow()

    # Device code (legacy) is only ever used when it was actively/explicitly
    # selected in Messaging settings — never as a silent automatic fallback.
    # "auto" (the default) and "loopback" both always use the browser
    # sign-in flow; if the local loopback listener can't be bound, surface a
    # clear error asking the user to switch modes themselves instead of
    # silently downgrading to the legacy flow behind their back.
    if flow_mode == "device_code":
        try:
            info = _run_async(_start_device_code_async(scope), timeout=30)
        except Exception as exc:
            return {"error": f"Could not start device code flow: {exc}"}
        return {
            "status": "auth_required",
            "message": (
                f"{prefix}{label} authentication required. "
                f"Open {info['verification_uri']} and enter the code: {info['user_code']}. "
                f"The code expires in {info['expires_in_seconds'] // 60} minutes. Show this exact URL "
                "and code to the user; never construct your own HTTP/curl request to Microsoft's "
                "endpoints under any circumstances. Once the user has signed in, call this tool again, "
                f"unchanged, with the device_code parameter set to exactly: {info['device_code']}"
            ),
            **({"required_scopes": scope} if scope else {}),
            "verification_uri": info["verification_uri"],
            "user_code": info["user_code"],
            "device_code": info["device_code"],
            "expires_in_seconds": info["expires_in_seconds"],
            "flow": "device_code",
        }

    try:
        from tools.microsoft_graph_auth import start_loopback_auth, DEFAULT_DELEGATED_SCOPE
        creds = _get_outlook_creds()
        info = start_loopback_auth(
            creds["tenant_id"],
            creds["client_id"],
            creds["client_secret"] or None,
            (scope or DEFAULT_DELEGATED_SCOPE).strip() or DEFAULT_DELEGATED_SCOPE,
        )
        return {
            "status": "auth_required",
            "message": (
                f"{prefix}{label} sign-in required. Open this link to sign in with Microsoft: "
                f"{info['auth_url']} — there is no code to enter. Show this exact link to the "
                "user; do not construct your own sign-in URL or any HTTP/curl request. Once the "
                "user confirms they've signed in, call this tool again, unchanged, with the "
                f"device_code parameter set to exactly: lb:{info['request_id']}"
            ),
            **({"required_scopes": scope} if scope else {}),
            "verification_uri": info["auth_url"],
            "user_code": "",
            "device_code": f"lb:{info['request_id']}",
            "expires_in_seconds": info["expires_in_seconds"],
            "flow": "loopback",
        }
    except OSError as exc:
        logger.warning("[Outlook] Loopback bind failed: %s", exc)
        return {
            "error": (
                f"Loopback sign-in unavailable on this host ({exc}). Device code sign-in is "
                "only used when explicitly selected — go to Messaging → Outlook setup and set "
                "'Sign-in method' to 'Device code (legacy)' if this host cannot bind a local "
                "listener, then try again."
            )
        }


def _outlook_auth_guard(
    device_code: str,
    scope: str | None = None,
    label: str = "Outlook",
) -> tuple[dict[str, Any] | None, bool]:
    """Run the common credential/interactive-auth guard shared by all Outlook
    tools.

    Returns ``(early_response, toolset_enabled)``. If ``early_response`` is not
    ``None`` the caller must return it (json-encoded) immediately without
    performing the Graph call. Otherwise the caller may proceed, and
    ``toolset_enabled`` reflects whether this call just turned the toolset on.
    """
    creds = _get_outlook_creds()
    if not creds["tenant_id"] or not creds["client_id"]:
        return {
            "error": (
                "Outlook credentials not configured. "
                "Go to Messaging → Outlook setup and enter your Azure AD Tenant ID and Client ID."
            )
        }, False

    # Best-effort: if auth is already usable, ensure toolset is enabled now.
    # This covers chat/desktop paths where token cache exists before a tool
    # call and avoids waiting for a fresh sign-in round trip.
    _, pre_enable_error = _auto_enable_outlook_toolset_if_token_ready()
    if pre_enable_error:
        logger.warning("[Outlook] Could not auto-enable outlook toolset: %s", pre_enable_error)

    toolset_enabled = False

    # Step 1 — if caller is providing a resume id to poll (user just signed in).
    # `lb:` prefixes a loopback request_id; anything else is a device_code.
    if device_code:
        if device_code.startswith("lb:"):
            request_id = device_code[3:]
            try:
                from tools.microsoft_graph_auth import poll_loopback_auth
                status = _run_async(poll_loopback_auth(request_id), timeout=30)
            except Exception as exc:
                return _start_interactive_auth(
                    scope, label, note=f"Could not check your sign-in status ({exc})."
                ), False
            if status["status"] == "pending":
                return {
                    "status": "pending",
                    "message": (
                        "Authentication still pending. Please complete sign-in in the browser tab, "
                        "then try again."
                    ),
                }, False
            if status["status"] != "success":
                # Expired / failed — auto-restart instead of dead-ending on a raw error.
                # Note: if the user never got redirected back at all (e.g. Azure AD
                # rejected the request with its own generic error page before ever
                # calling our loopback listener — the classic symptom of
                # AADSTS500113 "No reply address is registered for the application"),
                # this will just say "expired" with no further detail, since we never
                # receive Microsoft's browser-side error. Surface that possibility
                # explicitly so the model can tell the user to check the Azure AD app's
                # redirect URI configuration instead of retrying forever.
                return _start_interactive_auth(
                    scope,
                    label,
                    note=(
                        f"That sign-in link is no longer valid "
                        f"({status.get('error', status['status'])}). If Microsoft's sign-in page "
                        "showed 'AADSTS500113: No reply address is registered for the application', "
                        "this will keep failing until the Azure AD app registration has "
                        "http://localhost added as a redirect URI under 'Mobile and desktop "
                        "applications' — tell the user to check this before retrying."
                    ),
                ), False
            _save_outlook_token_cache(
                status["access_token"],
                status["refresh_token"],
                status["expires_in"],
                status.get("token_type", "Bearer"),
            )
        else:
            try:
                authed = _run_async(_poll_device_code_async(device_code, scope), timeout=30)
            except Exception as exc:
                # e.g. AADSTS7000014 (stale/invalid/expired device code). Auto-restart
                # sign-in instead of surfacing a raw AADSTS error the model can't act on.
                # If Microsoft's own verification page showed AADSTS500113 when the code
                # was entered, the device code never gets authorized and this poll will
                # eventually time out with a generic expiry error — call that out
                # explicitly instead of retrying forever with no useful signal.
                return _start_interactive_auth(
                    scope,
                    label,
                    note=(
                        f"That sign-in code is no longer valid ({exc}). If Microsoft's page showed "
                        "'AADSTS500113: No reply address is registered for the application' when "
                        "you entered the code, this will keep failing until the Azure AD app "
                        "registration has http://localhost added as a redirect URI under 'Mobile "
                        "and desktop applications' — tell the user to check this before retrying."
                    ),
                ), False
            if not authed:
                return {
                    "status": "pending",
                    "message": (
                        "Authentication still pending. Please complete sign-in at the URL "
                        "provided, then try again."
                    ),
                }, False
        toolset_enabled, toolset_error = _auto_enable_outlook_toolset_if_token_ready()
        if toolset_error:
            logger.warning("[Outlook] Could not auto-enable outlook toolset: %s", toolset_error)
        # Fall through — now proceed with the fresh token

    # Step 2 — if no token cached, start interactive sign-in and return prompt
    if not _has_valid_token_cache():
        return _start_interactive_auth(scope, label), False

    return None, toolset_enabled


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def outlook_get_emails(
    count: int = 10,
    time_range: str = "today",
    folder: str = "inbox",
    unread_only: bool = False,
    include_body: bool = True,
    device_code: str = "",
    task_id: str | None = None,
) -> str:
    """Fetch a short, time-boxed list of emails, or handle device-code auth if no token exists."""
    early, toolset_enabled = _outlook_auth_guard(device_code, label="Outlook")
    if early is not None:
        return json.dumps(early)

    try:
        raw_emails = _run_async(
            _fetch_emails_async(count, folder, unread_only, include_body, time_range),
            timeout=120,
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    emails = [_format_email(m, include_body) for m in raw_emails]
    return json.dumps({
        "folder": folder,
        "time_range": time_range,
        "count": len(emails),
        "unread_only": unread_only,
        "emails": emails,
        "toolset_auto_enabled": bool(device_code and toolset_enabled),
    })


def outlook_read_email(
    message_id: str,
    device_code: str = "",
    task_id: str | None = None,
) -> str:
    """Fetch a single email by id with its full body content."""
    if not message_id:
        return json.dumps({"error": "message_id is required."})

    early, toolset_enabled = _outlook_auth_guard(device_code, label="Outlook")
    if early is not None:
        return json.dumps(early)

    try:
        raw = _run_async(_fetch_email_by_id_async(message_id), timeout=120)
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    email = _format_email(raw, include_body=False)
    body = raw.get("body") or {}
    email["body_content_type"] = body.get("contentType", "")
    email["body"] = body.get("content", "")
    email["cc"] = [
        r.get("emailAddress", {}).get("address", "")
        for r in raw.get("ccRecipients", [])
    ]
    email["conversation_id"] = raw.get("conversationId", "")
    email["web_link"] = raw.get("webLink", "")

    return json.dumps({
        "email": email,
        "toolset_auto_enabled": bool(device_code and toolset_enabled),
    })


def outlook_search_emails(
    query: str,
    count: int = 10,
    search_in: str = "both",
    folder: str = "inbox",
    device_code: str = "",
    task_id: str | None = None,
) -> str:
    """Search emails by keyword in subject and/or body."""
    if not query:
        return json.dumps({"error": "query is required."})

    early, toolset_enabled = _outlook_auth_guard(device_code, label="Outlook")
    if early is not None:
        return json.dumps(early)

    try:
        raw_emails = _run_async(
            _search_emails_async(query, count, search_in, folder), timeout=120
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    emails = [_format_email(m, include_body=True) for m in raw_emails]
    return json.dumps({
        "query": query,
        "search_in": search_in,
        "folder": folder,
        "count": len(emails),
        "emails": emails,
        "toolset_auto_enabled": bool(device_code and toolset_enabled),
    })


def outlook_read_shared_mail(
    mailbox: str,
    count: int = 10,
    folder: str = "inbox",
    unread_only: bool = False,
    include_body: bool = True,
    device_code: str = "",
    task_id: str | None = None,
) -> str:
    """Read emails from a shared mailbox the signed-in user has been granted access to."""
    if not mailbox:
        return json.dumps({"error": "mailbox is required (e.g. shared@company.com)."})

    early, toolset_enabled = _outlook_auth_guard(
        device_code, scope=OUTLOOK_SHARED_MAIL_SCOPE, label="Outlook shared mailbox"
    )
    if early is not None:
        return json.dumps(early)

    try:
        raw_emails = _run_async(
            _fetch_shared_mail_async(mailbox, count, folder, unread_only, include_body),
            timeout=120,
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    emails = [_format_email(m, include_body) for m in raw_emails]
    return json.dumps({
        "mailbox": mailbox,
        "folder": folder,
        "count": len(emails),
        "unread_only": unread_only,
        "emails": emails,
        "toolset_auto_enabled": bool(device_code and toolset_enabled),
    })


def outlook_write_email(
    to: str,
    subject: str = "",
    body: str = "",
    cc: str = "",
    bcc: str = "",
    reply_to_message_id: str = "",
    device_code: str = "",
    confirm: bool = False,
    task_id: str | None = None,
) -> str:
    """Compose and send a new email, or reply within an existing message thread.

    Two-step contract: without ``confirm=True`` this returns a preview
    (to/cc/bcc/subject/body) and does NOT send anything. The caller must show
    that preview to the user and get explicit approval before calling this
    tool again with the exact same fields plus ``confirm=True`` to actually
    send.
    """
    to_list = [addr.strip() for addr in to.split(",") if addr.strip()] if to else []
    cc_list = [addr.strip() for addr in cc.split(",") if addr.strip()] if cc else []
    bcc_list = [addr.strip() for addr in bcc.split(",") if addr.strip()] if bcc else []

    if not reply_to_message_id and not to_list:
        return json.dumps({
            "error": "Either 'to' (comma-separated recipients) or 'reply_to_message_id' is required."
        })
    if not reply_to_message_id and not subject:
        return json.dumps({"error": "subject is required when composing a new email."})

    early, toolset_enabled = _outlook_auth_guard(device_code, label="Outlook")
    if early is not None:
        return json.dumps(early)

    if not confirm:
        return json.dumps({
            "status": "confirmation_required",
            "message": (
                "Do not send yet. Show this exact preview (To/Cc/Bcc/Subject/Body) to the user "
                "as text, then — in the SAME turn, right after that text — you MUST call the "
                "'clarify' tool with question='Send this email?' and "
                "choices=['Ja, senden', 'Abbrechen'] (or an equivalent phrasing in the user's "
                "language). This is mandatory: do NOT just print the choices as plain text "
                "(e.g. '✅ Ja, senden / ❌ Abbrechen') and end your turn — that leaves the user "
                "with nothing clickable. Ending the turn without calling 'clarify' here is a "
                "workflow error. Only call outlook_write_email again with the same "
                "to/subject/body/cc/bcc/reply_to_message_id plus confirm=true after 'clarify' "
                "returns an affirmative answer — never send without that explicit confirmation."
            ),
            "preview": {
                "to": to_list,
                "cc": cc_list,
                "bcc": bcc_list,
                "subject": subject,
                "body": body,
                "reply_to_message_id": reply_to_message_id,
            },
        })

    try:
        verification = _run_async(
            _send_new_email_async(to_list, subject, body, cc_list, bcc_list, reply_to_message_id),
            timeout=120,
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    verified = verification.get("verified")
    response: dict[str, Any] = {
        "status": "sent" if verified is not False else "sent_unverified",
        "to": to_list,
        "cc": cc_list,
        "bcc": bcc_list,
        "subject": subject,
        "reply_to_message_id": reply_to_message_id,
        "toolset_auto_enabled": bool(device_code and toolset_enabled),
    }
    if verified is True:
        response["sent_item_id"] = verification.get("sent_item_id", "")
        if verification.get("web_link"):
            response["web_link"] = verification["web_link"]
    elif verified is False:
        # Graph accepted the send request (no 4xx/5xx) but the message never
        # showed up in Sent Items within the poll window — tell the model not
        # to claim delivery, since this previously happened silently (e.g. a
        # stale/insufficiently-scoped cached token) and was reported to the
        # user as a confident "email sent successfully". The user should NOT
        # be asked to manually confirm delivery themselves — the model has
        # the tools to check this on its own: after a short pause, call
        # outlook_get_emails(folder="sent") (or outlook_search_emails) for
        # this subject/recipient and report back once it actually knows,
        # instead of pushing that verification step onto the user.
        response["warning"] = (
            "Graph accepted the send request, but the message could not be confirmed in Sent "
            "Items within the poll window yet. Do not tell the user the email was definitely "
            "delivered, and do not ask the user to check the Sent folder themselves — instead, "
            "wait a few seconds and call outlook_get_emails(folder='sent') (or "
            "outlook_search_emails) yourself to confirm it landed, then report the actual "
            "result to the user."
        )
    return json.dumps(response)


async def _get_me_async() -> dict[str, Any]:
    """Minimal Graph call used purely to verify a delegated token actually
    works end-to-end — independent of gateway/platform connection state."""
    client, _ = _new_graph_client()
    return await client.get_json("/me", params={"$select": "displayName,mail,userPrincipalName"})


def outlook_test_connection() -> dict[str, Any]:
    """Verify the Outlook delegated auth actually works via a real Graph call.

    Unlike the platform "test" endpoint (which only reports gateway/process
    state), this performs a real ``GET /me`` request using the cached
    delegated token, so it works regardless of whether the gateway process
    is running. Does NOT start a new interactive sign-in — if there is no
    valid cached token, it reports that plainly so the UI can point the user
    at "Start Auth" instead.
    """
    creds = _get_outlook_creds()
    if not creds["tenant_id"] or not creds["client_id"]:
        return {"ok": False, "message": "Outlook credentials are not configured yet."}

    if not _has_valid_token_cache():
        return {
            "ok": False,
            "message": "Not signed in yet. Use \"Start Auth\" to sign in with Microsoft first.",
        }

    try:
        me = _run_async(_get_me_async(), timeout=30)
    except Exception as exc:
        return {"ok": False, "message": f"Connection test failed: {exc}"}

    display = me.get("displayName") or me.get("userPrincipalName") or me.get("mail") or "your mailbox"
    return {"ok": True, "message": f"Connected as {display}."}


def outlook_authenticate(device_code: str = "", task_id: str | None = None) -> str:
    """Sign in to Outlook, resume a pending sign-in, or confirm an existing
    sign-in still works — the single dedicated entry point for handling
    Outlook auth from chat.

    This is what the model should call when the user asks to "log in" /
    "sign in" / "authenticate" / "connect" Outlook, or to check whether
    Outlook is currently connected — instead of calling a read/write Outlook
    tool purely as a side effect to trigger a sign-in prompt, and instead of
    ever constructing a manual OAuth/device-code HTTP request itself. The
    entire login workflow (browser sign-in, legacy device code, token
    caching/refresh) stays inside this plugin — the model never needs to
    reason about Microsoft's endpoints directly.
    """
    early, _ = _outlook_auth_guard(device_code, label="Outlook")
    if early is not None:
        return json.dumps(early)

    # Guard returned None: either a valid cached token already existed, or
    # the device_code/lb: resume id just supplied was successfully
    # exchanged for one. Confirm it actually works end-to-end before
    # reporting success, the same way outlook_test_connection does.
    try:
        me = _run_async(_get_me_async(), timeout=30)
    except Exception as exc:
        return json.dumps({
            "ok": False,
            "error": f"Sign-in looked complete but the connection check failed: {exc}",
        })

    display = me.get("displayName") or me.get("userPrincipalName") or me.get("mail") or "your mailbox"
    return json.dumps({
        "ok": True,
        "status": "authenticated",
        "message": f"Connected to Outlook as {display}.",
    })


def outlook_read_calendar_entries(
    count: int = 10,
    days_ahead: int = 7,
    include_body_preview: bool = False,
    timezone_name: str = "UTC",
    device_code: str = "",
    task_id: str | None = None,
) -> str:
    """Fetch calendar entries, or handle device-code auth if no token exists."""
    early, toolset_enabled = _outlook_auth_guard(
        device_code, scope=OUTLOOK_CALENDAR_READ_SCOPE, label="Outlook calendar"
    )
    if early is not None:
        return json.dumps(early)

    try:
        raw_entries = _run_async(
            _fetch_calendar_entries_async(
                count=count,
                days_ahead=days_ahead,
                include_body_preview=include_body_preview,
                timezone_name=timezone_name,
            ),
            timeout=120,
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    entries = [
        _format_calendar_entry(entry, include_body_preview) for entry in raw_entries
    ]
    return json.dumps({
        "count": len(entries),
        "days_ahead": max(1, min(days_ahead, 30)),
        "timezone": timezone_name,
        "entries": entries,
        "toolset_auto_enabled": bool(device_code and toolset_enabled),
    })


def outlook_write_calendar_entries(
    action: str,
    event_id: str = "",
    subject: str = "",
    start_datetime: str = "",
    end_datetime: str = "",
    timezone_name: str = "UTC",
    location: str = "",
    body: str = "",
    attendees: str = "",
    is_all_day: bool = False,
    confirm: bool = False,
    device_code: str = "",
    task_id: str | None = None,
) -> str:
    """Create, update, or delete a calendar entry.

    ``update`` and ``delete`` are destructive: unless ``confirm=true`` is
    passed, this tool only returns a preview containing the *current* event
    state plus the requested change — it does not modify anything. The
    calling assistant MUST show this preview to the user and get explicit
    confirmation before calling again with ``confirm=true``.
    """
    normalized_action = (action or "").strip().lower()
    if normalized_action not in ("create", "update", "delete"):
        return json.dumps({
            "error": "action must be one of 'create', 'update', or 'delete'."
        })
    if normalized_action in ("update", "delete") and not event_id:
        return json.dumps({"error": "event_id is required for update/delete."})
    if normalized_action == "create" and (not subject or not start_datetime or not end_datetime):
        return json.dumps({
            "error": "subject, start_datetime, and end_datetime are required to create an event."
        })

    early, toolset_enabled = _outlook_auth_guard(
        device_code, scope=OUTLOOK_CALENDAR_WRITE_SCOPE, label="Outlook calendar (write)"
    )
    if early is not None:
        return json.dumps(early)

    attendee_list = [a.strip() for a in attendees.split(",") if a.strip()] if attendees else []

    # Destructive actions require an explicit confirm=true. Without it, fetch
    # and return the current event state as a preview so the assistant can
    # show the user exactly what would change (and can recover the previous
    # state from the response if something goes wrong after confirming).
    if normalized_action in ("update", "delete") and not confirm:
        try:
            previous_state = _run_async(_get_calendar_entry_async(event_id), timeout=60)
        except Exception as exc:
            return json.dumps({"error": f"Could not load current event state: {exc}"})

        preview: dict[str, Any] = {
            "status": "confirmation_required",
            "action": normalized_action,
            "event_id": event_id,
            "previous_state": _format_calendar_entry(previous_state, include_body_preview=True),
            "message": (
                f"This would {normalized_action} the calendar event above. Show this preview as "
                "text, then — in the SAME turn — you MUST call the 'clarify' tool with "
                "question='Apply this change?' and choices=['Ja, bestätigen', 'Abbrechen'] (or "
                "an equivalent phrasing in the user's language). Do NOT just print the choices "
                "as plain text and end your turn — that leaves the user with nothing clickable. "
                "Once 'clarify' returns an affirmative answer, call this tool again with the "
                "same arguments plus confirm=true. The previous_state above is preserved here "
                "so it can be restored manually if needed."
            ),
        }
        if normalized_action == "update":
            preview["requested_changes"] = _build_calendar_event_body(
                subject, start_datetime, end_datetime, timezone_name,
                location, body, attendee_list, is_all_day,
            )
        return json.dumps(preview)

    try:
        if normalized_action == "create":
            event_body = _build_calendar_event_body(
                subject, start_datetime, end_datetime, timezone_name,
                location, body, attendee_list, is_all_day,
            )
            result = _run_async(_create_calendar_entry_async(event_body), timeout=120)
            return json.dumps({
                "status": "created",
                "entry": _format_calendar_entry(result, include_body_preview=True),
                "toolset_auto_enabled": bool(device_code and toolset_enabled),
            })

        if normalized_action == "update":
            # Capture the previous state one more time right before applying
            # the change, so the response always carries a recoverable
            # snapshot even if the caller skipped the preview step somehow.
            previous_state = _run_async(_get_calendar_entry_async(event_id), timeout=60)
            event_body = _build_calendar_event_body(
                subject, start_datetime, end_datetime, timezone_name,
                location, body, attendee_list, is_all_day,
            )
            result = _run_async(_update_calendar_entry_async(event_id, event_body), timeout=120)
            return json.dumps({
                "status": "updated",
                "previous_state": _format_calendar_entry(previous_state, include_body_preview=True),
                "entry": _format_calendar_entry(result, include_body_preview=True),
                "toolset_auto_enabled": bool(device_code and toolset_enabled),
            })

        # delete
        previous_state = _run_async(_get_calendar_entry_async(event_id), timeout=60)
        _run_async(_delete_calendar_entry_async(event_id), timeout=120)
        return json.dumps({
            "status": "deleted",
            "event_id": event_id,
            "previous_state": _format_calendar_entry(previous_state, include_body_preview=True),
            "toolset_auto_enabled": bool(device_code and toolset_enabled),
        })
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def outlook_read_contacts(
    count: int = 20,
    search: str = "",
    device_code: str = "",
    task_id: str | None = None,
) -> str:
    """Read/search Outlook contacts (read-only)."""
    early, toolset_enabled = _outlook_auth_guard(
        device_code, scope=OUTLOOK_CONTACTS_SCOPE, label="Outlook contacts"
    )
    if early is not None:
        return json.dumps(early)

    try:
        raw_contacts = _run_async(_fetch_contacts_async(count, search.strip()), timeout=120)
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    contacts = [_format_contact(c) for c in raw_contacts]
    return json.dumps({
        "count": len(contacts),
        "search": search,
        "contacts": contacts,
        "toolset_auto_enabled": bool(device_code and toolset_enabled),
    })


def outlook_write_contacts(
    action: str,
    contact_id: str = "",
    display_name: str = "",
    given_name: str = "",
    surname: str = "",
    company_name: str = "",
    job_title: str = "",
    emails: str = "",
    business_phone: str = "",
    mobile_phone: str = "",
    notes: str = "",
    confirm: bool = False,
    device_code: str = "",
    task_id: str | None = None,
) -> str:
    """Create, update, or delete an Outlook contact.

    ``update`` and ``delete`` are destructive: unless ``confirm=true`` is
    passed, this tool only returns a preview containing the *current*
    contact state plus the requested change — it does not modify anything.
    The calling assistant MUST show this preview to the user and get
    explicit confirmation before calling again with ``confirm=true``.
    """
    normalized_action = (action or "").strip().lower()
    if normalized_action not in ("create", "update", "delete"):
        return json.dumps({
            "error": "action must be one of 'create', 'update', or 'delete'."
        })
    if normalized_action in ("update", "delete") and not contact_id:
        return json.dumps({"error": "contact_id is required for update/delete."})
    if normalized_action == "create" and not display_name and not (given_name or surname):
        return json.dumps({
            "error": "display_name (or given_name/surname) is required to create a contact."
        })

    early, toolset_enabled = _outlook_auth_guard(
        device_code, scope=OUTLOOK_CONTACTS_SCOPE, label="Outlook contacts (write)"
    )
    if early is not None:
        return json.dumps(early)

    email_list = [e.strip() for e in emails.split(",") if e.strip()] if emails else []

    # Destructive actions require an explicit confirm=true. Without it, fetch
    # and return the current contact state as a preview so the assistant can
    # show the user exactly what would change (and can recover the previous
    # state from the response if something goes wrong after confirming).
    if normalized_action in ("update", "delete") and not confirm:
        try:
            previous_state = _run_async(_get_contact_async(contact_id), timeout=60)
        except Exception as exc:
            return json.dumps({"error": f"Could not load current contact state: {exc}"})

        preview: dict[str, Any] = {
            "status": "confirmation_required",
            "action": normalized_action,
            "contact_id": contact_id,
            "previous_state": _format_contact(previous_state),
            "message": (
                f"This would {normalized_action} the contact above. Show this preview as text, "
                "then — in the SAME turn — you MUST call the 'clarify' tool with "
                "question='Apply this change?' and choices=['Ja, bestätigen', 'Abbrechen'] (or "
                "an equivalent phrasing in the user's language). Do NOT just print the choices "
                "as plain text and end your turn — that leaves the user with nothing clickable. "
                "Once 'clarify' returns an affirmative answer, call this tool again with the "
                "same arguments plus confirm=true. The previous_state above is preserved here "
                "so it can be restored manually if needed."
            ),
        }
        if normalized_action == "update":
            preview["requested_changes"] = _build_contact_body(
                display_name, given_name, surname, company_name, job_title,
                email_list, business_phone, mobile_phone, notes,
            )
        return json.dumps(preview)

    try:
        if normalized_action == "create":
            contact_body = _build_contact_body(
                display_name, given_name, surname, company_name, job_title,
                email_list, business_phone, mobile_phone, notes,
            )
            result = _run_async(_create_contact_async(contact_body), timeout=120)
            return json.dumps({
                "status": "created",
                "contact": _format_contact(result),
                "toolset_auto_enabled": bool(device_code and toolset_enabled),
            })

        if normalized_action == "update":
            previous_state = _run_async(_get_contact_async(contact_id), timeout=60)
            contact_body = _build_contact_body(
                display_name, given_name, surname, company_name, job_title,
                email_list, business_phone, mobile_phone, notes,
            )
            result = _run_async(_update_contact_async(contact_id, contact_body), timeout=120)
            return json.dumps({
                "status": "updated",
                "previous_state": _format_contact(previous_state),
                "contact": _format_contact(result) if result else _format_contact(
                    _run_async(_get_contact_async(contact_id), timeout=60)
                ),
                "toolset_auto_enabled": bool(device_code and toolset_enabled),
            })

        # delete
        previous_state = _run_async(_get_contact_async(contact_id), timeout=60)
        _run_async(_delete_contact_async(contact_id), timeout=120)
        return json.dumps({
            "status": "deleted",
            "contact_id": contact_id,
            "previous_state": _format_contact(previous_state),
            "toolset_auto_enabled": bool(device_code and toolset_enabled),
        })
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_DEVICE_CODE_PARAM_SCHEMA = {
    "type": "string",
    "description": (
        "Only set this after an auth_required response. Pass back the exact "
        "device_code value from that response, unchanged, after the user has "
        "completed sign-in at the verification/sign-in URL. Never invent your "
        "own value here and never make your own HTTP/curl requests to "
        "Microsoft's endpoints — always rely on this tool's own responses."
    ),
    "default": "",
}

registry.register(
    name="outlook_authenticate",
    toolset="outlook",
    schema={
        "name": "outlook_authenticate",
        "description": (
            "Sign in to Outlook, resume a pending sign-in, or confirm an existing sign-in still "
            "works. Use this whenever the user asks to log in / sign in / authenticate / connect "
            "Outlook, or asks whether Outlook is currently connected. This is the ONLY tool that "
            "should be used to start or check Outlook authentication — never call a read/write "
            "Outlook tool purely to trigger a sign-in prompt, and never construct your own "
            "OAuth/device-code HTTP or curl request to Microsoft's endpoints; the entire login "
            "workflow (browser sign-in, legacy device code, token caching/refresh) is handled "
            "internally by this tool."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "device_code": _DEVICE_CODE_PARAM_SCHEMA,
            },
            "required": [],
        },
    },
    handler=lambda args, **kw: outlook_authenticate(
        device_code=str(args.get("device_code", "")),
        task_id=kw.get("task_id"),
    ),
)

registry.register(
    name="outlook_get_emails",
    toolset="outlook",
    schema={
        "name": "outlook_get_emails",
        "description": (
            "Get a short, time-boxed list of emails from the Outlook / Microsoft 365 mailbox "
            "(subject, sender, date, read status, optional body preview). "
            "Use this for a quick daily/weekly overview or briefing, e.g. 'what emails did I get today'. "
            "The default limit is intentionally small (10) — if the result looks truncated, narrow the "
            "time_range or call outlook_search_emails with a keyword instead of raising count blindly. "
            "Do NOT use this to search by keyword or topic — use outlook_search_emails instead. "
            "Do NOT use this to read the full content of one specific email — use outlook_read_email "
            "with its message id instead. "
            "IMPORTANT: you have direct, live access to this mailbox through this tool — when the user "
            "asks whether they received a reply, a new message, or anything mailbox-related, call this "
            "tool YOURSELF right now to check. Never tell the user to check Outlook manually, to run a "
            "tool/command themselves, or to 'let you know later' — you can and should look it up yourself "
            "immediately."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": "Number of emails to fetch (1-50). Default 10, kept small on purpose.",
                    "default": 10,
                },
                "time_range": {
                    "type": "string",
                    "description": (
                        "Time window to fetch emails from: 'today' (default), 'yesterday', "
                        "'this_week' (Monday through now), or 'all' (no date filter)."
                    ),
                    "default": "today",
                    "enum": ["today", "yesterday", "this_week", "all"],
                },
                "folder": {
                    "type": "string",
                    "description": "Folder to read: inbox, sent, drafts, deleted, archive. Default inbox.",
                    "default": "inbox",
                    "enum": ["inbox", "sent", "drafts", "deleted", "archive"],
                },
                "unread_only": {
                    "type": "boolean",
                    "description": "If true, return only unread messages.",
                    "default": False,
                },
                "include_body": {
                    "type": "boolean",
                    "description": "Include body preview (~255 chars) in results.",
                    "default": True,
                },
                "device_code": _DEVICE_CODE_PARAM_SCHEMA,
            },
            "required": [],
        },
    },
    handler=lambda args, **kw: outlook_get_emails(
        count=int(args.get("count", 10)),
        time_range=str(args.get("time_range", "today")),
        folder=str(args.get("folder", "inbox")),
        unread_only=bool(args.get("unread_only", False)),
        include_body=bool(args.get("include_body", True)),
        device_code=str(args.get("device_code", "")),
        task_id=kw.get("task_id"),
    ),
)

registry.register(
    name="outlook_read_email",
    toolset="outlook",
    schema={
        "name": "outlook_read_email",
        "description": (
            "Read ONE specific email by its message id, returning the FULL body content "
            "(not just a preview). Use this after outlook_get_emails or outlook_search_emails "
            "returned a message id and you need the complete text to answer the user's question. "
            "Do NOT use this to browse or list multiple emails — use outlook_get_emails or "
            "outlook_search_emails for that."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": (
                        "The Graph message id, as returned in the 'id' field by outlook_get_emails "
                        "or outlook_search_emails."
                    ),
                },
                "device_code": _DEVICE_CODE_PARAM_SCHEMA,
            },
            "required": ["message_id"],
        },
    },
    handler=lambda args, **kw: outlook_read_email(
        message_id=str(args.get("message_id", "")),
        device_code=str(args.get("device_code", "")),
        task_id=kw.get("task_id"),
    ),
)

registry.register(
    name="outlook_search_emails",
    toolset="outlook",
    schema={
        "name": "outlook_search_emails",
        "description": (
            "Search the Outlook / Microsoft 365 mailbox for emails matching a keyword or phrase. "
            "Use this whenever the user asks to find an email about a topic, sender, or word "
            "(e.g. 'find the email about the invoice', 'search for emails from Alice about the contract'), "
            "or whenever the user asks if someone has replied yet / if a follow-up arrived "
            "(e.g. search for the recipient's name or the original subject). "
            "By default searches both subject and body content. "
            "Do NOT use this for a plain time-boxed overview with no keyword — use outlook_get_emails instead. "
            "IMPORTANT: you have direct, live access to this mailbox through this tool — call it YOURSELF "
            "right now instead of telling the user to search Outlook manually, to run this tool themselves "
            "later, or offering to 'check periodically' without actually checking. If asked to check for a "
            "reply, check now and report the real result (found / not found yet)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keyword or phrase to search for, e.g. 'invoice' or 'quarterly report'.",
                },
                "count": {
                    "type": "integer",
                    "description": "Maximum number of matching emails to return (1-50). Default 10.",
                    "default": 10,
                },
                "search_in": {
                    "type": "string",
                    "description": (
                        "Where to search: 'both' (subject + body, default), 'subject' only, "
                        "or 'body' only."
                    ),
                    "default": "both",
                    "enum": ["both", "subject", "body"],
                },
                "folder": {
                    "type": "string",
                    "description": "Folder to search: inbox, sent, drafts, deleted, archive. Default inbox.",
                    "default": "inbox",
                    "enum": ["inbox", "sent", "drafts", "deleted", "archive"],
                },
                "device_code": _DEVICE_CODE_PARAM_SCHEMA,
            },
            "required": ["query"],
        },
    },
    handler=lambda args, **kw: outlook_search_emails(
        query=str(args.get("query", "")),
        count=int(args.get("count", 10)),
        search_in=str(args.get("search_in", "both")),
        folder=str(args.get("folder", "inbox")),
        device_code=str(args.get("device_code", "")),
        task_id=kw.get("task_id"),
    ),
)

registry.register(
    name="outlook_read_shared_mail",
    toolset="outlook",
    schema={
        "name": "outlook_read_shared_mail",
        "description": (
            "Read emails from a SHARED mailbox (a different mailbox address the signed-in user has "
            "been granted full-access delegate permissions on in Exchange), not the user's own inbox. "
            "Use this only when the user explicitly refers to a shared/team mailbox address. "
            "Do NOT use this for the signed-in user's own mailbox — use outlook_get_emails or "
            "outlook_search_emails for that. Requires the Mail.Read.Shared permission; the mailbox "
            "must already have delegate access granted in Exchange or this call will fail with an "
            "access-denied error from Graph."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "mailbox": {
                    "type": "string",
                    "description": "Email address of the shared mailbox to read, e.g. 'support@company.com'.",
                },
                "count": {
                    "type": "integer",
                    "description": "Number of emails to fetch (1-50). Default 10.",
                    "default": 10,
                },
                "folder": {
                    "type": "string",
                    "description": "Folder to read: inbox, sent, drafts, deleted, archive. Default inbox.",
                    "default": "inbox",
                    "enum": ["inbox", "sent", "drafts", "deleted", "archive"],
                },
                "unread_only": {
                    "type": "boolean",
                    "description": "If true, return only unread messages.",
                    "default": False,
                },
                "include_body": {
                    "type": "boolean",
                    "description": "Include body preview (~255 chars) in results.",
                    "default": True,
                },
                "device_code": _DEVICE_CODE_PARAM_SCHEMA,
            },
            "required": ["mailbox"],
        },
    },
    handler=lambda args, **kw: outlook_read_shared_mail(
        mailbox=str(args.get("mailbox", "")),
        count=int(args.get("count", 10)),
        folder=str(args.get("folder", "inbox")),
        unread_only=bool(args.get("unread_only", False)),
        include_body=bool(args.get("include_body", True)),
        device_code=str(args.get("device_code", "")),
        task_id=kw.get("task_id"),
    ),
)

registry.register(
    name="outlook_write_email",
    toolset="outlook",
    schema={
        "name": "outlook_write_email",
        "description": (
            "Compose and send a NEW email from the Outlook / Microsoft 365 mailbox, or reply within an "
            "existing message thread. Use this whenever the user asks to send, write, forward, or "
            "reply to an email. Set reply_to_message_id (from outlook_get_emails/outlook_search_emails/"
            "outlook_read_email) to reply within an existing thread — this keeps the original subject and "
            "recipients. Leave reply_to_message_id empty to compose a brand-new email, in which case "
            "'to' and 'subject' are required. IMPORTANT two-step contract: call this tool WITHOUT "
            "confirm (or confirm=false) first — it returns a preview and does not send anything. Show "
            "that exact preview (To/Cc/Bcc/Subject/Body) to the user and only call this tool again with "
            "confirm=true after they explicitly approve. Never set confirm=true on the first call — "
            "sent emails cannot be recalled."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": (
                        "Comma-separated recipient email addresses, e.g. 'a@x.com,b@y.com'. "
                        "Required unless reply_to_message_id is set."
                    ),
                    "default": "",
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject. Required when composing a new email (ignored for replies).",
                    "default": "",
                },
                "body": {
                    "type": "string",
                    "description": "Plain-text email body / reply comment.",
                    "default": "",
                },
                "cc": {
                    "type": "string",
                    "description": "Comma-separated Cc recipient email addresses.",
                    "default": "",
                },
                "bcc": {
                    "type": "string",
                    "description": "Comma-separated Bcc recipient email addresses.",
                    "default": "",
                },
                "reply_to_message_id": {
                    "type": "string",
                    "description": (
                        "Graph message id to reply to within its existing thread. "
                        "When set, 'to'/'subject' are not needed — the reply goes to the original sender."
                    ),
                    "default": "",
                },
                "confirm": {
                    "type": "boolean",
                    "description": (
                        "Must be true to actually send. Leave false/unset on the first call to get a "
                        "preview; only set true after the user has explicitly confirmed that exact "
                        "preview."
                    ),
                    "default": False,
                },
                "device_code": _DEVICE_CODE_PARAM_SCHEMA,
            },
            "required": [],
        },
    },
    handler=lambda args, **kw: outlook_write_email(
        to=str(args.get("to", "")),
        subject=str(args.get("subject", "")),
        body=str(args.get("body", "")),
        cc=str(args.get("cc", "")),
        bcc=str(args.get("bcc", "")),
        reply_to_message_id=str(args.get("reply_to_message_id", "")),
        device_code=str(args.get("device_code", "")),
        confirm=bool(args.get("confirm", False)),
        task_id=kw.get("task_id"),
    ),
)

registry.register(
    name="outlook_read_calendar_entries",
    toolset="outlook",
    schema={
        "name": "outlook_read_calendar_entries",
        "description": (
            "Read Microsoft Outlook / Microsoft 365 calendar entries (read-only). "
            "Returns upcoming events for the next N days with start/end time, organizer, and location. "
            "Do NOT use this to create, change, or cancel a meeting — use "
            "outlook_write_calendar_entries for that."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": "Number of calendar entries to fetch (1-50). Default 10.",
                    "default": 10,
                },
                "days_ahead": {
                    "type": "integer",
                    "description": "How many days ahead to read (1-30). Default 7.",
                    "default": 7,
                },
                "include_body_preview": {
                    "type": "boolean",
                    "description": "Include event body preview in results.",
                    "default": False,
                },
                "timezone_name": {
                    "type": "string",
                    "description": (
                        "Outlook timezone to render times in (IANA/Windows name accepted by Graph). "
                        "Default UTC."
                    ),
                    "default": "UTC",
                },
                "device_code": _DEVICE_CODE_PARAM_SCHEMA,
            },
            "required": [],
        },
    },
    handler=lambda args, **kw: outlook_read_calendar_entries(
        count=int(args.get("count", 10)),
        days_ahead=int(args.get("days_ahead", 7)),
        include_body_preview=bool(args.get("include_body_preview", False)),
        timezone_name=str(args.get("timezone_name", "UTC")),
        device_code=str(args.get("device_code", "")),
        task_id=kw.get("task_id"),
    ),
)

registry.register(
    name="outlook_write_calendar_entries",
    toolset="outlook",
    schema={
        "name": "outlook_write_calendar_entries",
        "description": (
            "Create, update, or delete a Microsoft Outlook / Microsoft 365 calendar entry. "
            "Use action='create' to schedule a new meeting/event (subject, start_datetime, "
            "end_datetime are required). Use action='update' to change an existing event's time, "
            "subject, location, body, or attendees (event_id required). Use action='delete' to "
            "cancel an existing event (event_id required). "
            "IMPORTANT — update and delete are DESTRUCTIVE: calling them without confirm=true "
            "only returns a PREVIEW (the event's current state plus the requested change) and makes "
            "no changes at all. You MUST show this preview to the user and get their explicit "
            "confirmation before calling this tool again with confirm=true to actually apply it. "
            "The response always includes previous_state so the prior event details are not lost "
            "even after the change is applied. "
            "Do NOT use this for read-only lookups — use outlook_read_calendar_entries instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Operation to perform: 'create', 'update', or 'delete'.",
                    "enum": ["create", "update", "delete"],
                },
                "event_id": {
                    "type": "string",
                    "description": "Graph event id. Required for 'update' and 'delete'.",
                    "default": "",
                },
                "subject": {
                    "type": "string",
                    "description": "Event subject/title. Required for 'create'.",
                    "default": "",
                },
                "start_datetime": {
                    "type": "string",
                    "description": (
                        "Start date/time in ISO-8601 without timezone offset, e.g. "
                        "'2026-07-10T14:00:00'. Interpreted in timezone_name. Required for 'create'."
                    ),
                    "default": "",
                },
                "end_datetime": {
                    "type": "string",
                    "description": (
                        "End date/time in ISO-8601 without timezone offset, e.g. "
                        "'2026-07-10T15:00:00'. Interpreted in timezone_name. Required for 'create'."
                    ),
                    "default": "",
                },
                "timezone_name": {
                    "type": "string",
                    "description": (
                        "Outlook timezone for start_datetime/end_datetime (IANA/Windows name accepted "
                        "by Graph, e.g. 'UTC' or 'W. Europe Standard Time'). Default UTC."
                    ),
                    "default": "UTC",
                },
                "location": {
                    "type": "string",
                    "description": "Event location display name, e.g. 'Room A' or 'Microsoft Teams'.",
                    "default": "",
                },
                "body": {
                    "type": "string",
                    "description": "Plain-text event description/agenda.",
                    "default": "",
                },
                "attendees": {
                    "type": "string",
                    "description": "Comma-separated attendee email addresses to invite.",
                    "default": "",
                },
                "is_all_day": {
                    "type": "boolean",
                    "description": "Mark the event as an all-day event.",
                    "default": False,
                },
                "confirm": {
                    "type": "boolean",
                    "description": (
                        "Required to actually apply 'update' or 'delete'. Leave false (default) to "
                        "get a preview of the current state and requested change without modifying "
                        "anything. Only set true after the user has explicitly confirmed the change."
                    ),
                    "default": False,
                },
                "device_code": _DEVICE_CODE_PARAM_SCHEMA,
            },
            "required": ["action"],
        },
    },
    handler=lambda args, **kw: outlook_write_calendar_entries(
        action=str(args.get("action", "")),
        event_id=str(args.get("event_id", "")),
        subject=str(args.get("subject", "")),
        start_datetime=str(args.get("start_datetime", "")),
        end_datetime=str(args.get("end_datetime", "")),
        timezone_name=str(args.get("timezone_name", "UTC")),
        location=str(args.get("location", "")),
        body=str(args.get("body", "")),
        attendees=str(args.get("attendees", "")),
        is_all_day=bool(args.get("is_all_day", False)),
        confirm=bool(args.get("confirm", False)),
        device_code=str(args.get("device_code", "")),
        task_id=kw.get("task_id"),
    ),
)

registry.register(
    name="outlook_read_contacts",
    toolset="outlook",
    schema={
        "name": "outlook_read_contacts",
        "description": (
            "Read/search Outlook / Microsoft 365 contacts (read-only). Returns name, company, "
            "job title, email addresses, and phone numbers. Use 'search' to find a specific "
            "contact by name/email/company. Do NOT use this to create, change, or delete a "
            "contact — use outlook_write_contacts for that."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": "Number of contacts to fetch (1-100). Default 20.",
                    "default": 20,
                },
                "search": {
                    "type": "string",
                    "description": (
                        "Optional free-text search (matches name, email, company, etc). "
                        "Leave empty to list contacts alphabetically by display name."
                    ),
                    "default": "",
                },
                "device_code": _DEVICE_CODE_PARAM_SCHEMA,
            },
            "required": [],
        },
    },
    handler=lambda args, **kw: outlook_read_contacts(
        count=int(args.get("count", 20)),
        search=str(args.get("search", "")),
        device_code=str(args.get("device_code", "")),
        task_id=kw.get("task_id"),
    ),
)

registry.register(
    name="outlook_write_contacts",
    toolset="outlook",
    schema={
        "name": "outlook_write_contacts",
        "description": (
            "Create, update, or delete an Outlook / Microsoft 365 contact. Use action='create' "
            "to add a new contact (display_name, or given_name/surname, is required). Use "
            "action='update' to change an existing contact's details (contact_id required). Use "
            "action='delete' to remove an existing contact (contact_id required). "
            "IMPORTANT — update and delete are DESTRUCTIVE: calling them without confirm=true "
            "only returns a PREVIEW (the contact's current state plus the requested change) and "
            "makes no changes at all. You MUST show this preview to the user and get their "
            "explicit confirmation before calling this tool again with confirm=true to actually "
            "apply it. The response always includes previous_state so the prior contact details "
            "are not lost even after the change is applied. "
            "Do NOT use this for read-only lookups — use outlook_read_contacts instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Operation to perform: 'create', 'update', or 'delete'.",
                    "enum": ["create", "update", "delete"],
                },
                "contact_id": {
                    "type": "string",
                    "description": "Graph contact id. Required for 'update' and 'delete'.",
                    "default": "",
                },
                "display_name": {
                    "type": "string",
                    "description": "Full display name shown in Outlook, e.g. 'Jane Doe'.",
                    "default": "",
                },
                "given_name": {
                    "type": "string",
                    "description": "First name.",
                    "default": "",
                },
                "surname": {
                    "type": "string",
                    "description": "Last name.",
                    "default": "",
                },
                "company_name": {
                    "type": "string",
                    "description": "Company/organization name.",
                    "default": "",
                },
                "job_title": {
                    "type": "string",
                    "description": "Job title.",
                    "default": "",
                },
                "emails": {
                    "type": "string",
                    "description": "Comma-separated email addresses, e.g. 'a@x.com,b@y.com'.",
                    "default": "",
                },
                "business_phone": {
                    "type": "string",
                    "description": "Business phone number.",
                    "default": "",
                },
                "mobile_phone": {
                    "type": "string",
                    "description": "Mobile phone number.",
                    "default": "",
                },
                "notes": {
                    "type": "string",
                    "description": "Free-text personal notes about the contact.",
                    "default": "",
                },
                "confirm": {
                    "type": "boolean",
                    "description": (
                        "Required to actually apply 'update' or 'delete'. Leave false (default) to "
                        "get a preview of the current state and requested change without modifying "
                        "anything. Only set true after the user has explicitly confirmed the change."
                    ),
                    "default": False,
                },
                "device_code": _DEVICE_CODE_PARAM_SCHEMA,
            },
            "required": ["action"],
        },
    },
    handler=lambda args, **kw: outlook_write_contacts(
        action=str(args.get("action", "")),
        contact_id=str(args.get("contact_id", "")),
        display_name=str(args.get("display_name", "")),
        given_name=str(args.get("given_name", "")),
        surname=str(args.get("surname", "")),
        company_name=str(args.get("company_name", "")),
        job_title=str(args.get("job_title", "")),
        emails=str(args.get("emails", "")),
        business_phone=str(args.get("business_phone", "")),
        mobile_phone=str(args.get("mobile_phone", "")),
        notes=str(args.get("notes", "")),
        confirm=bool(args.get("confirm", False)),
        device_code=str(args.get("device_code", "")),
        task_id=kw.get("task_id"),
    ),
)
