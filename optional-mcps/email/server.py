"""IMAP / SMTP Email MCP Server.

Provides access to standard email servers via IMAP (fetching/searching emails)
and SMTP (sending emails) with configurable SSL/TLS modes and custom ports.
"""

from __future__ import annotations

import email
import email.header
import email.mime.text
import email.mime.multipart
import imaplib
import os
import smtplib
import ssl
from datetime import datetime
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

# Initialize FastMCP Server
mcp = FastMCP("EmailMCP")


def _decode_header(header_value: Optional[str]) -> str:
    if not header_value:
        return ""
    decoded_parts = []
    for content, encoding in email.header.decode_header(header_value):
        if isinstance(content, bytes):
            charset = encoding or "utf-8"
            try:
                decoded_parts.append(content.decode(charset, errors="replace"))
            except (LookupError, UnicodeDecodeError):
                decoded_parts.append(content.decode("utf-8", errors="replace"))
        else:
            decoded_parts.append(str(content))
    return "".join(decoded_parts)


def _get_imap_config() -> Dict[str, Any]:
    host = os.environ.get("EMAIL_IMAP_HOST", "").strip()
    port_str = os.environ.get("EMAIL_IMAP_PORT", "").strip()
    mode = os.environ.get("EMAIL_IMAP_MODE", "ssl").strip().lower()
    user = os.environ.get("EMAIL_IMAP_USER", "").strip()
    password = os.environ.get("EMAIL_IMAP_PASSWORD", "").strip()

    port = int(port_str) if port_str.isdigit() else (993 if mode == "ssl" else 143)
    return {
        "host": host,
        "port": port,
        "mode": mode,
        "user": user,
        "password": password,
    }


def _get_smtp_config() -> Dict[str, Any]:
    host = os.environ.get("EMAIL_SMTP_HOST", "").strip()
    port_str = os.environ.get("EMAIL_SMTP_PORT", "").strip()
    mode = os.environ.get("EMAIL_SMTP_MODE", "starttls").strip().lower()
    user = os.environ.get("EMAIL_SMTP_USER", "").strip()
    password = os.environ.get("EMAIL_SMTP_PASSWORD", "").strip()

    port = int(port_str) if port_str.isdigit() else (465 if mode == "ssl" else 587)
    return {
        "host": host,
        "port": port,
        "mode": mode,
        "user": user,
        "password": password,
    }


def _connect_imap() -> imaplib.IMAP4:
    cfg = _get_imap_config()
    if not cfg["host"] or not cfg["user"]:
        raise RuntimeError("IMAP configuration missing (EMAIL_IMAP_HOST, EMAIL_IMAP_USER required).")

    context = ssl.create_default_context()
    if cfg["mode"] == "ssl":
        client = imaplib.IMAP4_SSL(cfg["host"], cfg["port"], ssl_context=context)
    else:
        client = imaplib.IMAP4(cfg["host"], cfg["port"])
        if cfg["mode"] == "starttls":
            client.starttls(ssl_context=context)

    client.login(cfg["user"], cfg["password"])
    return client


def _connect_smtp() -> smtplib.SMTP:
    cfg = _get_smtp_config()
    if not cfg["host"] or not cfg["user"]:
        raise RuntimeError("SMTP configuration missing (EMAIL_SMTP_HOST, EMAIL_SMTP_USER required).")

    context = ssl.create_default_context()
    if cfg["mode"] == "ssl":
        client = smtplib.SMTP_SSL(cfg["host"], cfg["port"], context=context, timeout=30.0)
    else:
        client = smtplib.SMTP(cfg["host"], cfg["port"], timeout=30.0)
        if cfg["mode"] == "starttls":
            client.starttls(context=context)

    if cfg["user"] and cfg["password"]:
        client.login(cfg["user"], cfg["password"])
    return client


def _extract_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            if content_type == "text/plain" and "attachment" not in content_disposition:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        # Fallback to HTML if plain text not found
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    return ""


# ─── Tools ───────────────────────────────────────────────────────────────────


@mcp.tool()
def email_test_connection() -> Dict[str, Any]:
    """Test connection and credentials for both IMAP and SMTP servers."""
    results = {"imap": {"ok": False}, "smtp": {"ok": False}}

    # Test IMAP
    try:
        imap_client = _connect_imap()
        imap_client.select("INBOX", readonly=True)
        results["imap"] = {"ok": True, "message": "IMAP connection and authentication successful."}
        imap_client.logout()
    except Exception as exc:
        results["imap"] = {"ok": False, "error": str(exc)}

    # Test SMTP
    try:
        smtp_client = _connect_smtp()
        results["smtp"] = {"ok": True, "message": "SMTP connection and authentication successful."}
        smtp_client.quit()
    except Exception as exc:
        results["smtp"] = {"ok": False, "error": str(exc)}

    return {
        "success": results["imap"]["ok"] and results["smtp"]["ok"],
        "details": results,
    }


