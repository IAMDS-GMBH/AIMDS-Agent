"""Tests for tools/outlook_tool.py."""

from __future__ import annotations

import json
import sys
import types

from tools import outlook_tool


def test_outlook_read_calendar_entries_requires_credentials(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "", "client_id": "", "client_secret": ""},
    )

    payload = json.loads(outlook_tool.outlook_read_calendar_entries())
    assert "error" in payload
    assert "Outlook credentials not configured" in payload["error"]


def test_format_calendar_entry_includes_expected_fields():
    entry = {
        "id": "evt-1",
        "subject": "Team Sync",
        "start": {"dateTime": "2026-06-26T10:00:00", "timeZone": "UTC"},
        "end": {"dateTime": "2026-06-26T10:30:00", "timeZone": "UTC"},
        "isAllDay": False,
        "location": {"displayName": "Room A"},
        "organizer": {
            "emailAddress": {"name": "Alice", "address": "alice@example.com"}
        },
        "webLink": "https://example.com/event",
        "bodyPreview": "Agenda",
    }

    payload = outlook_tool._format_calendar_entry(entry, include_body_preview=True)

    assert payload["id"] == "evt-1"
    assert payload["subject"] == "Team Sync"
    assert payload["start"]["date_time"] == "2026-06-26T10:00:00"
    assert payload["end"]["date_time"] == "2026-06-26T10:30:00"
    assert payload["location"] == "Room A"
    assert payload["organizer"]["email"] == "alice@example.com"
    assert payload["body_preview"] == "Agenda"


def test_enable_outlook_toolset_for_cli_appends_when_missing(monkeypatch):
    saved = {}
    config = {"platform_toolsets": {"cli": ["web"]}}

    fake_module = types.SimpleNamespace(
        load_config=lambda: config,
        save_config=lambda updated: saved.setdefault("config", updated),
    )
    monkeypatch.setitem(sys.modules, "hermes_cli.config", fake_module)

    changed, error = outlook_tool._enable_outlook_toolset_for_cli()

    assert error is None
    assert changed is True
    assert "outlook" in saved["config"]["platform_toolsets"]["cli"]


def test_auto_enable_outlook_toolset_if_token_ready_no_token(monkeypatch):
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: False)

    called = {"count": 0}

    def _should_not_run():
        called["count"] += 1
        return True, None

    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", _should_not_run)

    changed, error = outlook_tool._auto_enable_outlook_toolset_if_token_ready()

    assert changed is False
    assert error is None
    assert called["count"] == 0


def test_outlook_get_emails_auto_enables_when_token_ready(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    async def _fetch_emails_async(*args, **kwargs):
        return []

    monkeypatch.setattr(outlook_tool, "_fetch_emails_async", _fetch_emails_async)

    enable_calls = {"count": 0}

    def _enable():
        enable_calls["count"] += 1
        return True, None

    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", _enable)

    payload = json.loads(outlook_tool.outlook_get_emails())

    assert payload["count"] == 0
    assert enable_calls["count"] == 1


def test_outlook_get_emails_requires_credentials(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "", "client_id": "", "client_secret": ""},
    )

    payload = json.loads(outlook_tool.outlook_get_emails())
    assert "error" in payload
    assert "Outlook credentials not configured" in payload["error"]


def test_time_range_bounds_today_yesterday_this_week_all():
    assert outlook_tool._time_range_bounds("all") is None

    today = outlook_tool._time_range_bounds("today")
    assert today is not None
    start, end = today
    assert start.endswith("T00:00:00Z")

    yesterday = outlook_tool._time_range_bounds("yesterday")
    assert yesterday is not None

    this_week = outlook_tool._time_range_bounds("this_week")
    assert this_week is not None


def test_outlook_read_email_requires_message_id(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    payload = json.loads(outlook_tool.outlook_read_email(message_id=""))
    assert "error" in payload
    assert "message_id" in payload["error"]


def test_outlook_read_email_happy_path(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))

    async def _fetch_email_by_id_async(message_id):
        assert message_id == "msg-1"
        return {
            "id": "msg-1",
            "subject": "Hello",
            "from": {"emailAddress": {"name": "Bob", "address": "bob@example.com"}},
            "receivedDateTime": "2026-07-01T10:00:00Z",
            "isRead": True,
            "hasAttachments": False,
            "importance": "normal",
            "ccRecipients": [{"emailAddress": {"address": "cc@example.com"}}],
            "conversationId": "conv-1",
            "webLink": "https://example.com/msg-1",
            "body": {"contentType": "text", "content": "Full body text"},
        }

    monkeypatch.setattr(outlook_tool, "_fetch_email_by_id_async", _fetch_email_by_id_async)

    payload = json.loads(outlook_tool.outlook_read_email(message_id="msg-1"))

    assert payload["email"]["id"] == "msg-1"
    assert payload["email"]["body"] == "Full body text"
    assert payload["email"]["cc"] == ["cc@example.com"]
    assert payload["email"]["conversation_id"] == "conv-1"


