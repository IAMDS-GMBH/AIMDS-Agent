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


def test_outlook_search_emails_requires_at_least_one_filter(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    payload = json.loads(outlook_tool.outlook_search_emails(query=""))
    assert "error" in payload


def test_outlook_search_emails_happy_path(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))

    captured = {}

    async def _search_emails_async(query, count, search_in, folder, sender="", recipient="",
                                    date_from="", date_to=""):
        captured["args"] = (query, count, search_in, folder, sender, recipient, date_from, date_to)
        return []

    monkeypatch.setattr(outlook_tool, "_search_emails_async", _search_emails_async)

    payload = json.loads(outlook_tool.outlook_search_emails(query="invoice", search_in="subject"))

    assert payload["count"] == 0
    assert captured["args"] == ("invoice", 10, "subject", "inbox", "", "", "", "")


def test_outlook_search_emails_structured_filters_without_query(monkeypatch):
    """A search with only sender/recipient/date filters (no keyword) must be
    accepted — this is the exact scenario that used to be forced into
    unparseable free text and always returned zero results."""
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))

    captured = {}

    async def _search_emails_async(query, count, search_in, folder, sender="", recipient="",
                                    date_from="", date_to=""):
        captured["args"] = (query, count, search_in, folder, sender, recipient, date_from, date_to)
        return []

    monkeypatch.setattr(outlook_tool, "_search_emails_async", _search_emails_async)

    payload = json.loads(
        outlook_tool.outlook_search_emails(
            recipient="arnim.schmidt@f1rst.ch", date_from="2026-05-30", date_to="2026-06-05",
        )
    )

    assert "error" not in payload
    assert captured["args"] == (
        "", 10, "both", "inbox", "", "arnim.schmidt@f1rst.ch", "2026-05-30", "2026-06-05",
    )


def test_build_mail_search_kql_combines_sender_recipient_and_free_text():
    expr = outlook_tool._build_mail_search_kql(
        query="Angebot",
        search_in="both",
        sender="alice@contoso.com",
        recipient="bob@contoso.com",
    )
    assert 'from:"alice@contoso.com"' in expr
    assert 'to:"bob@contoso.com"' in expr
    assert '"Angebot"' in expr
    # Graph's $search does not support date-range KQL for messages —
    # dates must never leak into this expression.
    assert "received" not in expr


def test_build_received_date_filter_both_bounds():
    expr = outlook_tool._build_received_date_filter("2026-05-30", "2026-06-05")
    assert expr == (
        "receivedDateTime ge 2026-05-30T00:00:00Z and "
        "receivedDateTime le 2026-06-05T23:59:59Z"
    )


def test_build_received_date_filter_from_only():
    expr = outlook_tool._build_received_date_filter("2026-05-30", "")
    assert expr == "receivedDateTime ge 2026-05-30T00:00:00Z"


def test_build_received_date_filter_to_only():
    expr = outlook_tool._build_received_date_filter("", "2026-06-05")
    assert expr == "receivedDateTime le 2026-06-05T23:59:59Z"


def test_search_emails_async_search_expr_wrapped_in_outer_quotes(monkeypatch):
    """Regression guard for a real production bug: Graph's $search requires
    the ENTIRE KQL expression to be wrapped in one overall pair of double
    quotes (with inner quotes escaped) as part of the parameter value itself
    — not just the individual from:/subject: phrases. Sending
    'from:"x" "keyword"' unquoted caused a live 400 'character \":\" is not
    valid' syntax error as soon as more than one term was combined."""
    captured = {}

    class _FakeClient:
        async def get_json(self, path, params=None, headers=None):
            captured["params"] = params
            captured["headers"] = headers
            return {"value": []}

    monkeypatch.setattr(outlook_tool, "_new_graph_client", lambda: (_FakeClient(), None))

    import asyncio
    asyncio.run(
        outlook_tool._search_emails_async(
            query="keyword", count=10, search_in="both", folder="inbox",
            sender="arnim.schmidt@f1rst.ch",
        )
    )

    expected_expr = outlook_tool._build_mail_search_kql(
        "keyword", "both", "arnim.schmidt@f1rst.ch", "",
    )
    expected_search_param = '"' + expected_expr.replace('"', '\\"') + '"'
    assert captured["params"]["$search"] == expected_search_param
    assert captured["params"]["$count"] == "true"
    assert captured["headers"]["ConsistencyLevel"] == "eventual"


