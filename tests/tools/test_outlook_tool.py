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


def test_outlook_write_email_happy_path_sends(monkeypatch):
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

    monkeypatch.setattr(outlook_tool, "_send_new_email_async", _send_new_email_async)

    payload = json.loads(
        outlook_tool.outlook_write_email(
            to="a@example.com,b@example.com", subject="Hi", body="Body text"
        )
    )

    assert payload["status"] == "sent"
    assert captured["args"][0] == ["a@example.com", "b@example.com"]
    assert captured["args"][1] == "Hi"


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