def test_outlook_search_emails_requires_query(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    payload = json.loads(outlook_tool.outlook_search_emails(query=""))
    assert "error" in payload
    assert "query" in payload["error"]


def test_outlook_search_emails_happy_path(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))

    captured = {}

    async def _search_emails_async(query, count, search_in, folder):
        captured["args"] = (query, count, search_in, folder)
        return []

    monkeypatch.setattr(outlook_tool, "_search_emails_async", _search_emails_async)

    payload = json.loads(outlook_tool.outlook_search_emails(query="invoice", search_in="subject"))

    assert payload["count"] == 0
    assert captured["args"] == ("invoice", 10, "subject", "inbox")


def test_outlook_read_shared_mail_requires_mailbox(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    payload = json.loads(outlook_tool.outlook_read_shared_mail(mailbox=""))
    assert "error" in payload
    assert "mailbox" in payload["error"]


def test_outlook_read_shared_mail_happy_path(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))

    captured = {}

    async def _fetch_shared_mail_async(mailbox, count, folder, unread_only, include_body):
        captured["mailbox"] = mailbox
        return []

    monkeypatch.setattr(outlook_tool, "_fetch_shared_mail_async", _fetch_shared_mail_async)

    payload = json.loads(outlook_tool.outlook_read_shared_mail(mailbox="shared@example.com"))

    assert payload["mailbox"] == "shared@example.com"
    assert captured["mailbox"] == "shared@example.com"


def test_outlook_write_email_requires_recipient_or_reply(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    payload = json.loads(outlook_tool.outlook_write_email(to=""))
    assert "error" in payload


def test_outlook_write_email_requires_subject_for_new_email(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    payload = json.loads(outlook_tool.outlook_write_email(to="a@example.com", subject=""))
    assert "error" in payload
    assert "subject" in payload["error"]


def test_outlook_write_email_without_confirm_returns_preview(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))
    monkeypatch.setattr(outlook_tool, "_load_cached_signature", lambda: None)

    async def _detect_signature_from_sent_emails_async(sample_size=3):
        return None

    monkeypatch.setattr(
        outlook_tool,
        "_detect_signature_from_sent_emails_async",
        _detect_signature_from_sent_emails_async,
    )

    async def _send_new_email_async(to, subject, body, cc, bcc, reply_to_message_id):
        raise AssertionError("must not send before confirm=True")

    monkeypatch.setattr(outlook_tool, "_send_new_email_async", _send_new_email_async)

    payload = json.loads(
        outlook_tool.outlook_write_email(
            to="a@example.com,b@example.com", subject="Hi", body="Body text"
        )
    )

    assert payload["status"] == "confirmation_required"
    assert payload["preview"]["to"] == ["a@example.com", "b@example.com"]
    assert payload["preview"]["subject"] == "Hi"
    # Must steer the model toward the clarify tool (real buttons/choices)
    # instead of free-text "OK" confirmation.
    assert "clarify" in payload["message"].lower()
    assert "user_signature" not in payload


def test_outlook_write_email_uses_cached_signature(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))
    monkeypatch.setattr(outlook_tool, "_load_cached_signature", lambda: "Best,\nJohannes")

    async def _detect_signature_from_sent_emails_async(sample_size=3):
        raise AssertionError("must not re-detect when a cache hit exists")

    monkeypatch.setattr(
        outlook_tool,
        "_detect_signature_from_sent_emails_async",
        _detect_signature_from_sent_emails_async,
    )

    payload = json.loads(
        outlook_tool.outlook_write_email(
            to="a@example.com", subject="Hi", body="Body text without a signature"
        )
    )

    assert payload["status"] == "confirmation_required"
    assert payload["user_signature"] == "Best,\nJohannes"
    assert payload["signature_source"] == "cache"