@mcp.tool()
def email_fetch_inbox(
    count: int = 10,
    unread_only: bool = False,
    folder: str = "INBOX",
) -> Dict[str, Any]:
    """Fetch recent emails from the mailbox via IMAP."""
    imap_client = _connect_imap()
    try:
        imap_client.select(folder, readonly=True)
        search_criteria = "UNSEEN" if unread_only else "ALL"
        status, data = imap_client.search(None, search_criteria)
        if status != "OK" or not data or not data[0]:
            return {"folder": folder, "count": 0, "emails": []}

        msg_ids = data[0].split()
        fetch_ids = msg_ids[-min(count, 50):]
        fetch_ids.reverse()  # Newest first

        emails_list = []
        for msg_id in fetch_ids:
            res_status, msg_data = imap_client.fetch(msg_id, "(RFC822)")
            if res_status != "OK" or not msg_data:
                continue

            raw_email = msg_data[0][1]
            if isinstance(raw_email, bytes):
                msg = email.message_from_bytes(raw_email)
                emails_list.append({
                    "id": msg_id.decode("utf-8"),
                    "subject": _decode_header(msg.get("Subject")),
                    "from": _decode_header(msg.get("From")),
                    "to": _decode_header(msg.get("To")),
                    "date": msg.get("Date", ""),
                    "body_preview": _extract_body(msg)[:300],
                })

        return {
            "folder": folder,
            "count": len(emails_list),
            "emails": emails_list,
        }
    finally:
        try:
            imap_client.logout()
        except Exception:
            pass


@mcp.tool()
def email_search_messages(
    query: Optional[str] = None,
    sender: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    count: int = 10,
    folder: str = "INBOX",
) -> Dict[str, Any]:
    """Search emails in the mailbox using IMAP search criteria."""
    imap_client = _connect_imap()
    try:
        imap_client.select(folder, readonly=True)
        criteria = []

        if query:
            criteria.extend(["TEXT", f'"{query}"'])
        if sender:
            criteria.extend(["FROM", f'"{sender}"'])
        if date_from:
            try:
                dt = datetime.strptime(date_from, "%Y-%m-%d")
                criteria.extend(["SINCE", dt.strftime("%d-%b-%Y")])
            except ValueError:
                pass
        if date_to:
            try:
                dt = datetime.strptime(date_to, "%Y-%m-%d")
                criteria.extend(["BEFORE", dt.strftime("%d-%b-%Y")])
            except ValueError:
                pass

        search_str = " ".join(criteria) if criteria else "ALL"
        status, data = imap_client.search(None, search_str)
        if status != "OK" or not data or not data[0]:
            return {"query": query, "count": 0, "emails": []}

        msg_ids = data[0].split()
        fetch_ids = msg_ids[-min(count, 50):]
        fetch_ids.reverse()

        emails_list = []
        for msg_id in fetch_ids:
            res_status, msg_data = imap_client.fetch(msg_id, "(RFC822)")
            if res_status != "OK" or not msg_data:
                continue

            raw_email = msg_data[0][1]
            if isinstance(raw_email, bytes):
                msg = email.message_from_bytes(raw_email)
                emails_list.append({
                    "id": msg_id.decode("utf-8"),
                    "subject": _decode_header(msg.get("Subject")),
                    "from": _decode_header(msg.get("From")),
                    "to": _decode_header(msg.get("To")),
                    "date": msg.get("Date", ""),
                    "body_preview": _extract_body(msg)[:300],
                })

        return {
            "query": query,
            "count": len(emails_list),
            "emails": emails_list,
        }
    finally:
        try:
            imap_client.logout()
        except Exception:
            pass


@mcp.tool()
def email_send_message(
    to: List[str],
    subject: str,
    body: str,
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None,
    is_html: bool = False,
) -> Dict[str, Any]:
    """Send an email message via SMTP."""
    smtp_cfg = _get_smtp_config()
    sender_addr = smtp_cfg["user"]
    if not sender_addr:
        raise RuntimeError("SMTP user address missing.")

    mime_msg = email.mime.text.MIMEText(body, "html" if is_html else "plain", "utf-8")
    mime_msg["Subject"] = subject
    mime_msg["From"] = sender_addr
    mime_msg["To"] = ", ".join(to)

    recipients = list(to)
    if cc:
        mime_msg["Cc"] = ", ".join(cc)
        recipients.extend(cc)
    if bcc:
        recipients.extend(bcc)

    smtp_client = _connect_smtp()
    try:
        smtp_client.sendmail(sender_addr, recipients, mime_msg.as_string())
        return {
            "success": True,
            "to": to,
            "subject": subject,
            "message": "Email sent successfully via SMTP.",
        }
    finally:
        try:
            smtp_client.quit()
        except Exception:
            pass


if __name__ == "__main__":
    mcp.run()