def test_search_emails_async_date_range_uses_filter_not_search(monkeypatch):
    """Regression guard: date_from/date_to must go through $filter, never
    into the $search KQL string, since Graph rejects received:/received>=
    KQL for messages with a 400 syntax error."""
    captured = {}

    class _FakeClient:
        async def get_json(self, path, params=None, headers=None):
            captured["params"] = params
            return {"value": []}

    monkeypatch.setattr(outlook_tool, "_new_graph_client", lambda: (_FakeClient(), None))

    import asyncio
    asyncio.run(
        outlook_tool._search_emails_async(
            query="", count=10, search_in="both", folder="inbox",
            sender="arnim.schmidt@f1rst.ch", date_from="2026-06-01", date_to="2026-06-03",
        )
    )

    params = captured["params"]
    assert params["$filter"] == (
        "receivedDateTime ge 2026-06-01T00:00:00Z and receivedDateTime le 2026-06-03T23:59:59Z"
    )
    expected_expr = outlook_tool._build_mail_search_kql("", "both", "arnim.schmidt@f1rst.ch", "")
    expected_search_param = '"' + expected_expr.replace('"', '\\"') + '"'
    assert params["$search"] == expected_search_param
    assert "received" not in expected_expr  # no received: KQL ever built into $search


def test_search_emails_async_pure_date_filter_no_search_param(monkeypatch):
    """When only date filters are given (no query/sender/recipient), no
    $search parameter should be sent at all — just a plain $filter, exactly
    like the already-working outlook_get_emails time_range path."""
    captured = {}

    class _FakeClient:
        async def get_json(self, path, params=None, headers=None):
            captured["params"] = params
            return {"value": []}

    monkeypatch.setattr(outlook_tool, "_new_graph_client", lambda: (_FakeClient(), None))

    import asyncio
    asyncio.run(
        outlook_tool._search_emails_async(
            query="", count=10, search_in="both", folder="inbox",
            date_from="2026-06-01", date_to="2026-06-03",
        )
    )

    assert "$search" not in captured["params"]
    assert "$count" not in captured["params"]
    assert captured["params"]["$filter"] == (
        "receivedDateTime ge 2026-06-01T00:00:00Z and receivedDateTime le 2026-06-03T23:59:59Z"
    )


def test_search_emails_async_folder_all_uses_whole_mailbox(monkeypatch):
    """folder='all' must query /me/messages (whole mailbox), not silently
    fall back to inbox while still reporting 'all' back to the caller."""
    captured = {}

    class _FakeClient:
        async def get_json(self, path, params=None, headers=None):
            captured["path"] = path
            captured["params"] = params
            return {"value": []}

    monkeypatch.setattr(outlook_tool, "_new_graph_client", lambda: (_FakeClient(), None))

    import asyncio
    asyncio.run(
        outlook_tool._search_emails_async(
            query="", count=10, search_in="both", folder="all", recipient="bob@contoso.com",
        )
    )

    assert captured["path"] == "/me/messages"


def test_search_emails_async_folder_junk_maps_to_junkemail(monkeypatch):
    captured = {}

    class _FakeClient:
        async def get_json(self, path, params=None, headers=None):
            captured["path"] = path
            return {"value": []}

    monkeypatch.setattr(outlook_tool, "_new_graph_client", lambda: (_FakeClient(), None))

    import asyncio
    asyncio.run(
        outlook_tool._search_emails_async(
            query="invoice", count=10, search_in="both", folder="junk",
        )
    )

    assert captured["path"] == "/me/mailFolders/junkemail/messages"



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
    # A draft_id must be issued so the confirm=true call can reference the
    # cached draft instead of repeating the full body as tool-call JSON.
    assert payload["draft_id"]
    assert "draft_id" in payload["message"]


def test_outlook_write_email_confirm_with_draft_id_skips_resending_fields(monkeypatch):
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

    sent_args = {}

    async def _send_new_email_async(to, subject, body, cc, bcc, reply_to_message_id):
        sent_args["call"] = (to, subject, body, cc, bcc, reply_to_message_id)
        return {"verified": True}

    monkeypatch.setattr(outlook_tool, "_send_new_email_async", _send_new_email_async)

    preview = json.loads(
        outlook_tool.outlook_write_email(
            to="a@example.com", subject="Hi", body="Body text"
        )
    )
    draft_id = preview["draft_id"]

    # Confirm using only draft_id — no to/subject/body repeated at all.
    result = json.loads(outlook_tool.outlook_write_email(confirm=True, draft_id=draft_id))

    assert "error" not in result
    assert sent_args["call"] == (["a@example.com"], "Hi", "Body text", [], [], "")

    # A draft_id can only be used once.
    replay = json.loads(outlook_tool.outlook_write_email(confirm=True, draft_id=draft_id))
    assert "error" in replay
    assert "draft_id" in replay["error"]