def test_outlook_write_email_skips_signature_lookup_for_replies(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))

    def _load_cached_signature():
        raise AssertionError("replies must not trigger signature lookup")

    monkeypatch.setattr(outlook_tool, "_load_cached_signature", _load_cached_signature)

    payload = json.loads(
        outlook_tool.outlook_write_email(
            to="", subject="", body="Reply body", reply_to_message_id="msg-1"
        )
    )

    assert payload["status"] == "confirmation_required"
    assert "user_signature" not in payload


def test_extract_signature_candidate_cuts_at_quote_marker():
    body = (
        "Hi Gonzalo,\n\nDas klingt gut.\n\nBeste Grüße,\nJohannes\n\n"
        "From: Someone Else\nSent: yesterday\nSubject: RE: thread\n\nOld message body"
    )
    candidate = outlook_tool._extract_signature_candidate(body)
    assert candidate is not None
    assert "Johannes" in candidate
    assert "Old message body" not in candidate


def test_outlook_write_email_happy_path_sends_when_confirmed(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))

    captured = {}

    async def _send_new_email_async(to, subject, body, cc, bcc, reply_to_message_id):
        captured["args"] = (to, subject, body, cc, bcc, reply_to_message_id)
        return {"verified": True, "sent_item_id": "msg-123"}

    monkeypatch.setattr(outlook_tool, "_send_new_email_async", _send_new_email_async)

    payload = json.loads(
        outlook_tool.outlook_write_email(
            to="a@example.com,b@example.com", subject="Hi", body="Body text", confirm=True
        )
    )

    assert payload["status"] == "sent"
    assert payload["sent_item_id"] == "msg-123"
    assert captured["args"][0] == ["a@example.com", "b@example.com"]
    assert captured["args"][1] == "Hi"


def test_outlook_write_email_unverified_send_returns_warning(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))

    async def _send_new_email_async(to, subject, body, cc, bcc, reply_to_message_id):
        return {"verified": False}

    monkeypatch.setattr(outlook_tool, "_send_new_email_async", _send_new_email_async)

    payload = json.loads(
        outlook_tool.outlook_write_email(
            to="a@example.com", subject="Hi", body="Body text", confirm=True
        )
    )

    assert payload["status"] == "sent_unverified"
    assert "warning" in payload


def test_outlook_authenticate_returns_auth_required_when_no_token(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: False)
    monkeypatch.setattr(
        outlook_tool, "_auto_enable_outlook_toolset_if_token_ready", lambda: (False, None)
    )
    monkeypatch.setattr(
        outlook_tool,
        "_start_interactive_auth",
        lambda scope, label, note="": {
            "status": "auth_required",
            "message": "sign in please",
            "flow": "loopback",
        },
    )

    payload = json.loads(outlook_tool.outlook_authenticate())

    assert payload["status"] == "auth_required"


