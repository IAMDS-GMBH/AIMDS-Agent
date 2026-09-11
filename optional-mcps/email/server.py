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

import json
import re
import sqlite3
from pathlib import Path

# Initialize FastMCP Server
mcp = FastMCP("EmailMCP")


# ─── Audit trail & trash safety (AIS-231) ────────────────────────────────────
# Every write action (send, move, trash) is logged by the tool handler itself
# into a local SQLite file; hard delete does not exist — "delete" is a move to
# the Trash folder. Nothing here may break a tool call.

_TRASH_NAME_CANDIDATES = ("trash", "deleted items", "deleted messages", "papierkorb", "gelöschte elemente", "geloeschte elemente", "bin", "corbeille")
_LIST_LINE_RE = re.compile(r'^\((?P<flags>[^)]*)\)\s+"?(?P<delim>[^"\s]*)"?\s+"?(?P<name>.+?)"?$')


def _audit_path() -> Path:
    override = os.environ.get("EMAIL_AUDIT_PATH")
    if override:
        return Path(override).expanduser()
    hermes_home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    return Path(hermes_home) / "state" / "email_audit.sqlite"


def _audit_conn() -> Optional[sqlite3.Connection]:
    try:
        path = _audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, tool TEXT NOT NULL, "
            "action TEXT NOT NULL, target_id TEXT, subject TEXT, counterpart TEXT, details TEXT NOT NULL DEFAULT '{}', "
            "result TEXT NOT NULL, error TEXT)"
        )
        conn.commit()
        return conn
    except Exception:
        return None


def _audit_log(tool: str, action: str, *, target_id: str = "", subject: str = "", counterpart: str = "",
               details: Optional[Dict[str, Any]] = None, result: str = "ok", error: Optional[str] = None) -> None:
    conn = _audit_conn()
    if conn is None:
        return
    try:
        with conn:
            conn.execute(
                "INSERT INTO audit_log (at, tool, action, target_id, subject, counterpart, details, result, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (datetime.utcnow().replace(microsecond=0).isoformat() + "Z", tool, action, str(target_id or ""), str(subject or "")[:200],
                 str(counterpart or "")[:300], json.dumps(details or {}, ensure_ascii=False, sort_keys=True), result, (str(error)[:500] if error else None)),
            )
    except Exception:
        pass
    finally:
        conn.close()


def _audit_entries(limit: int = 20, action: Optional[str] = None, since: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _audit_conn()
    if conn is None:
        return []
    try:
        sql, args = "SELECT * FROM audit_log", []
        clauses = []
        if action:
            clauses.append("action = ?"); args.append(action)
        if since:
            clauses.append("at >= ?"); args.append(since)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(max(1, min(int(limit), 200)))
        out = []
        for r in conn.execute(sql, args).fetchall():
            try:
                details = json.loads(r["details"] or "{}")
            except Exception:
                details = {}
            out.append({"id": r["id"], "at": r["at"], "tool": r["tool"], "action": r["action"], "target_id": r["target_id"],
                        "subject": r["subject"], "counterpart": r["counterpart"], "details": details, "result": r["result"], "error": r["error"]})
        return out
    except Exception:
        return []
    finally:
        conn.close()


def _parse_list_line(line: Any) -> Optional[Dict[str, str]]:
    text = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else str(line or "")
    m = _LIST_LINE_RE.match(text.strip())
    if not m:
        return None
    return {"flags": m.group("flags").lower(), "delimiter": m.group("delim"), "name": m.group("name").strip().strip('"')}


def _find_trash_folder(imap_client: imaplib.IMAP4) -> str:
    """The Trash mailbox: EMAIL_TRASH_FOLDER, else the \\Trash special-use flag, else a well-known name."""
    override = os.environ.get("EMAIL_TRASH_FOLDER", "").strip()
    if override:
        return override
    try:
        status, boxes = imap_client.list()
    except Exception:
        status, boxes = "NO", []
    parsed = [p for p in (_parse_list_line(b) for b in (boxes or [])) if p] if status == "OK" else []
    for box in parsed:
        if "\\trash" in box["flags"]:
            return box["name"]
    for box in parsed:
        leaf = box["name"].split(box["delimiter"])[-1].lower() if box["delimiter"] else box["name"].lower()
        if leaf in _TRASH_NAME_CANDIDATES or box["name"].lower() in _TRASH_NAME_CANDIDATES:
            return box["name"]
    return "Trash"


def _quote_mailbox(name: str) -> str:
    return name if name.startswith('"') else '"' + name.replace('"', '\\"') + '"'


def _message_summary(imap_client: imaplib.IMAP4, msg_id: str) -> Dict[str, str]:
    try:
        status, data = imap_client.fetch(msg_id, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM)])")
    except Exception:
        return {}
    if status != "OK" or not data or not isinstance(data[0], tuple):
        return {}
    msg = email.message_from_bytes(data[0][1])
    return {"subject": _decode_header(msg.get("Subject")), "from": _decode_header(msg.get("From"))}