def test_outlook_write_email_confirm_with_unknown_draft_id_errors(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))

    result = json.loads(
        outlook_tool.outlook_write_email(confirm=True, draft_id="does-not-exist")
    )
    assert "error" in result
    assert "does-not-exist" in result["error"]


def test_outlook_write_email_registry_schema_exposes_draft_id():
    """Regression guard: draft_id is a real function parameter used by the
    confirm-flow, but was previously missing from both the tool schema and
    the handler lambda — meaning the model could never actually pass it
    back, silently breaking the whole draft_id-based confirm contract."""
    entry = outlook_tool.registry.get_entry("outlook_write_email")
    assert entry is not None
    assert "draft_id" in entry.schema["parameters"]["properties"]

    captured = {}
    monkeypatch_handler = entry.handler
    original_fn = outlook_tool.outlook_write_email
    try:
        def _fake(*args, **kwargs):
            captured.update(kwargs)
            return "{}"
        outlook_tool.outlook_write_email = _fake
        monkeypatch_handler({"confirm": True, "draft_id": "abc-123"})
    finally:
        outlook_tool.outlook_write_email = original_fn

    assert captured.get("draft_id") == "abc-123"


def test_outlook_write_email_confirm_with_nothing_gives_actionable_error(monkeypatch):
    """Regression test: confirm=true with no draft_id and no to/subject/body
    previously fell through to the generic 'to is required' validation
    error, which caused the model to retry blindly several times instead of
    resending the draft_id. It must now get a specific, actionable error."""
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))

    result = json.loads(outlook_tool.outlook_write_email(confirm=True))
    assert "error" in result
    assert "draft_id" in result["error"]


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
    # Only the sign-off line + what follows should be captured, not the
    # preceding body prose.
    assert "Das klingt gut" not in candidate


def test_extract_signature_candidate_requires_closing_marker():
    # Regression test: a long closing *paragraph* without a recognizable
    # sign-off phrase must NOT be mistaken for a signature — this was the
    # exact bug that cached an unrelated email's body prose ("...Probleme
    # mit der Signatur behoben...") as if it were a real signature.
    body = (
        "Hallo,\n\nwie besprochen habe ich die Probleme mit der Signatur behoben. "
        "Die Änderungen sollten jetzt korrekt angezeigt werden. Falls Sie weitere "
        "Anpassungen benötigen oder weitere Fragen haben, stehe ich gerne zur "
        "Verfügung.\n\nVielen Dank erneut für Ihre Unterstützung!"
    )
    assert outlook_tool._extract_signature_candidate(body) is None


def test_extract_signature_candidate_captures_real_closing_line():
    body = (
        "Hallo,\n\nwie besprochen habe ich die Probleme behoben.\n\n"
        "Vielen Dank erneut für Ihre Unterstützung!\n\n"
        "Mit freundlichen Grüßen,\nJohannes Huchler"
    )
    candidate = outlook_tool._extract_signature_candidate(body)
    assert candidate == "Mit freundlichen Grüßen,\nJohannes Huchler"


def test_extract_signature_candidate_detects_bare_gruesse_signoff():
    """Regression test for a real-world sample: the user's actual sent
    emails close with a bare 'Grüße' (no qualifier like 'Viele'/'Beste'),
    which the phrase-only marker list never matched, so this user's real
    signature could never be auto-detected from the sent folder."""
    body = (
        "Hallo,\n\nkönnen wir die Domains dort bereits einbinden?\n\n"
        "Grüße\nJohannes Huchler\nSenior Developer\nIAMDS GmbH\n"
        "Heininger Str. 6, 94036 Passau\nHRB: 10734 Amtsgericht Passau"
    )
    candidate = outlook_tool._extract_signature_candidate(body)
    assert candidate is not None
    assert candidate.startswith("Grüße")
    assert "Johannes Huchler" in candidate
    assert "IAMDS GmbH" in candidate
    assert "können wir die Domains" not in candidate