def test_outlook_authenticate_confirms_already_signed_in(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(
        outlook_tool, "_auto_enable_outlook_toolset_if_token_ready", lambda: (False, None)
    )

    async def _get_me_async():
        return {"displayName": "Jane Doe", "mail": "jane@example.com"}

    monkeypatch.setattr(outlook_tool, "_get_me_async", _get_me_async)

    payload = json.loads(outlook_tool.outlook_authenticate())

    assert payload["ok"] is True
    assert payload["status"] == "authenticated"
    assert "Jane Doe" in payload["message"]


def test_outlook_authenticate_resumes_pending_loopback_session(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )

    async def _poll_loopback_auth(request_id):
        return {
            "status": "success",
            "access_token": "tok",
            "refresh_token": "refresh",
            "expires_in": 3600,
            "token_type": "Bearer",
        }

    monkeypatch.setitem(
        sys.modules,
        "tools.microsoft_graph_auth",
        types.SimpleNamespace(poll_loopback_auth=_poll_loopback_auth),
    )
    monkeypatch.setattr(outlook_tool, "_save_outlook_token_cache", lambda *a, **kw: None)
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(
        outlook_tool, "_auto_enable_outlook_toolset_if_token_ready", lambda: (True, None)
    )

    async def _get_me_async():
        return {"displayName": "Jane Doe"}

    monkeypatch.setattr(outlook_tool, "_get_me_async", _get_me_async)

    payload = json.loads(outlook_tool.outlook_authenticate(device_code="lb:req123"))

    assert payload["ok"] is True
    assert payload["status"] == "authenticated"


def test_auth_guard_no_cached_token_starts_interactive_auth(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: False)
    monkeypatch.setattr(
        outlook_tool, "_auto_enable_outlook_toolset_if_token_ready", lambda: (False, None)
    )
    monkeypatch.setattr(
        outlook_tool,
        "_start_interactive_auth",
        lambda scope, label, note="": {"status": "auth_required", "message": "sign in", "flow": "loopback"},
    )

    early, toolset_enabled = outlook_tool._outlook_auth_guard("", label="Outlook")

    assert early == {"status": "auth_required", "message": "sign in", "flow": "loopback"}
    assert toolset_enabled is False


def test_auth_guard_cached_token_skips_interactive_auth(monkeypatch):
    """Persistent sign-in: a valid cached token must never trigger a fresh
    interactive flow, for either loopback or device-code call sites."""
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(
        outlook_tool, "_auto_enable_outlook_toolset_if_token_ready", lambda: (False, None)
    )

    def _boom(*a, **kw):
        raise AssertionError("must not start a new interactive auth when a token is cached")

    monkeypatch.setattr(outlook_tool, "_start_interactive_auth", _boom)

    early, toolset_enabled = outlook_tool._outlook_auth_guard("", label="Outlook")

    assert early is None
    assert toolset_enabled is False


def test_auth_guard_loopback_resume_success_saves_token(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(
        outlook_tool, "_auto_enable_outlook_toolset_if_token_ready", lambda: (True, None)
    )

    saved = {}
    monkeypatch.setattr(
        outlook_tool,
        "_save_outlook_token_cache",
        lambda access_token, refresh_token, expires_in, token_type="Bearer": saved.update(
            access_token=access_token, refresh_token=refresh_token
        ),
    )

    async def _poll_loopback_auth(request_id):
        assert request_id == "abc123"
        return {
            "status": "success",
            "access_token": "at",
            "refresh_token": "rt",
            "expires_in": 3600,
            "token_type": "Bearer",
        }

    fake_module = types.SimpleNamespace(poll_loopback_auth=_poll_loopback_auth)
    monkeypatch.setitem(sys.modules, "tools.microsoft_graph_auth", fake_module)

    early, toolset_enabled = outlook_tool._outlook_auth_guard("lb:abc123", label="Outlook")

    assert early is None
    assert toolset_enabled is True
    assert saved == {"access_token": "at", "refresh_token": "rt"}


def test_auth_guard_loopback_resume_pending(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)

    async def _poll_loopback_auth(request_id):
        return {"status": "pending"}

    fake_module = types.SimpleNamespace(poll_loopback_auth=_poll_loopback_auth)
    monkeypatch.setitem(sys.modules, "tools.microsoft_graph_auth", fake_module)

    early, toolset_enabled = outlook_tool._outlook_auth_guard("lb:abc123", label="Outlook")

    assert early["status"] == "pending"
    assert toolset_enabled is False


def test_auth_guard_loopback_resume_expired_restarts_auth(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)

    async def _poll_loopback_auth(request_id):
        return {"status": "expired"}

    fake_module = types.SimpleNamespace(poll_loopback_auth=_poll_loopback_auth)
    monkeypatch.setitem(sys.modules, "tools.microsoft_graph_auth", fake_module)

    restart_calls = []
    monkeypatch.setattr(
        outlook_tool,
        "_start_interactive_auth",
        lambda scope, label, note="": restart_calls.append(note)
        or {"status": "auth_required", "message": "restarted"},
    )

    early, toolset_enabled = outlook_tool._outlook_auth_guard("lb:abc123", label="Outlook")

    assert early == {"status": "auth_required", "message": "restarted"}
    assert toolset_enabled is False
    assert len(restart_calls) == 1
    assert "no longer valid" in restart_calls[0]


def test_auth_guard_device_code_aadsts_error_restarts_auth_instead_of_raw_error(monkeypatch):
    """Regression test for AADSTS7000014: an invalid/expired device code must
    trigger an automatic fresh sign-in instead of dead-ending on a raw error
    the model cannot act on."""
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)

    async def _poll_device_code_async(device_code, scope=None):
        raise RuntimeError(
            "Token poll failed: AADSTS7000014: The provided value for the input "
            "parameter 'device_code' is not valid."
        )

    monkeypatch.setattr(outlook_tool, "_poll_device_code_async", _poll_device_code_async)

    restart_calls = []
    monkeypatch.setattr(
        outlook_tool,
        "_start_interactive_auth",
        lambda scope, label, note="": restart_calls.append(note)
        or {"status": "auth_required", "message": "restarted", "flow": "device_code"},
    )

    early, toolset_enabled = outlook_tool._outlook_auth_guard("stale-device-code", label="Outlook")

    assert early == {"status": "auth_required", "message": "restarted", "flow": "device_code"}
    assert toolset_enabled is False
    assert len(restart_calls) == 1
    assert "no longer valid" in restart_calls[0]
    assert "AADSTS7000014" in restart_calls[0]


def test_start_interactive_auth_message_forbids_manual_curl(monkeypatch):
    monkeypatch.setattr(outlook_tool, "outlook_interactive_auth_flow", lambda: "device_code")
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )

    async def _start_device_code_async(scope=None):
        return {
            "verification_uri": "https://microsoft.com/devicelogin",
            "user_code": "ABC123",
            "device_code": "raw-device-code",
            "expires_in_seconds": 900,
        }

    monkeypatch.setattr(outlook_tool, "_start_device_code_async", _start_device_code_async)

    response = outlook_tool._start_interactive_auth(None, "Outlook")

    assert response["status"] == "auth_required"
    assert response["device_code"] == "raw-device-code"
    assert response["flow"] == "device_code"
    assert "curl" in response["message"] or "HTTP" in response["message"]
    assert "never construct" in response["message"].lower()


