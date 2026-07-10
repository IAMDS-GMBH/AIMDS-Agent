"""
Microsoft Outlook (Graph API) platform adapter for Hermes Agent.

Polls a mailbox via the Microsoft Graph API and sends replies through
Graph — no IMAP/SMTP credentials required.

Authentication: app-only (client_credentials grant).
  Required Azure AD app permissions (Application, not Delegated):
    • Mail.Read
    • Mail.Send

Environment variables:
    OUTLOOK_TENANT_ID      — Azure AD tenant ID
    OUTLOOK_CLIENT_ID      — Azure AD app (client) ID
    OUTLOOK_CLIENT_SECRET  — Azure AD app client secret
    OUTLOOK_MAILBOX        — UPN / address of the mailbox to monitor

Optional:
    OUTLOOK_ALLOWED_USERS  — comma-separated sender addresses that may
                             trigger the agent (all others are ignored)
    OUTLOOK_ALLOW_ALL_USERS — "true" to skip the allowlist check
    OUTLOOK_POLL_INTERVAL  — seconds between inbox polls (default: 30)
    OUTLOOK_HOME_CHANNEL   — default sender address for cron delivery

Configuration via config.yaml:
    platforms:
      outlook:
        enabled: true
        extra:
          tenant_id: "..."
          client_id: "..."
          client_secret: "..."
          mailbox: "agent@company.com"
          poll_interval: 30
          interactive_auth_flow: "auto"  # auto | loopback | device_code
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any, Dict, List, Optional

from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)
from gateway.config import Platform, PlatformConfig

logger = logging.getLogger(__name__)

# Automated sender patterns — same set used by the IMAP email adapter
_NOREPLY_PATTERNS = (
    "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply",
    "mailer-daemon", "postmaster", "bounce", "notifications@",
    "automated@", "auto-confirm", "auto-reply", "automailer",
)

# Graph internetMessageHeaders flags for bulk/automated mail
_AUTOMATED_HEADER_NAMES = frozenset({
    "auto-submitted",
    "precedence",
    "x-auto-response-suppress",
    "list-unsubscribe",
})

MAX_MESSAGE_LENGTH = 50_000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_automated_sender(address: str, internet_headers: list[dict]) -> bool:
    addr = address.lower()
    if any(p in addr for p in _NOREPLY_PATTERNS):
        return True
    for h in internet_headers:
        name = (h.get("name") or "").lower()
        value = (h.get("value") or "").lower()
        if name not in _AUTOMATED_HEADER_NAMES:
            continue
        if name == "auto-submitted" and value == "no":
            continue
        if name == "precedence" and value not in {"bulk", "list", "junk"}:
            continue
        return True
    return False


def _extract_sender_address(sender_field: dict) -> str:
    """Return the email address from a Graph sender/from field."""
    try:
        return (
            sender_field.get("emailAddress", {}).get("address") or ""
        ).lower().strip()
    except Exception:
        return ""


def _extract_sender_name(sender_field: dict) -> str:
    try:
        return (
            sender_field.get("emailAddress", {}).get("name") or ""
        ).strip()
    except Exception:
        return ""


def _strip_html(text: str) -> str:
    """Very basic HTML tag stripper for email bodies."""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _check_outlook_requirements() -> bool:
    """Return True when Outlook runtime dependencies are importable."""
    try:
        from tools.microsoft_graph_auth import (  # noqa: F401
            GraphCredentials,
            GraphDelegatedCredentials,
            GraphDeviceCodeProvider,
            GraphLoopbackAuthProvider,
            MicrosoftGraphTokenProvider,
        )
        from tools.microsoft_graph_client import MicrosoftGraphClient  # noqa: F401
        return True
    except Exception:
        return False


def _outlook_auth_mode(config: PlatformConfig) -> str:
    extra = getattr(config, "extra", {}) or {}
    raw = extra.get("auth_mode") or os.getenv("OUTLOOK_AUTH_MODE", "auto")
    mode = str(raw).strip().lower() or "auto"
    if mode in {"app", "delegated"}:
        return mode
    return "auto"


def _validate_outlook_config(config: PlatformConfig) -> bool:
    """Validate Outlook credentials from config.yaml extras or env vars."""
    extra = getattr(config, "extra", {}) or {}
    tenant_id = str(extra.get("tenant_id") or os.getenv("OUTLOOK_TENANT_ID", "")).strip()
    client_id = str(extra.get("client_id") or os.getenv("OUTLOOK_CLIENT_ID", "")).strip()
    client_secret = str(extra.get("client_secret") or os.getenv("OUTLOOK_CLIENT_SECRET", "")).strip()
    mailbox = str(extra.get("mailbox") or os.getenv("OUTLOOK_MAILBOX", "")).strip()
    mode = _outlook_auth_mode(config)
    has_delegated = bool(tenant_id and client_id)
    has_app = bool(tenant_id and client_id and client_secret and mailbox)
    if mode == "delegated":
        return has_delegated
    if mode == "app":
        return has_app
    # auto: prefer app-only when fully configured, otherwise delegated if possible.
    return has_app or has_delegated


def _is_outlook_connected(config: PlatformConfig) -> bool:
    return _validate_outlook_config(config)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class OutlookAdapter(BasePlatformAdapter):
    """Microsoft Outlook adapter using the Microsoft Graph API (app-only auth)."""

    MAX_MESSAGE_LENGTH = MAX_MESSAGE_LENGTH

    def __init__(self, config: PlatformConfig) -> None:
        super().__init__(config, Platform("outlook"))
        extra = config.extra or {}

        self._tenant_id = (
            extra.get("tenant_id") or os.getenv("OUTLOOK_TENANT_ID", "")
        ).strip()
        self._client_id = (
            extra.get("client_id") or os.getenv("OUTLOOK_CLIENT_ID", "")
        ).strip()
        self._client_secret = (
            extra.get("client_secret") or os.getenv("OUTLOOK_CLIENT_SECRET", "")
        ).strip()
        self._mailbox = (
            extra.get("mailbox") or os.getenv("OUTLOOK_MAILBOX", "")
        ).strip().lower()
        self._poll_interval = int(
            extra.get("poll_interval") or os.getenv("OUTLOOK_POLL_INTERVAL", "30")
        )

        # Thread context: sender → {subject, message_id} for reply threading
        self._thread_context: Dict[str, Dict[str, str]] = {}
        # IDs of messages we have already dispatched
        self._seen_ids: set[str] = set()
        self._seen_ids_max = 2000

        self._poll_task: Optional[asyncio.Task] = None
        self._graph: Optional[Any] = None  # MicrosoftGraphClient

    @property
    def _mailbox_root(self) -> str:
        """Graph API path prefix for the active mailbox.
        Delegated auth uses /me; app-only uses /users/{address}.
        """
        if self._mailbox == "me":
            return "/me"
        return f"/users/{self._mailbox}"

    @property
    def enforces_own_access_policy(self) -> bool:
        """Outlook gates inbound senders at intake via its own allowlist.

        ``_on_new_message`` already applies ``OUTLOOK_ALLOWED_USERS`` /
        ``OUTLOOK_ALLOW_ALL_USERS`` (default: allow all senders) before an
        event ever reaches the gateway. Without this flag, the gateway's
        generic ``_is_user_authorized`` would re-check the sender against the
        chat-bot pairing store, find every ordinary email correspondent
        "unauthorized" (they were never meant to be paired bot users), and
        auto-send them a raw pairing-code reply via ``sendMail`` — invisible
        to the chat UI and sent to real external contacts. See bug: an
        inbound reply from a legitimate business contact triggered
        `Unauthorized user: ... on outlook` and a pairing-code email sent
        straight out of the user's mailbox.
        """
        return True

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        auth_mode = _outlook_auth_mode(self.config)
        if auth_mode == "auto":
            auth_mode = (
                "app"
                if all([self._tenant_id, self._client_id, self._client_secret, self._mailbox])
                else "delegated"
            )
        extra = self.config.extra or {}
        logger.info("[Outlook] Using auth mode: %s", auth_mode)

        if auth_mode == "delegated":
            if not all([self._tenant_id, self._client_id]):
                self._set_fatal_error(
                    "MISSING_CREDENTIALS",
                    "OUTLOOK_TENANT_ID and OUTLOOK_CLIENT_ID are required for delegated auth.",
                    retryable=False,
                )
                return False
            try:
                from tools.microsoft_graph_auth import (
                    GraphDelegatedCredentials,
                    GraphDeviceCodeProvider,
                    GraphLoopbackAuthProvider,
                )
                from tools.outlook_tool import outlook_interactive_auth_flow
                creds = GraphDelegatedCredentials(
                    tenant_id=self._tenant_id,
                    client_id=self._client_id,
                    client_secret=self._client_secret,  # Include secret if provided
                )
                provider_cls = (
                    GraphDeviceCodeProvider
                    if outlook_interactive_auth_flow() == "device_code"
                    else GraphLoopbackAuthProvider
                )
                provider = provider_cls(creds)
                from tools.microsoft_graph_client import MicrosoftGraphClient
                self._graph = MicrosoftGraphClient(provider, user_agent="Hermes-Outlook/1.0")
                # Use /me for delegated — mailbox is the signed-in user
                self._mailbox = "me"
                await self._graph.get_json("/me/mailFolders/inbox")
            except Exception as exc:
                self._set_fatal_error(
                    "CONNECT_FAILED",
                    f"Outlook delegated auth failed: {exc}",
                    retryable=True,
                )
                logger.error("[Outlook] Delegated auth connection failed: %s", exc)
                return False
        else:
            if not all([self._tenant_id, self._client_id, self._client_secret, self._mailbox]):
                self._set_fatal_error(
                    "MISSING_CREDENTIALS",
                    "OUTLOOK_TENANT_ID, OUTLOOK_CLIENT_ID, OUTLOOK_CLIENT_SECRET, "
                    "and OUTLOOK_MAILBOX are all required for app-only auth.",
                    retryable=False,
                )
                return False
            try:
                from tools.microsoft_graph_auth import GraphCredentials, MicrosoftGraphTokenProvider
                from tools.microsoft_graph_client import MicrosoftGraphClient
                creds = GraphCredentials(
                    tenant_id=self._tenant_id,
                    client_id=self._client_id,
                    client_secret=self._client_secret,
                )
                provider = MicrosoftGraphTokenProvider(creds)
                self._graph = MicrosoftGraphClient(provider, user_agent="Hermes-Outlook/1.0")
                await self._graph.get_json(f"{self._mailbox_root}/mailFolders/inbox")
            except Exception as exc:
                self._set_fatal_error(
                    "CONNECT_FAILED",
                    f"Outlook Graph connection failed: {exc}",
                    retryable=True,
                )
                logger.error("[Outlook] Connection failed: %s", exc)
                return False

        # Seed seen IDs from existing unread messages so we don't replay history
        try:
            existing = await self._fetch_unread_messages()
            for msg in existing:
                self._seen_ids.add(msg["id"])
            logger.info(
                "[Outlook] Connected as %s — %d existing unread messages skipped.",
                self._mailbox, len(self._seen_ids),
            )
        except Exception as exc:
            logger.warning("[Outlook] Could not seed seen IDs: %s", exc)

        self._running = True
        self._mark_connected()
        self._poll_task = asyncio.create_task(self._poll_loop())
        print(f"[Outlook] Connected — monitoring {self._mailbox}")
        return True

    async def disconnect(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        self._graph = None
        self._mark_disconnected()
        logger.info("[Outlook] Disconnected.")

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {"name": chat_id, "type": "dm"}

    # ------------------------------------------------------------------
    # Poll loop
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self._check_inbox()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("[Outlook] Poll error: %s", exc)
            await asyncio.sleep(self._poll_interval)

    async def _check_inbox(self) -> None:
        messages = await self._fetch_unread_messages()
        for msg in messages:
            msg_id = msg.get("id", "")
            if not msg_id or msg_id in self._seen_ids:
                continue
            self._seen_ids.add(msg_id)
            self._trim_seen_ids()
            await self._dispatch_message(msg)

    async def _fetch_unread_messages(self) -> List[Dict[str, Any]]:
        """Fetch unread messages from inbox via Graph API."""
        if self._graph is None:
            return []
        try:
            data = await self._graph.get_json(
                f"{self._mailbox_root}/mailFolders/inbox/messages",
                params={
                    "$filter": "isRead eq false",
                    "$orderby": "receivedDateTime asc",
                    "$top": "50",
                    "$select": (
                        "id,subject,from,sender,toRecipients,receivedDateTime,"
                        "body,bodyPreview,internetMessageId,conversationId,"
                        "internetMessageHeaders,isRead"
                    ),
                },
            )
            return data.get("value") or []
        except Exception as exc:
            logger.error("[Outlook] Fetch unread failed: %s", exc)
            return []

    def _trim_seen_ids(self) -> None:
        if len(self._seen_ids) <= self._seen_ids_max:
            return
        keep = list(self._seen_ids)[-self._seen_ids_max // 2:]
        self._seen_ids = set(keep)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def _dispatch_message(self, msg: Dict[str, Any]) -> None:
        sender_field = msg.get("from") or msg.get("sender") or {}
        sender_addr = _extract_sender_address(sender_field)
        sender_name = _extract_sender_name(sender_field)

        # Skip self-messages (only meaningful in app-only mode where mailbox is an email address)
        if self._mailbox != "me" and sender_addr == self._mailbox:
            return

        # Skip automated senders
        headers = msg.get("internetMessageHeaders") or []
        if _is_automated_sender(sender_addr, headers):
            logger.debug("[Outlook] Skipping automated sender: %s", sender_addr)
            return

        # Allowlist check
        allowed_raw = os.getenv("OUTLOOK_ALLOWED_USERS", "").strip()
        allow_all = os.getenv("OUTLOOK_ALLOW_ALL_USERS", "").lower() in {"1", "true", "yes"}
        if not allow_all and allowed_raw:
            allowed = {a.strip().lower() for a in allowed_raw.split(",") if a.strip()}
            if sender_addr not in allowed:
                logger.debug("[Outlook] Dropping non-allowlisted sender: %s", sender_addr)
                return

        subject = (msg.get("subject") or "(no subject)").strip()
        body_obj = msg.get("body") or {}
        raw_body = body_obj.get("content") or msg.get("bodyPreview") or ""
        if body_obj.get("contentType", "").lower() == "html":
            body_text = _strip_html(raw_body)
        else:
            body_text = raw_body.strip()

        text = body_text
        if subject and not subject.lower().startswith("re:"):
            text = f"[Subject: {subject}]\n\n{body_text}"

        internet_msg_id = msg.get("internetMessageId") or msg.get("id") or ""

        self._thread_context[sender_addr] = {
            "subject": subject,
            "internet_message_id": internet_msg_id,
            "conversation_id": msg.get("conversationId") or "",
        }

        # Mark as read so it doesn't re-appear next poll
        asyncio.create_task(self._mark_read(msg["id"]))

        source = self.build_source(
            chat_id=sender_addr,
            chat_name=sender_name or sender_addr,
            chat_type="dm",
            user_id=sender_addr,
            user_name=sender_name or sender_addr,
        )

        event = MessageEvent(
            text=text or "(empty email)",
            message_type=MessageType.TEXT,
            source=source,
            message_id=internet_msg_id,
        )

        logger.info("[Outlook] New message from %s: %s", sender_addr, subject)
        await self.handle_message(event)

    async def _mark_read(self, graph_message_id: str) -> None:
        """Mark a message as read via Graph PATCH so it won't be re-fetched."""
        if self._graph is None:
            return
        try:
            await self._graph.patch_json(
                f"{self._mailbox_root}/messages/{graph_message_id}",
                json_body={"isRead": True},
            )
        except Exception as exc:
            logger.debug("[Outlook] Could not mark message %s as read: %s", graph_message_id, exc)

    # ------------------------------------------------------------------
    # Send
    # ------------------------------------------------------------------

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        if self._graph is None:
            return SendResult(success=False, error="Not connected")
        try:
            await self._send_email(chat_id, content)
            return SendResult(success=True)
        except Exception as exc:
            logger.error("[Outlook] Send failed to %s: %s", chat_id, exc)
            return SendResult(success=False, error=str(exc))

    async def _send_email(self, to_addr: str, body: str) -> None:
        ctx = self._thread_context.get(to_addr, {})
        subject = ctx.get("subject", "Hermes Agent")
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        message: Dict[str, Any] = {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": to_addr}}],
        }

        # Thread on existing conversation when possible
        conversation_id = ctx.get("conversation_id")
        if conversation_id:
            message["conversationId"] = conversation_id

        await self._graph.post_json(
            f"{self._mailbox_root}/sendMail",
            json_body={"message": message, "saveToSentItems": True},
        )
        logger.info("[Outlook] Sent reply to %s (subject: %s)", to_addr, subject)

    async def send_typing(self, chat_id: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Email has no typing indicator — no-op."""

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> SendResult:
        text = (caption or "") + f"\n\nImage: {image_url}"
        return await self.send(chat_id, text.strip(), reply_to)


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def _outlook_setup() -> None:
    """Interactive setup: collect Azure AD credentials, save to .env, enable in config."""
    from hermes_cli.config import (
        load_config,
        save_config,
        save_env_value,
        get_env_value,
    )

    print()
    print("  ─── 📧 Microsoft Outlook (Graph API) Setup ───")
    print()
    print("  You will need an Azure AD app registration with:")
    print("    • Mail.Read  (Application permission)")
    print("    • Mail.Send  (Application permission)")
    print("    • Calendars.Read (Delegated permission, if using outlook_read_calendar_entries tool)")
    print("  Docs: https://portal.azure.com/#view/Microsoft_AAD_IAM/ActiveDirectoryMenuBlade/~/RegisteredApps")
    print()

    _VARS = [
        ("OUTLOOK_TENANT_ID",     "Azure AD Tenant ID",              False),
        ("OUTLOOK_CLIENT_ID",     "Azure AD Application (Client) ID", False),
        ("OUTLOOK_CLIENT_SECRET", "Azure AD Client Secret",           True),
        ("OUTLOOK_MAILBOX",       "Mailbox UPN / email address",      False),
    ]
    _OPTIONAL_VARS = [
        ("OUTLOOK_ALLOWED_USERS",  "Allowed senders (comma-separated, leave empty for all)", False),
        ("OUTLOOK_POLL_INTERVAL",  "Poll interval in seconds (default: 30)",                 False),
        ("OUTLOOK_HOME_CHANNEL",   "Home channel email for cron delivery (optional)",        False),
    ]

    for env_var, prompt_text, is_password in _VARS:
        existing = get_env_value(env_var)
        if existing:
            display = ("*" * 8) if is_password else existing
            print(f"  {prompt_text}: (current: {display})")
            answer = input(f"  Leave empty to keep, or enter new value: ").strip()
        else:
            if is_password:
                import getpass
                answer = getpass.getpass(f"  {prompt_text}: ").strip()
            else:
                answer = input(f"  {prompt_text}: ").strip()

        if answer:
            save_env_value(env_var, answer)
            print(f"  ✓ Saved {env_var}")

    print()
    print("  Optional settings (press Enter to skip):")
    for env_var, prompt_text, is_password in _OPTIONAL_VARS:
        existing = get_env_value(env_var)
        display_default = f" (current: {existing})" if existing else ""
        answer = input(f"  {prompt_text}{display_default}: ").strip()
        if answer:
            save_env_value(env_var, answer)
            print(f"  ✓ Saved {env_var}")

    # Enable the platform in config.yaml. Keep the legacy top-level block for
    # backward compatibility, but write the canonical platforms.outlook toggle.
    config = load_config()
    platforms = config.get("platforms")
    if not isinstance(platforms, dict):
        platforms = {}
        config["platforms"] = platforms
    platform_cfg = platforms.get("outlook")
    if not isinstance(platform_cfg, dict):
        platform_cfg = {}
        platforms["outlook"] = platform_cfg
    platform_cfg["enabled"] = True

    if "outlook" not in config:
        config["outlook"] = {}
    config["outlook"]["enabled"] = True
    save_config(config)
    print()
    print("  ✓ platforms.outlook.enabled = true written to config.yaml")
    print("  ✓ Outlook setup complete — restart the gateway to connect.")
    print()


def register(ctx: Any) -> None:
    """Register the Outlook platform adapter with the Hermes plugin system."""
    ctx.register_platform(
        name="outlook",
        label="Microsoft Outlook (Graph API)",
        emoji="📧",
        platform_hint=(
            "You are operating as an AI agent via Microsoft Outlook email. "
            "Messages arrive as emails; replies are sent as email replies. "
            "Keep responses concise and professional."
        ),
        adapter_factory=OutlookAdapter,
        check_fn=_check_outlook_requirements,
        validate_config=_validate_outlook_config,
        is_connected=_is_outlook_connected,
        setup_fn=_outlook_setup,
        allowed_users_env="OUTLOOK_ALLOWED_USERS",
        allow_all_env="OUTLOOK_ALLOW_ALL_USERS",
        cron_deliver_env_var="OUTLOOK_HOME_CHANNEL",
    )