def test_extract_signature_candidate_bare_gruss_singular_signoff():
    body = "Hallo,\n\nDanke dir.\n\nGruß,\nJohannes"
    candidate = outlook_tool._extract_signature_candidate(body)
    assert candidate == "Gruß,\nJohannes"


def test_extract_signature_candidate_does_not_false_match_begruessen():
    """'begrüßen'/'begrüßenswert' contain 'grüße' as a raw substring but must
    NOT be mistaken for the bare 'Grüße' sign-off marker (word-boundary
    matching only)."""
    body = (
        "Hallo,\n\nwir würden Sie gerne im Team begrüßen und freuen uns auf die "
        "Zusammenarbeit. Das wäre sehr begrüßenswert für alle Beteiligten."
    )
    assert outlook_tool._extract_signature_candidate(body) is None


def test_extract_signature_html_block_finds_outlook_mobile_marker():
    """Verified against a real Outlook (OWA/mobile) sent email: the actual
    client wraps the user's configured signature in
    <div id="ms-outlook-mobile-signature">...</div> — a structural marker
    that should be preferred over the closing-phrase heuristic whenever
    present, since it's exact rather than guessed."""
    html = (
        '<div>Hi Bob,</div><div>can we sync tomorrow?</div><br>'
        '<div id="ms-outlook-mobile-signature" style="color: inherit;">'
        '<table><tbody><tr><td>'
        '<div>Max Mustermann <span>Senior Developer</span></div>'
        '</td></tr><tr><td>'
        '<div>Example GmbH, Musterstr. 1, 12345 Musterstadt</div>'
        '</td></tr></tbody></table>'
        '</div>'
    )
    candidate = outlook_tool._extract_signature_html_block(html)
    assert candidate is not None
    assert "Max Mustermann" in candidate
    assert "Senior Developer" in candidate
    assert "Example GmbH" in candidate
    assert "can we sync tomorrow" not in candidate  # body text excluded, only the marker div


def test_extract_signature_html_block_balances_nested_divs():
    """The signature container itself has nested <div> children (e.g. a
    logo cell and a details cell) — the extractor must scan to the correctly
    MATCHING closing </div>, not the first one it sees."""
    html = (
        '<div id="Signature">'
        '<div class="outer"><div class="inner">Jane Doe</div></div>'
        '<div>Acme Inc.</div>'
        '</div>'
        '<div>Unrelated trailing content that must not be included</div>'
    )
    candidate = outlook_tool._extract_signature_html_block(html)
    assert candidate is not None
    assert "Jane Doe" in candidate
    assert "Acme Inc." in candidate
    assert "Unrelated trailing content" not in candidate


def test_extract_signature_html_block_returns_none_without_known_marker():
    html = "<div>Hi,</div><div>Viele Grüße, Someone</div>"
    assert outlook_tool._extract_signature_html_block(html) is None


def test_detect_signature_from_sent_emails_prefers_html_marker(monkeypatch):
    """When a sent email's HTML body contains a recognized signature
    container marker, that structural extraction should be used instead of
    falling back to the closing-phrase heuristic."""
    html_body = (
        '<div>Danke, klingt gut.</div>'
        '<div id="ms-outlook-mobile-signature">'
        '<div>Max Mustermann</div><div>Example GmbH</div>'
        '</div>'
    )

    async def _fetch_emails_async(count, folder, unread_only, include_body, time_range="today"):
        return [{"id": "m1"}]

    async def _fetch_email_by_id_async(message_id):
        return {"body": {"contentType": "html", "content": html_body}}

    monkeypatch.setattr(outlook_tool, "_fetch_emails_async", _fetch_emails_async)
    monkeypatch.setattr(outlook_tool, "_fetch_email_by_id_async", _fetch_email_by_id_async)

    import asyncio
    result = asyncio.run(outlook_tool._detect_signature_from_sent_emails_async(sample_size=1))

    assert result is not None
    assert "Max Mustermann" in result
    assert "Example GmbH" in result
    assert "Danke, klingt gut" not in result


def test_outlook_write_email_uses_cached_tone_for_new_email(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))
    monkeypatch.setattr(outlook_tool, "_load_cached_signature", lambda: "Best,\nJohannes")
    monkeypatch.setattr(outlook_tool, "_load_cached_tone", lambda email: "casual")

    async def _detect_tone_for_contact_async(email, sample_size=5):
        raise AssertionError("must not re-detect when a tone cache hit exists")

    monkeypatch.setattr(
        outlook_tool, "_detect_tone_for_contact_async", _detect_tone_for_contact_async
    )

    payload = json.loads(
        outlook_tool.outlook_write_email(
            to="gonzalo@example.com", subject="Hi", body="Sehr geehrter Gonzalo,\n\nBest,\nJohannes"
        )
    )

    assert payload["status"] == "confirmation_required"
    assert payload["contact_tone"] == "casual"
    assert payload["contact_tone_source"] == "cache"