def test_start_interactive_auth_loopback_bind_failure_returns_error_no_device_code_fallback(monkeypatch):
    monkeypatch.setattr(outlook_tool, "outlook_interactive_auth_flow", lambda: "auto")
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )

    def _start_loopback_auth(*a, **kw):
        raise OSError("could not bind local listener")

    fake_module = types.SimpleNamespace(
        start_loopback_auth=_start_loopback_auth, DEFAULT_DELEGATED_SCOPE="Mail.Read Mail.Send"
    )
    monkeypatch.setitem(sys.modules, "tools.microsoft_graph_auth", fake_module)

    # Device code must NOT be started automatically — it's legacy and only
    # used when explicitly selected in settings.
    def _start_device_code_async(scope=None):
        raise AssertionError("must not silently fall back to device code")

    monkeypatch.setattr(outlook_tool, "_start_device_code_async", _start_device_code_async)

    response = outlook_tool._start_interactive_auth(None, "Outlook")

    assert "error" in response
    assert "could not bind local listener" in response["error"]
    assert "device code" in response["error"].lower()


def test_start_interactive_auth_device_code_explicit_mode_used_directly(monkeypatch):
    monkeypatch.setattr(outlook_tool, "outlook_interactive_auth_flow", lambda: "device_code")
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )

    async def _start_device_code_async(scope=None):
        return {
            "verification_uri": "https://microsoft.com/devicelogin",
            "user_code": "ABC123",
            "device_code": "raw-device-code",
            "expires_in_seconds": 900,
        }

    monkeypatch.setattr(outlook_tool, "_start_device_code_async", _start_device_code_async)

    response = outlook_tool._start_interactive_auth(None, "Outlook")

    assert response["status"] == "auth_required"
    assert response["flow"] == "device_code"