def _move_message(tool: str, action: str, message_id: str, destination: Optional[str], folder: str) -> Dict[str, Any]:
    mid = str(message_id or "").strip()
    if not mid.isdigit():
        return {"error": "message_id must be the numeric id returned by email_fetch_inbox / email_search_messages"}
    imap_client = _connect_imap()
    try:
        imap_client.select(folder)
        dest = destination or _find_trash_folder(imap_client)
        summary = _message_summary(imap_client, mid)
        status, _ = imap_client.copy(mid, _quote_mailbox(dest))
        if status != "OK":
            err = f"IMAP COPY to {dest!r} failed"
            _audit_log(tool, action, target_id=mid, subject=summary.get("subject", ""), counterpart=summary.get("from", ""),
                       details={"from_folder": folder, "destination": dest}, result="error", error=err)
            return {"error": err}
        imap_client.store(mid, "+FLAGS", "(\\Deleted)")
        imap_client.expunge()
        _audit_log(tool, action, target_id=mid, subject=summary.get("subject", ""), counterpart=summary.get("from", ""),
                   details={"from_folder": folder, "destination": dest})
        out = {"success": True, "action": action, "message_id": mid, "from_folder": folder, "destination": dest,
               "subject": summary.get("subject", ""), "from": summary.get("from", ""), "audited": True}
        if action == "trash":
            out["note"] = "Moved to the Trash folder (soft delete). Hard delete is not available through this MCP."
        return out
    except Exception as exc:
        _audit_log(tool, action, target_id=mid, details={"from_folder": folder, "destination": destination or "trash"}, result="error", error=str(exc))
        raise
    finally:
        try:
            imap_client.logout()
        except Exception:
            pass


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
        try:
            smtp_client.sendmail(sender_addr, recipients, mime_msg.as_string())
        except Exception as exc:
            _audit_log("email_send_message", "send", subject=subject, counterpart=", ".join(recipients), details={"html": bool(is_html)}, result="error", error=str(exc))
            raise
        _audit_log("email_send_message", "send", subject=subject, counterpart=", ".join(recipients),
                   details={"to": list(to), "cc": list(cc or []), "bcc_count": len(bcc or []), "html": bool(is_html)})
        return {
            "success": True,
            "to": to,
            "subject": subject,
            "message": "Email sent successfully via SMTP.",
            "audited": True,
        }
    finally:
        try:
            smtp_client.quit()
        except Exception:
            pass


@mcp.tool()
def email_move_message(message_id: str, destination_folder: str, folder: str = "INBOX") -> Dict[str, Any]:
    """Move an email to another IMAP folder (COPY + flag + EXPUNGE). Logged in the audit trail.

    Args:
        message_id: Numeric id from email_fetch_inbox / email_search_messages (valid within `folder`).
        destination_folder: Target mailbox name as listed by the server (e.g. "Archive", "INBOX/Projekte").
        folder: Source mailbox (default INBOX).
    """
    if not (destination_folder or "").strip():
        return {"error": "destination_folder is required"}
    return _move_message("email_move_message", "move", message_id, destination_folder.strip(), folder or "INBOX")


@mcp.tool()
def email_trash_message(message_id: str, folder: str = "INBOX") -> Dict[str, Any]:
    """Move an email to the Trash folder (soft delete). This MCP never hard-deletes mail; every trash action is logged.

    The Trash mailbox is EMAIL_TRASH_FOLDER, else the folder flagged \\Trash, else a well-known name (Trash, Deleted Items, Papierkorb).
    """
    return _move_message("email_trash_message", "trash", message_id, None, folder or "INBOX")


@mcp.tool()
def email_delete_message(message_id: str, folder: str = "INBOX") -> Dict[str, Any]:
    """Delete = move to Trash. Hard delete is disabled by policy (AIS-231); identical to email_trash_message and logged."""
    out = _move_message("email_delete_message", "trash", message_id, None, folder or "INBOX")
    if isinstance(out, dict) and out.get("success"):
        out["note"] = "Hard delete is disabled: the mail was moved to Trash instead and the action was logged."
    return out


@mcp.tool()
def email_get_audit_log(limit: int = 20, action: Optional[str] = None, since: Optional[str] = None) -> Dict[str, Any]:
    """Show the audit trail of mailbox write actions this MCP performed (send, move, trash) — logged by the tools themselves.

    Args:
        limit: Max entries, newest first (≤ 200).
        action: Optional filter: "send", "move" or "trash".
        since: Optional ISO timestamp (UTC), e.g. "2026-09-01T00:00:00Z".
    """
    entries = _audit_entries(limit=limit, action=(action or "").strip().lower() or None, since=(since or "").strip() or None)
    return {"count": len(entries), "entries": entries, "log": str(_audit_path()), "note": "Hard delete is not available; 'trash' means the Trash folder."}


if __name__ == "__main__":
    mcp.run()