def test_outlook_write_email_detects_tone_on_cache_miss(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))
    monkeypatch.setattr(outlook_tool, "_load_cached_signature", lambda: "Best,\nJohannes")
    monkeypatch.setattr(outlook_tool, "_load_cached_tone", lambda email: None)

    saved = {}

    def _save_cached_tone(email, tone, source="sent-folder"):
        saved["args"] = (email, tone, source)

    monkeypatch.setattr(outlook_tool, "_save_cached_tone", _save_cached_tone)

    async def _detect_tone_for_contact_async(email, sample_size=5):
        assert email == "gonzalo@example.com"
        return "casual"

    monkeypatch.setattr(
        outlook_tool, "_detect_tone_for_contact_async", _detect_tone_for_contact_async
    )

    payload = json.loads(
        outlook_tool.outlook_write_email(
            to="gonzalo@example.com", subject="Hi", body="Body text"
        )
    )

    assert payload["contact_tone"] == "casual"
    assert payload["contact_tone_source"] == "sent-folder"
    assert saved["args"] == ("gonzalo@example.com", "casual", "sent-folder")


def test_classify_tone_detects_formal_and_casual():
    assert outlook_tool._classify_tone("Sehr geehrter Herr Müller,\n\nanbei...") == "formal"
    assert outlook_tool._classify_tone("Hi Gonzalo,\n\nkurze Frage...") == "casual"
    assert outlook_tool._classify_tone("Ambiguous opening line without markers") is None


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


def test_outlook_write_calendar_entries_default_timezone_is_not_hardcoded_utc(monkeypatch):
    """Regression test for the 13:00-lands-at-15:00 bug: when the caller
    doesn't pass timezone_name explicitly, the event body must use a real
    resolved local zone name, not the literal string 'UTC'."""
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))
    monkeypatch.setattr(outlook_tool.hermes_time, "default_timezone_name", lambda: "Europe/Berlin")

    captured_body = {}

    async def _create_calendar_entry_async(event_body):
        captured_body.update(event_body)
        return {"id": "evt-new", **event_body, "isAllDay": False}

    monkeypatch.setattr(outlook_tool, "_create_calendar_entry_async", _create_calendar_entry_async)

    payload = json.loads(
        outlook_tool.outlook_write_calendar_entries(
            action="create",
            subject="Kickoff",
            start_datetime="2026-07-10T13:00:00",
            end_datetime="2026-07-10T14:00:00",
        )
    )

    assert payload["status"] == "created"
    assert captured_body["start"]["timeZone"] == "Europe/Berlin"
    assert captured_body["end"]["timeZone"] == "Europe/Berlin"


def test_outlook_write_calendar_entries_explicit_timezone_overrides_default(monkeypatch):
    """An explicit timezone_name argument must still win over the resolved
    default."""
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))
    monkeypatch.setattr(outlook_tool.hermes_time, "default_timezone_name", lambda: "Europe/Berlin")

    captured_body = {}

    async def _create_calendar_entry_async(event_body):
        captured_body.update(event_body)
        return {"id": "evt-new", **event_body, "isAllDay": False}

    monkeypatch.setattr(outlook_tool, "_create_calendar_entry_async", _create_calendar_entry_async)

    payload = json.loads(
        outlook_tool.outlook_write_calendar_entries(
            action="create",
            subject="Kickoff",
            start_datetime="2026-07-10T13:00:00",
            end_datetime="2026-07-10T14:00:00",
            timezone_name="Asia/Kolkata",
        )
    )

    assert payload["status"] == "created"
    assert captured_body["start"]["timeZone"] == "Asia/Kolkata"


def test_outlook_read_calendar_entries_default_timezone_is_not_hardcoded_utc(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))
    monkeypatch.setattr(outlook_tool.hermes_time, "default_timezone_name", lambda: "Europe/Berlin")

    async def _fetch_calendar_entries_async(**kwargs):
        assert kwargs["timezone_name"] == "Europe/Berlin"
        return []

    monkeypatch.setattr(
        outlook_tool, "_fetch_calendar_entries_async", _fetch_calendar_entries_async
    )

    payload = json.loads(outlook_tool.outlook_read_calendar_entries())
    assert payload["timezone"] == "Europe/Berlin"


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