def test_outlook_write_calendar_entries_invalid_action(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    payload = json.loads(outlook_tool.outlook_write_calendar_entries(action="bogus"))
    assert "error" in payload


def test_outlook_write_calendar_entries_create_requires_fields(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    payload = json.loads(outlook_tool.outlook_write_calendar_entries(action="create"))
    assert "error" in payload


def test_outlook_write_calendar_entries_update_requires_event_id(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    payload = json.loads(outlook_tool.outlook_write_calendar_entries(action="update"))
    assert "error" in payload


def test_outlook_write_calendar_entries_update_without_confirm_returns_preview(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))

    previous_entry = {
        "id": "evt-1",
        "subject": "Old Subject",
        "start": {"dateTime": "2026-07-01T10:00:00", "timeZone": "UTC"},
        "end": {"dateTime": "2026-07-01T10:30:00", "timeZone": "UTC"},
        "isAllDay": False,
        "location": {"displayName": "Room A"},
        "organizer": {"emailAddress": {"name": "Alice", "address": "alice@example.com"}},
        "webLink": "https://example.com/evt-1",
    }

    async def _get_calendar_entry_async(event_id):
        assert event_id == "evt-1"
        return previous_entry

    apply_calls = {"count": 0}

    async def _update_calendar_entry_async(event_id, event_body):
        apply_calls["count"] += 1
        return previous_entry

    monkeypatch.setattr(outlook_tool, "_get_calendar_entry_async", _get_calendar_entry_async)
    monkeypatch.setattr(outlook_tool, "_update_calendar_entry_async", _update_calendar_entry_async)

    payload = json.loads(
        outlook_tool.outlook_write_calendar_entries(
            action="update", event_id="evt-1", subject="New Subject", confirm=False,
        )
    )

    assert payload["status"] == "confirmation_required"
    assert payload["previous_state"]["subject"] == "Old Subject"
    assert apply_calls["count"] == 0


def test_outlook_write_calendar_entries_update_with_confirm_applies(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))

    previous_entry = {
        "id": "evt-1",
        "subject": "Old Subject",
        "start": {"dateTime": "2026-07-01T10:00:00", "timeZone": "UTC"},
        "end": {"dateTime": "2026-07-01T10:30:00", "timeZone": "UTC"},
        "isAllDay": False,
    }
    updated_entry = dict(previous_entry, subject="New Subject")

    async def _get_calendar_entry_async(event_id):
        return previous_entry

    async def _update_calendar_entry_async(event_id, event_body):
        assert event_body["subject"] == "New Subject"
        return updated_entry

    monkeypatch.setattr(outlook_tool, "_get_calendar_entry_async", _get_calendar_entry_async)
    monkeypatch.setattr(outlook_tool, "_update_calendar_entry_async", _update_calendar_entry_async)

    payload = json.loads(
        outlook_tool.outlook_write_calendar_entries(
            action="update", event_id="evt-1", subject="New Subject", confirm=True,
        )
    )

    assert payload["status"] == "updated"
    assert payload["previous_state"]["subject"] == "Old Subject"
    assert payload["entry"]["subject"] == "New Subject"


def test_outlook_write_calendar_entries_delete_without_confirm_returns_preview(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))

    previous_entry = {"id": "evt-1", "subject": "To Delete"}

    async def _get_calendar_entry_async(event_id):
        return previous_entry

    delete_calls = {"count": 0}

    async def _delete_calendar_entry_async(event_id):
        delete_calls["count"] += 1
        return {"deleted": True}

    monkeypatch.setattr(outlook_tool, "_get_calendar_entry_async", _get_calendar_entry_async)
    monkeypatch.setattr(outlook_tool, "_delete_calendar_entry_async", _delete_calendar_entry_async)

    payload = json.loads(
        outlook_tool.outlook_write_calendar_entries(action="delete", event_id="evt-1", confirm=False)
    )

    assert payload["status"] == "confirmation_required"
    assert payload["previous_state"]["subject"] == "To Delete"
    assert delete_calls["count"] == 0


def test_outlook_write_calendar_entries_create_happy_path(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))

    created_entry = {
        "id": "evt-new",
        "subject": "Kickoff",
        "start": {"dateTime": "2026-07-10T14:00:00", "timeZone": "UTC"},
        "end": {"dateTime": "2026-07-10T15:00:00", "timeZone": "UTC"},
        "isAllDay": False,
    }

    async def _create_calendar_entry_async(event_body):
        assert event_body["subject"] == "Kickoff"
        return created_entry

    monkeypatch.setattr(outlook_tool, "_create_calendar_entry_async", _create_calendar_entry_async)

    payload = json.loads(
        outlook_tool.outlook_write_calendar_entries(
            action="create",
            subject="Kickoff",
            start_datetime="2026-07-10T14:00:00",
            end_datetime="2026-07-10T15:00:00",
        )
    )

    assert payload["status"] == "created"
    assert payload["entry"]["subject"] == "Kickoff"


def test_outlook_read_contacts_requires_credentials(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "", "client_id": "", "client_secret": ""},
    )
    payload = json.loads(outlook_tool.outlook_read_contacts())
    assert "error" in payload