def test_outlook_read_contacts_falls_back_to_org_directory(monkeypatch):
    """Regression test: a colleague who only exists in the tenant's org
    directory (never saved as a personal contact) must still be found."""
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))

    async def _fetch_contacts_async(count, search):
        return []  # nothing in personal contacts

    raw_org_contacts = [
        {
            "id": "org-1",
            "displayName": "Tobias Hehl",
            "givenName": "Tobias",
            "surname": "Hehl",
            "companyName": "IAMDS",
            "jobTitle": "",
            "mail": "tobias.hehl@iamds.com",
            "proxyAddresses": ["SMTP:tobias.hehl@iamds.com"],
            "businessPhones": [],
            "mobilePhone": "",
        }
    ]

    async def _fetch_org_contacts_async(count, search):
        assert search == "Tobias Hehl"
        return raw_org_contacts

    monkeypatch.setattr(outlook_tool, "_fetch_contacts_async", _fetch_contacts_async)
    monkeypatch.setattr(outlook_tool, "_fetch_org_contacts_async", _fetch_org_contacts_async)

    payload = json.loads(outlook_tool.outlook_read_contacts(search="Tobias Hehl"))

    assert payload["count"] == 1
    assert payload["contacts"][0]["display_name"] == "Tobias Hehl"
    assert payload["contacts"][0]["emails"] == ["tobias.hehl@iamds.com"]
    assert payload["contacts"][0]["source"] == "org_directory"


def test_outlook_read_contacts_dedupes_org_directory_against_personal(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))

    async def _fetch_contacts_async(count, search):
        return [{
            "id": "c-1",
            "displayName": "Jane Doe",
            "emailAddresses": [{"address": "jane@example.com"}],
        }]

    async def _fetch_org_contacts_async(count, search):
        return [{
            "id": "org-1",
            "displayName": "Jane Doe",
            "mail": "jane@example.com",
        }]

    monkeypatch.setattr(outlook_tool, "_fetch_contacts_async", _fetch_contacts_async)
    monkeypatch.setattr(outlook_tool, "_fetch_org_contacts_async", _fetch_org_contacts_async)

    payload = json.loads(outlook_tool.outlook_read_contacts())
    assert payload["count"] == 1  # org duplicate of the personal contact is dropped


def test_outlook_read_contacts_include_org_directory_false_skips_org_search(monkeypatch):
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))

    async def _fetch_contacts_async(count, search):
        return []

    async def _fetch_org_contacts_async(count, search):
        raise AssertionError("org directory search should not be called")

    monkeypatch.setattr(outlook_tool, "_fetch_contacts_async", _fetch_contacts_async)
    monkeypatch.setattr(outlook_tool, "_fetch_org_contacts_async", _fetch_org_contacts_async)

    payload = json.loads(outlook_tool.outlook_read_contacts(include_org_directory=False))
    assert payload["count"] == 0


def test_outlook_read_contacts_org_directory_failure_is_non_fatal(monkeypatch):
    """If the org directory lookup fails (e.g. scope not consented), the
    personal-contacts result must still be returned, with a warning."""
    monkeypatch.setattr(
        outlook_tool,
        "_get_outlook_creds",
        lambda: {"tenant_id": "tenant", "client_id": "client", "client_secret": ""},
    )
    monkeypatch.setattr(outlook_tool, "_has_valid_token_cache", lambda: True)
    monkeypatch.setattr(outlook_tool, "_enable_outlook_toolset_for_cli", lambda: (False, None))

    async def _fetch_contacts_async(count, search):
        return [{
            "id": "c-1",
            "displayName": "Jane Doe",
            "emailAddresses": [{"address": "jane@example.com"}],
        }]

    async def _fetch_org_contacts_async(count, search):
        raise RuntimeError("insufficient privileges")

    monkeypatch.setattr(outlook_tool, "_fetch_contacts_async", _fetch_contacts_async)
    monkeypatch.setattr(outlook_tool, "_fetch_org_contacts_async", _fetch_org_contacts_async)

    payload = json.loads(outlook_tool.outlook_read_contacts())
    assert payload["count"] == 1
    assert "org_directory_warning" in payload


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