def test_outlook_read_contacts_happy_path(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))

    raw_contacts = [
        {
            "id": "c-1",
            "displayName": "Jane Doe",
            "givenName": "Jane",
            "surname": "Doe",
            "companyName": "IAMDS",
            "jobTitle": "Engineer",
            "emailAddresses": [{"address": "jane@example.com"}],
            "businessPhones": ["+49 1 2345"],
            "mobilePhone": "",
            "personalNotes": "",
        }
    ]

    async def _fetch_contacts_async(count, search):
        assert count == 20
        assert search == ""
        return raw_contacts

    monkeypatch.setattr(outlook_tool, "_fetch_contacts_async", _fetch_contacts_async)

    payload = json.loads(outlook_tool.outlook_read_contacts())

    assert payload["count"] == 1
    assert payload["contacts"][0]["display_name"] == "Jane Doe"
    assert payload["contacts"][0]["emails"] == ["jane@example.com"]


def test_outlook_write_contacts_invalid_action(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    payload = json.loads(outlook_tool.outlook_write_contacts(action="bogus"))
    assert "error" in payload


def test_outlook_write_contacts_create_requires_name(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    payload = json.loads(outlook_tool.outlook_write_contacts(action="create"))
    assert "error" in payload


def test_outlook_write_contacts_update_requires_contact_id(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    payload = json.loads(outlook_tool.outlook_write_contacts(action="update"))
    assert "error" in payload


def test_outlook_write_contacts_update_without_confirm_returns_preview(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))

    previous_contact = {
        "id": "c-1",
        "displayName": "Old Name",
        "emailAddresses": [{"address": "old@example.com"}],
    }

    async def _get_contact_async(contact_id):
        assert contact_id == "c-1"
        return previous_contact

    apply_calls = {"count": 0}

    async def _update_contact_async(contact_id, contact_body):
        apply_calls["count"] += 1
        return previous_contact

    monkeypatch.setattr(outlook_tool, "_get_contact_async", _get_contact_async)
    monkeypatch.setattr(outlook_tool, "_update_contact_async", _update_contact_async)

    payload = json.loads(
        outlook_tool.outlook_write_contacts(
            action="update", contact_id="c-1", display_name="New Name", confirm=False,
        )
    )

    assert payload["status"] == "confirmation_required"
    assert payload["previous_state"]["display_name"] == "Old Name"
    assert apply_calls["count"] == 0


def test_outlook_write_contacts_update_with_confirm_applies(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))

    previous_contact = {"id": "c-1", "displayName": "Old Name"}
    updated_contact = {"id": "c-1", "displayName": "New Name"}

    async def _get_contact_async(contact_id):
        return previous_contact

    async def _update_contact_async(contact_id, contact_body):
        assert contact_body["displayName"] == "New Name"
        return updated_contact

    monkeypatch.setattr(outlook_tool, "_get_contact_async", _get_contact_async)
    monkeypatch.setattr(outlook_tool, "_update_contact_async", _update_contact_async)

    payload = json.loads(
        outlook_tool.outlook_write_contacts(
            action="update", contact_id="c-1", display_name="New Name", confirm=True,
        )
    )

    assert payload["status"] == "updated"
    assert payload["previous_state"]["display_name"] == "Old Name"
    assert payload["contact"]["display_name"] == "New Name"


def test_outlook_write_contacts_delete_without_confirm_returns_preview(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))

    previous_contact = {"id": "c-1", "displayName": "To Delete"}

    async def _get_contact_async(contact_id):
        return previous_contact

    delete_calls = {"count": 0}

    async def _delete_contact_async(contact_id):
        delete_calls["count"] += 1
        return {"deleted": True}

    monkeypatch.setattr(outlook_tool, "_get_contact_async", _get_contact_async)
    monkeypatch.setattr(outlook_tool, "_delete_contact_async", _delete_contact_async)

    payload = json.loads(
        outlook_tool.outlook_write_contacts(action="delete", contact_id="c-1", confirm=False)
    )

    assert payload["status"] == "confirmation_required"
    assert payload["previous_state"]["display_name"] == "To Delete"
    assert delete_calls["count"] == 0


def test_outlook_write_contacts_create_happy_path(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))

    created_contact = {"id": "c-new", "displayName": "Gonzalo Oberreuter"}

    async def _create_contact_async(contact_body):
        assert contact_body["displayName"] == "Gonzalo Oberreuter"
        return created_contact

    monkeypatch.setattr(outlook_tool, "_create_contact_async", _create_contact_async)

    payload = json.loads(
        outlook_tool.outlook_write_contacts(action="create", display_name="Gonzalo Oberreuter")
    )

    assert payload["status"] == "created"
    assert payload["contact"]["display_name"] == "Gonzalo Oberreuter"
