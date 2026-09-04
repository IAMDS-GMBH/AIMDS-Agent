import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

pytest.importorskip("msal")  # server.py imports msal at module level

server_path = Path(__file__).parent.parent.parent / "optional-mcps" / "MSOffice365MCP" / "server.py"
spec = importlib.util.spec_from_file_location("m365_server", server_path)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def test_translate_aadsts_error():
    err_msg = "Error acquire token: AADSTS65001: The user or administrator has not consented to use the application"
    translated = server._translate_aadsts_error(err_msg)
    assert "[M365 OAuth Hint (AADSTS65001)]" in translated
    assert "consent" in translated.lower()


def test_format_timestamp_local():
    with patch.dict(server.os.environ, {"HERMES_TIMEZONE": "Europe/Berlin"}):
        # 1. UTC ISO string
        formatted = server._format_timestamp_local("2026-07-28T10:21:46Z")
        assert "2026-07-28 12:21:46" in formatted
        assert "CEST" in formatted or "CEST" in formatted or "+02" in formatted or "Europe/Berlin" in formatted

        # 2. Graph API dateTime dictionary
        dict_val = {"dateTime": "2026-07-28T10:21:46.0000000", "timeZone": "UTC"}
        formatted_dict = server._format_timestamp_local(dict_val)
        assert "2026-07-28 12:21:46" in formatted_dict

        # 3. Empty or invalid values pass through safely
        assert server._format_timestamp_local(None) == ""
        assert server._format_timestamp_local("not-a-date") == "not-a-date"
    with patch.dict(server.os.environ, {"M365_CLIENT_ID": "app-123", "M365_TENANT_ID": "tenant-456"}):
        res = server.m365_generate_admin_consent_url(redirect_uri="http://localhost:8400")
        assert res["success"] is True
        assert res["client_id"] == "app-123"
        assert res["tenant_id"] == "tenant-456"
        assert "login.microsoftonline.com/tenant-456/v2.0/adminconsent" in res["admin_consent_url"]
        assert "client_id=app-123" in res["admin_consent_url"]

    mock_member_of = {
        "value": [
            {"roleTemplateId": "62e90394-69f5-4237-9190-012177145e10", "displayName": "Global Administrator"}
        ]
    }
    with patch.object(server, "_graph_request", return_value=mock_member_of):
        res = server.m365_check_admin_status()
        assert res["is_admin"] is True
        assert "Global Administrator" in res["admin_roles"]


def test_m365_send_email_payload():
    with patch.object(server, "_graph_request") as mock_req:
        mock_req.return_value = {"success": True}
        server.m365_send_email(
            to=["user@example.com"],
            subject="Test Subject",
            body="Hello World",
            save_to_sent_items=True,
        )
        mock_req.assert_called_once()
        args, kwargs = mock_req.call_args
        assert args[0] == "POST"
        assert args[1] == "/me/sendMail"
        assert kwargs["json_data"]["saveToSentItems"] is True
        assert kwargs["json_data"]["message"]["subject"] == "Test Subject"
        # HTML is the default now — plain text gets auto-wrapped into a paragraph.
        assert kwargs["json_data"]["message"]["body"]["contentType"] == "HTML"
        assert kwargs["json_data"]["message"]["body"]["content"] == "<p>Hello World</p>"


def test_m365_send_email_plain_text_opt_out():
    with patch.object(server, "_graph_request") as mock_req:
        mock_req.return_value = {"success": True}
        server.m365_send_email(
            to=["user@example.com"],
            subject="Test Subject",
            body="Hello World",
            is_html=False,
        )
        args, kwargs = mock_req.call_args
        assert kwargs["json_data"]["message"]["body"]["contentType"] == "Text"
        assert kwargs["json_data"]["message"]["body"]["content"] == "Hello World"


def test_m365_send_email_html_passthrough_when_already_tagged():
    with patch.object(server, "_graph_request") as mock_req:
        mock_req.return_value = {"success": True}
        server.m365_send_email(
            to=["user@example.com"],
            subject="Test Subject",
            body="<p>Already HTML</p>",
        )
        args, kwargs = mock_req.call_args
        assert kwargs["json_data"]["message"]["body"]["content"] == "<p>Already HTML</p>"


def test_m365_list_emails_default_folder_is_inbox():
    with patch.object(server, "_graph_request", return_value={"value": []}) as mock_req:
        server.m365_list_emails()
        mock_req.assert_called_once()
        args, kwargs = mock_req.call_args
        assert args[0] == "GET"
        assert args[1] == "/me/messages"


def test_m365_list_emails_sentitems_folder_for_signature_detection():
    with patch.object(server, "_graph_request", return_value={"value": []}) as mock_req:
        server.m365_list_emails(folder="sentitems")
        args, kwargs = mock_req.call_args
        assert args[0] == "GET"
        assert args[1] == "/me/mailFolders/sentitems/messages"


def test_m365_token_device_flow_fallback():
    mock_app = MagicMock()
    mock_app.token_cache.serialize.return_value = "{}"
    mock_app.get_accounts.return_value = []
    mock_app.acquire_token_interactive.side_effect = RuntimeError("No browser")
    mock_app.initiate_device_flow.return_value = {
        "user_code": "TEST1234",
        "verification_uri": "https://microsoft.com/devicelogin",
    }
    mock_app.acquire_token_by_device_flow.return_value = {"access_token": "mock-token-xyz"}

    with patch.object(server, "_get_msal_app", return_value=mock_app), patch.object(server.sys, "argv", ["server.py", "--login"]):
        token = server._get_access_token()
        assert token == "mock-token-xyz"


def test_save_cache_is_atomic_and_leaves_no_tmp_file(tmp_path):
    """_save_cache must write-then-rename so concurrent MCP subprocess
    restarts can never observe/leave a truncated or corrupt cache file
    (see docstring on _save_cache for the concurrency scenario)."""
    cache_path = tmp_path / "m365_token_cache.bin"
    mock_app = MagicMock()
    mock_app.token_cache.has_state_changed = True
    mock_app.token_cache.serialize.return_value = '{"Account": {}}'

    with patch.object(server, "_get_token_cache_path", return_value=cache_path):
        server._save_cache(mock_app)

    assert cache_path.read_text(encoding="utf-8") == '{"Account": {}}'
    # No leftover .tmp.<pid> files from the write-then-rename.
    assert list(tmp_path.glob("*.tmp.*")) == []


def test_save_cache_noop_when_state_unchanged(tmp_path):
    cache_path = tmp_path / "m365_token_cache.bin"
    mock_app = MagicMock()
    mock_app.token_cache.has_state_changed = False

    with patch.object(server, "_get_token_cache_path", return_value=cache_path):
        server._save_cache(mock_app)

    assert not cache_path.exists()


def test_m365_search_users():
    mock_res = {"value": [{"id": "user-123", "displayName": "Gonzalo Oberreuter", "mail": "gonzalo@example.com"}]}
    with patch.object(server, "_graph_request", return_value=mock_res) as mock_req:
        res = server.m365_search_users("gonzalo")
        assert res["value"][0]["id"] == "user-123"
        mock_req.assert_called_once()
        args, kwargs = mock_req.call_args
        assert args[0] == "GET"
        assert args[1] == "/users"
        assert "startswith(displayName,'gonzalo')" in kwargs["params"]["$filter"]


def test_m365_get_chat_members():
    mock_res = {"value": [{"id": "mem-1", "displayName": "Gonzalo", "email": "g@example.com"}]}
    with patch.object(server, "_graph_request", return_value=mock_res) as mock_req:
        res = server.m365_get_chat_members("chat-123")
        assert res["members"][0] == {"displayName": "Gonzalo", "email": "g@example.com", "user_id": "mem-1"}
        assert res["count"] == 1
        mock_req.assert_called_once_with("GET", "/me/chats/chat-123/members")
    with patch.object(server, "_graph_request", return_value=mock_res):
        assert server.m365_get_chat_members("chat-123", raw=True)["value"][0]["id"] == "mem-1"


def test_m365_get_or_create_direct_chat():
    me_res = {"id": "my-id-999"}
    search_res = {"value": [{"id": "gonzalo-id-123"}]}
    chat_created = {"id": "19:direct-chat-id"}

    def side_effect(method, endpoint, json_data=None, params=None, extra_headers=None, **kwargs):
        if endpoint == "/me":
            return me_res
        if endpoint == "/users":
            return search_res
        if endpoint == "/chats":
            assert json_data["chatType"] == "oneOnOne"
            assert len(json_data["members"]) == 2
            assert json_data["members"][1]["user@odata.bind"].endswith("/users/gonzalo-id-123")
            return chat_created
        return {}

    server._MY_IDENTITY_CACHE.clear()
    with patch.object(server, "_graph_request", side_effect=side_effect):
        res = server.m365_get_or_create_direct_chat("gonzalo@example.com")
        assert res["id"] == "19:direct-chat-id"
        assert res["existing"] is False
    server._MY_IDENTITY_CACHE.clear()


def test_m365_sharepoint_tools():
    with patch.object(server, "_graph_request", return_value={"value": []}) as mock_req:
        server.m365_list_sharepoint_sites(search="Intranet")
        mock_req.assert_called_with("GET", "/sites?search=Intranet", params={"$top": 10})

    with patch.object(server, "_graph_request", return_value={"value": []}) as mock_req:
        server.m365_list_sharepoint_drives("site-123")
        mock_req.assert_called_with("GET", "/sites/site-123/drives")

    with patch.object(server, "_graph_request", return_value={"value": []}) as mock_req:
        server.m365_list_sharepoint_files("site-123", "drive-456")
        mock_req.assert_called_with("GET", "/sites/site-123/drives/drive-456/root/children", params={"$top": 20})


def test_m365_send_chat_message_formatting():
    with patch.object(server, "_graph_request", return_value={"id": "msg-1"}) as mock_req:
        server.m365_send_chat_message("chat-123", "Para 1\n\nPara 2")
        mock_req.assert_called_once()
        args, kwargs = mock_req.call_args
        assert args[0] == "POST"
        assert args[1] == "/me/chats/chat-123/messages"
        json_data = kwargs["json_data"]
        assert json_data["body"]["contentType"] == "html"
        assert json_data["body"]["content"] == "<p>Para 1</p><p>Para 2</p>"

    with patch.object(server, "_graph_request", return_value={"id": "msg-2"}) as mock_req:
        server.m365_send_chat_message("chat-123", "<p>Paragraph 1</p><p>Paragraph 2</p>")
        mock_req.assert_called_once()
        args, kwargs = mock_req.call_args
        json_data = kwargs["json_data"]
        assert json_data["body"]["contentType"] == "html"
        assert json_data["body"]["content"] == "<p>Paragraph 1</p><p>Paragraph 2</p>"


def test_m365_send_email_with_small_attachment(tmp_path):
    attachment = tmp_path / "note.txt"
    attachment.write_text("hello attachment")
    with patch.object(server, "_graph_request") as mock_req:
        mock_req.return_value = {"success": True}
        server.m365_send_email(
            to=["user@example.com"],
            subject="With attachment",
            body="See attached",
            attachments=[str(attachment)],
        )
        args, kwargs = mock_req.call_args
        attachments = kwargs["json_data"]["message"]["attachments"]
        assert len(attachments) == 1
        assert attachments[0]["@odata.type"] == "#microsoft.graph.fileAttachment"
        assert attachments[0]["name"] == "note.txt"
        import base64
        assert base64.b64decode(attachments[0]["contentBytes"]) == b"hello attachment"


def test_m365_send_email_attachment_missing_file():
    with pytest.raises(ValueError, match="not found"):
        server.m365_send_email(to=["user@example.com"], subject="x", body="y", attachments=["/no/such/file.txt"])


def test_m365_send_email_attachment_too_large_for_inline(tmp_path):
    big_file = tmp_path / "big.bin"
    big_file.write_bytes(b"0" * (server._MAIL_INLINE_ATTACHMENT_MAX_BYTES + 1))
    with pytest.raises(ValueError, match="MB"):
        server.m365_send_email(to=["user@example.com"], subject="x", body="y", attachments=[str(big_file)])


def test_m365_send_chat_message_with_attachment_uploads_to_onedrive_and_links(tmp_path):
    attachment = tmp_path / "report.pdf"
    attachment.write_bytes(b"%PDF-1.4 fake")

    with patch.object(server, "_upload_file_to_onedrive") as mock_upload, \
            patch.object(server, "_graph_request", return_value={"id": "msg-1"}) as mock_req:
        mock_upload.return_value = {"id": "item-1", "name": "report.pdf", "webUrl": "https://onedrive/report.pdf"}
        server.m365_send_chat_message("chat-123", "Here you go", attachments=[str(attachment)])

        mock_upload.assert_called_once_with(str(attachment))
        args, kwargs = mock_req.call_args
        json_data = kwargs["json_data"]
        assert json_data["body"]["contentType"] == "html"
        assert '<attachment id="' in json_data["body"]["content"]
        assert len(json_data["attachments"]) == 1
        assert json_data["attachments"][0]["contentType"] == "reference"
        assert json_data["attachments"][0]["contentUrl"] == "https://onedrive/report.pdf"
        assert json_data["attachments"][0]["id"] in json_data["body"]["content"]


def test_m365_send_chat_message_attachment_forces_html_content_type(tmp_path):
    attachment = tmp_path / "img.png"
    attachment.write_bytes(b"\x89PNG fake")

    with patch.object(server, "_upload_file_to_onedrive", return_value={"id": "item-1", "name": "img.png", "webUrl": "https://onedrive/img.png"}), \
            patch.object(server, "_graph_request", return_value={"id": "msg-2"}) as mock_req:
        server.m365_send_chat_message("chat-123", "Look at this", content_type="text", attachments=[str(attachment)])
        json_data = mock_req.call_args.kwargs["json_data"]
        assert json_data["body"]["contentType"] == "html"


def test_upload_file_to_onedrive_simple_put(tmp_path):
    small_file = tmp_path / "small.txt"
    small_file.write_text("small content")
    with patch.object(server, "_graph_upload", return_value={"id": "item-1", "name": "small.txt"}) as mock_upload:
        result = server._upload_file_to_onedrive(str(small_file))
        assert result["id"] == "item-1"
        mock_upload.assert_called_once()
        args, kwargs = mock_upload.call_args
        assert args[0] == "PUT"
        assert "HermesAttachments/small.txt" in args[1]


def test_upload_file_to_onedrive_chunked_for_large_files(tmp_path):
    big_file = tmp_path / "big.bin"
    big_file.write_bytes(b"x" * (server._ONEDRIVE_SIMPLE_UPLOAD_MAX_BYTES + 10))

    fake_session = {"uploadUrl": "https://upload.example.com/session"}
    with patch.object(server, "_graph_request", return_value=fake_session) as mock_req:
        mock_response = MagicMock()
        mock_response.is_error = False
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "item-2", "name": "big.bin"}
        mock_client = MagicMock()
        mock_client.put.return_value = mock_response
        mock_client.__enter__.return_value = mock_client
        with patch.object(server.httpx, "Client", return_value=mock_client):
            result = server._upload_file_to_onedrive(str(big_file))
        assert result["id"] == "item-2"
        mock_req.assert_called_once()
        assert "createUploadSession" in mock_req.call_args.args[1]


def test_m365_activity_feed_and_channel_tools():
    with patch.object(server, "_graph_request", return_value={"value": [{"id": "msg-123"}]}) as mock_req:
        res = server.m365_list_chat_messages("chat-1")
        assert res["messages"][0]["id"] == "msg-123"
        mock_req.assert_called_with("GET", "/me/chats/chat-1/messages", params={"$top": 10})
        assert server.m365_list_chat_messages("chat-1", raw=True)["value"][0]["id"] == "msg-123"

    with patch.object(server, "_graph_request", return_value={"value": [{"id": "team-1"}]}) as mock_req:
        res = server.m365_list_joined_teams()
        assert res["value"][0]["id"] == "team-1"
        mock_req.assert_called_with("GET", "/me/joinedTeams")

    with patch.object(server, "_graph_request", return_value={"value": [{"id": "ch-1"}]}) as mock_req:
        res = server.m365_list_team_channels("team-1")
        assert res["value"][0]["id"] == "ch-1"
        mock_req.assert_called_with("GET", "/teams/team-1/channels")

    with patch.object(server, "_graph_request", return_value={"value": [{"id": "ch-msg-1"}]}) as mock_req:
        res = server.m365_list_channel_messages("team-1", "ch-1")
        assert res["value"][0]["id"] == "ch-msg-1"
        mock_req.assert_called_with("GET", "/teams/team-1/channels/ch-1/messages", params={"$top": 10})

    def mock_feed_graph(method, endpoint, params=None):
        if endpoint == "/me/chats":
            return {"value": [{"id": "chat-1", "topic": "Team Sync", "chatType": "oneOnOne"}]}
        if endpoint == "/me/chats/chat-1/messages":
            return {"value": [{"id": "m1", "messageType": "message", "body": {"content": "Hello"}, "from": {"user": {"displayName": "Alice"}}}]}
        if endpoint == "/me/joinedTeams":
            return {"value": [{"id": "team-1", "displayName": "Dev Team"}]}
        if endpoint == "/teams/team-1/channels":
            return {"value": [{"id": "ch-1", "displayName": "General"}]}
        if endpoint == "/teams/team-1/channels/ch-1/messages":
            return {"value": [{"id": "m2", "messageType": "message", "body": {"content": "Deploy update"}, "from": {"user": {"displayName": "Bob"}}}]}
        return {"value": []}

    with patch.object(server, "_graph_request", side_effect=mock_feed_graph):
        feed = server.m365_get_activity_feed()
        assert len(feed["recent_chats"]) == 1
        assert feed["recent_chats"][0]["chat_id"] == "chat-1"
        assert len(feed["team_channels"]) == 1
        assert feed["team_channels"][0]["team_name"] == "Dev Team"


def test_m365_shared_calendar_tools():
    # 1. Test m365_list_calendars
    def mock_list_cals(method, endpoint, params=None):
        if endpoint == "/me/calendars":
            return {"value": [{"id": "cal-1", "name": "URLAUB"}]}
        return {"value": []}

    with patch.object(server, "_graph_request", side_effect=mock_list_cals):
        res = server.m365_list_calendars()
        assert res["value"][0]["id"] == "cal-1"

    # 2. Test m365_get_events resolving calendar by name and date range
    mock_cals = {"value": [{"id": "cal-office-123", "name": "Officezeiten"}]}
    mock_events = {"value": [{"id": "evt-1", "subject": "Officezeiten Team"}]}

    def mock_shared_graph(method, endpoint, params=None):
        if endpoint == "/me/calendars":
            return mock_cals
        if endpoint == "/me/calendars/cal-office-123/calendarView":
            return mock_events
        return {}

    with patch.object(server, "_graph_request", side_effect=mock_shared_graph):
        res = server.m365_get_events(
            calendar="Officezeiten",
            start_time_iso="2026-07-26T00:00:00Z",
            end_time_iso="2026-07-27T00:00:00Z",
        )
        assert res["resolved_calendar_name"] == "Officezeiten"
        assert res["value"][0]["subject"] == "Officezeiten Team"

    # Test m365_get_events auto-completes single date start_time_iso into full-day calendarView
    captured_params = {}
    def mock_single_date_graph(method, endpoint, params=None):
        nonlocal captured_params
        captured_params = params or {}
        return {"value": [{"id": "evt-today"}]}

    with patch.object(server, "_graph_request", side_effect=mock_single_date_graph):
        res = server.m365_get_events(start_time_iso="2026-08-04")
        assert "startDateTime" in captured_params
        assert "endDateTime" in captured_params
        assert captured_params["startDateTime"] == "2026-08-04T00:00:00"
        assert captured_params["endDateTime"] == "2026-08-04T23:59:59"
        assert res["value"][0]["id"] == "evt-today"

    # 3. Test m365_create_event in shared calendar with all-day flag
    def mock_create_graph(method, endpoint, json_data=None, params=None):
        if endpoint == "/me/calendars":
            return {"value": [{"id": "cal-vacation-999", "name": "URLAUB"}]}
        if endpoint == "/me/calendars/cal-vacation-999/events":
            return {"id": "new-evt-123"}
        return {}

    with patch.object(server, "_graph_request", side_effect=mock_create_graph):
        res = server.m365_create_event(
            subject="Sommerurlaub",
            start_time_iso="2026-08-01T00:00:00Z",
            end_time_iso="2026-08-15T00:00:00Z",
            calendar="URLAUB",
            is_all_day=True,
            categories=["URLAUB"],
        )
        assert res["id"] == "new-evt-123"





class TestRegression_ChatMessageContentAliases:
    """m365_send_chat_message previously required the exact param name
    'content'; a wrong first guess (e.g. 'message') raised a pydantic
    validation error before the tool call was even attempted. Accept the
    common aliases so a first-try guess still succeeds."""

    def test_message_alias_accepted(self):
        with patch.object(server, "_graph_request", return_value={"id": "msg-1"}) as mock_req:
            server.m365_send_chat_message("chat-123", message="Hello via alias")
            args, kwargs = mock_req.call_args
            assert kwargs["json_data"]["body"]["content"] == "<p>Hello via alias</p>"

    def test_body_alias_accepted(self):
        with patch.object(server, "_graph_request", return_value={"id": "msg-2"}) as mock_req:
            server.m365_send_chat_message("chat-123", body="Hello via body alias")
            args, kwargs = mock_req.call_args
            assert kwargs["json_data"]["body"]["content"] == "<p>Hello via body alias</p>"

    def test_text_alias_accepted(self):
        with patch.object(server, "_graph_request", return_value={"id": "msg-3"}) as mock_req:
            server.m365_send_chat_message("chat-123", text="Hello via text alias")
            args, kwargs = mock_req.call_args
            assert kwargs["json_data"]["body"]["content"] == "<p>Hello via text alias</p>"

    def test_content_takes_priority_over_aliases(self):
        with patch.object(server, "_graph_request", return_value={"id": "msg-4"}) as mock_req:
            server.m365_send_chat_message("chat-123", content="Real content", message="Ignored")
            args, kwargs = mock_req.call_args
            assert kwargs["json_data"]["body"]["content"] == "<p>Real content</p>"

    def test_missing_content_and_aliases_raises_clear_error(self):
        with pytest.raises(ValueError, match="requires the message text"):
            server.m365_send_chat_message("chat-123")


class TestRegression_ScopeTiering:
    """Default sign-in must only request scopes any tenant member can
    consent to themselves -- requesting admin-only scopes (User.Read.All,
    Directory.Read.All, Sites.ReadWrite.All) by default blocked a brand-new
    non-admin account from signing in at all, reproduced on a customer
    tenant where the LLM was told an admin was required just to send mail."""

    def test_base_scopes_exclude_admin_only_scopes(self):
        admin_only = {"User.Read.All", "Directory.Read.All", "Sites.ReadWrite.All"}
        assert not (set(server.BASE_SCOPES) & admin_only)

    def test_base_scopes_cover_core_features(self):
        core = {"Mail.ReadWrite", "Mail.Send", "Calendars.ReadWrite", "Chat.ReadWrite", "Contacts.ReadWrite"}
        assert core.issubset(set(server.BASE_SCOPES))

    def test_all_scopes_is_base_plus_admin(self):
        assert set(server.ALL_SCOPES) == set(server.BASE_SCOPES) | set(server.ADMIN_SCOPES)

    def test_initiate_login_defaults_to_self_consent_scopes(self):
        """AIS-286: the default sign-in requests only tier 0 so non-admins never
        hit "Need admin approval"; org-tier scopes arrive silently later."""
        mock_app = MagicMock()
        mock_app.initiate_device_flow.return_value = {"user_code": "ABC123", "verification_uri": "https://example.com"}
        with patch.object(server, "_get_msal_app", return_value=mock_app):
            res = server.m365_initiate_login()
            assert res["requested_admin_scopes"] is False
            assert res["requested_tier"] == "self"
            mock_app.initiate_device_flow.assert_called_once_with(scopes=server.LOGIN_SCOPES)
            assert server.LOGIN_SCOPES == server.SELF_CONSENT_SCOPES

    def test_initiate_login_scope_tier_standard_and_invalid(self):
        mock_app = MagicMock()
        mock_app.initiate_device_flow.return_value = {"user_code": "ABC123", "verification_uri": "https://example.com"}
        with patch.object(server, "_get_msal_app", return_value=mock_app):
            res = server.m365_initiate_login(scope_tier="standard")
            assert res["requested_tier"] == "standard"
            mock_app.initiate_device_flow.assert_called_once_with(scopes=server.STANDARD_SCOPES)
            assert "error" in server.m365_initiate_login(scope_tier="root")

    def test_initiate_login_requests_all_scopes_when_admin_opted_in(self):
        mock_app = MagicMock()
        mock_app.initiate_device_flow.return_value = {"user_code": "ABC123", "verification_uri": "https://example.com"}
        with patch.object(server, "_get_msal_app", return_value=mock_app):
            res = server.m365_initiate_login(request_admin_scopes=True)
            assert res["requested_admin_scopes"] is True
            mock_app.initiate_device_flow.assert_called_once_with(scopes=server.ALL_SCOPES)

    def test_check_admin_status_gives_actionable_hint_on_403(self):
        with patch.object(server, "_graph_request", side_effect=RuntimeError("MS Graph API Error [403]: Authorization_RequestDenied")):
            res = server.m365_check_admin_status()
            assert "error" in res
            assert "request_admin_scopes=True" in res["recommendation"]


class TestTeamsAttachmentsAndVaultResolution:
    def test_resolve_save_path_defaults_to_vault_if_present(self, tmp_path, monkeypatch):
        vault_dir = tmp_path / "AIMDS-Suite-Vault"
        vault_dir.mkdir()
        monkeypatch.setenv("HERMES_VAULT_PATH", str(vault_dir))

        resolved = server._resolve_save_path(None, "doc.pdf", subfolder="m365_downloads")
        assert resolved == vault_dir / "m365_downloads" / "doc.pdf"
        assert (vault_dir / "m365_downloads").is_dir()

    def test_resolve_save_path_uses_explicit_path(self, tmp_path):
        target = tmp_path / "custom" / "file.txt"
        resolved = server._resolve_save_path(str(target), "default.txt")
        assert resolved == target
        assert target.parent.is_dir()

    def test_enrich_teams_message_adds_attachments_summary(self):
        msg = {
            "id": "m123",
            "createdDateTime": "2026-07-29T10:00:00Z",
            "body": {"content": "Check this file <img src='hostedContents/hc789/$value'/>"},
            "attachments": [{"id": "att1", "name": "report.pdf", "contentType": "reference", "contentUrl": "https://example.com/report.pdf"}],
        }
        enriched = server._enrich_teams_message(msg, chat_id="c123")
        assert enriched["has_attachments"] is True
        assert len(enriched["attachments_summary"]) == 2
        assert enriched["attachments_summary"][0]["name"] == "report.pdf"
        assert enriched["attachments_summary"][1]["hosted_content_id"] == "hc789"

    def test_list_teams_message_attachments(self):
        mock_msg = {
            "attachments": [{"id": "att1", "name": "file.docx", "contentType": "reference", "contentUrl": "https://example.com/file.docx"}]
        }
        mock_hc = {"value": [{"id": "hc1", "contentType": "image/png"}]}

        def fake_graph(method, endpoint, **kwargs):
            if "hostedContents" in endpoint:
                return mock_hc
            return mock_msg

        with patch.object(server, "_graph_request", side_effect=fake_graph):
            res = server.m365_list_teams_message_attachments(message_id="m123", chat_id="c123")
            assert res["attachments_count"] == 2
            assert res["attachments"][0]["name"] == "file.docx"
            assert res["attachments"][1]["hosted_content_id"] == "hc1"

    def test_download_teams_message_attachment_hosted_content(self, tmp_path):
        with patch.object(server, "_graph_download_bytes", return_value=b"pngdata"):
            res = server.m365_download_teams_message_attachment(
                message_id="m123",
                chat_id="c123",
                hosted_content_id="hc1",
                save_path=str(tmp_path / "out.png"),
            )
            assert res["success"] is True
            assert (tmp_path / "out.png").read_bytes() == b"pngdata"

    def test_delegated_mailbox_endpoint_translation(self):
        """Verify _graph_request translates /me/ endpoints to /users/{account}/ when account is a delegated mailbox email."""
        captured_urls = []
        def fake_request(method, url, **kwargs):
            captured_urls.append(url)
            m = MagicMock()
            m.status_code = 200
            m.is_error = False
            m.json.return_value = {"value": []}
            return m

        with patch("httpx.Client.request", side_effect=fake_request), \
             patch.object(server, "_get_access_token", return_value="token123"):
            server.m365_list_emails(account="support@company.com")
            assert len(captured_urls) == 1
            assert "/users/support@company.com/messages" in captured_urls[0]

    def test_normalize_attachment_list(self):
        """Verify _normalize_attachment_list handles string paths, JSON strings, comma lists, and lists."""
        assert server._normalize_attachment_list("/file.pdf") == ["/file.pdf"]
        assert server._normalize_attachment_list('["/a.pdf", "/b.pdf"]') == ["/a.pdf", "/b.pdf"]
        assert server._normalize_attachment_list("/a.pdf, /b.pdf") == ["/a.pdf", "/b.pdf"]
        assert server._normalize_attachment_list(["/a.pdf", "/b.pdf"]) == ["/a.pdf", "/b.pdf"]

    def test_resolve_attachment_path_relative_to_terminal_cwd(self, tmp_path, monkeypatch):
        sample_file = tmp_path / "relative_doc.pdf"
        sample_file.write_text("dummy content")
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))

        resolved = server._resolve_attachment_path("relative_doc.pdf")
        assert resolved == sample_file.resolve()


def test_enrich_timestamps():
    item = {
        "createdDateTime": "2026-07-28T10:21:46Z",
        "lastModifiedDateTime": "2026-07-28T11:21:46Z",
        "dueDateTime": "2026-07-29T12:00:00Z",
    }
    with patch.dict(server.os.environ, {"HERMES_TIMEZONE": "Europe/Berlin"}):
        enriched = server._enrich_timestamps(item)
        assert "createdDateTime_local" in enriched
        assert "lastModifiedDateTime_local" in enriched
        assert "dueDateTime_local" in enriched
        assert "2026-07-28 12:21:46" in enriched["createdDateTime_local"]



def _load_server_without_hermes_cli():
    """Load server.py with hermes_cli.m365_auth unimportable.

    Mapping a module to None in sys.modules makes `from ... import ...` raise
    ImportError, which is exactly the standalone/uvx launch this branch exists
    for.
    """
    import sys

    blocked = {"hermes_cli": None, "hermes_cli.m365_auth": None}
    with patch.dict(sys.modules, blocked):
        fallback_spec = importlib.util.spec_from_file_location("m365_server_fallback", server_path)
        module = importlib.util.module_from_spec(fallback_spec)
        fallback_spec.loader.exec_module(module)

    return module


def _fake_app(serialized: str, *, changed: bool = True):
    app = MagicMock()
    app.token_cache.has_state_changed = changed
    app.token_cache.serialize.return_value = serialized

    return app


def test_fallback_save_cache_writes_atomically_without_hermes_cli(tmp_path, monkeypatch):
    """Regression: the ImportError branch used to call the symbol it lacked.

    `_save_cache` raised `NameError: name '_save_msal_cache' is not defined`,
    so every token persist failed and M365 tools looped on "authentication
    required" until the device flow expired (AADSTS70008).
    """
    module = _load_server_without_hermes_cli()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    module._save_cache(_fake_app('{"Account": {}}'))

    cache_path = tmp_path / "m365_token_cache.bin"
    assert cache_path.read_text(encoding="utf-8") == '{"Account": {}}'
    assert cache_path.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.glob("*.tmp.*")) == []


def test_fallback_save_cache_skips_write_when_cache_unchanged(tmp_path, monkeypatch):
    module = _load_server_without_hermes_cli()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    module._save_cache(_fake_app("{}", changed=False))

    assert not (tmp_path / "m365_token_cache.bin").exists()


def test_initiate_login_returns_user_code_and_flow_data():
    """The response must not invite passing the short code to complete_login.

    A caller previously read the top-level `device_code` (the short user code)
    and passed it as flow_data, which pydantic rejected with
    "flow_data Field required".
    """
    flow = {
        "user_code": "C3L6VVVF4",
        "device_code": "CBgABIQEAAAA-long-secret",
        "verification_uri": "https://login.microsoft.com/device",
        "expires_in": 900,
    }
    app = MagicMock()
    app.initiate_device_flow.return_value = flow

    with patch.object(server, "_get_msal_app", return_value=app):
        res = server.m365_initiate_login()

    assert res["user_code"] == "C3L6VVVF4"
    assert res["device_code"] == "C3L6VVVF4", "deprecated alias must keep the old value"
    assert res["flow_data"] is flow
    assert "flow_data" in res["message"]
    assert "unchanged" in res["message"]


# --------------------------------------------------------------------------- AIS-286 consent tiers

class TestConsentTiers:
    # Verified against the Graph permissions reference (2026-09): delegated
    # scopes whose "Admin consent required" column is Yes.
    ADMIN_CONSENT_REQUIRED = {
        "Chat.ReadWrite", "OnlineMeetings.Read", "Presence.Read", "Mail.ReadWrite.Shared",
        "Mail.Send.Shared", "Calendars.ReadWrite.Shared", "Tasks.ReadWrite",
        "User.Read.All", "Directory.Read.All",
    }

    def test_self_consent_tier_has_no_admin_required_scopes(self):
        assert not (set(server.SELF_CONSENT_SCOPES) & self.ADMIN_CONSENT_REQUIRED)
        assert set(server.ORG_CONSENT_SCOPES) <= self.ADMIN_CONSENT_REQUIRED

    def test_scope_sources_agree(self):
        """manifest.yaml (dashboard/CLI login) == server LOGIN_SCOPES == hermes_cli.m365_auth."""
        import yaml
        from hermes_cli import m365_auth

        manifest = yaml.safe_load((server_path.parent / "manifest.yaml").read_text(encoding="utf-8"))
        assert manifest["auth"]["scopes"] == server.LOGIN_SCOPES == m365_auth.M365_LOGIN_SCOPES
        assert server.SELF_CONSENT_SCOPES == m365_auth.M365_SELF_CONSENT_SCOPES
        assert server.ORG_CONSENT_SCOPES == m365_auth.M365_ORG_CONSENT_SCOPES
        assert server.ADMIN_SCOPES == m365_auth.M365_ADMIN_SCOPES
        assert server.ALL_SCOPES == m365_auth.M365_ALL_SCOPES
        assert set(manifest["tools"]["default_enabled"]) >= {"m365_initiate_login", "m365_complete_login", "m365_generate_admin_consent_url"}
        assert manifest["auth"]["env"][1]["default"] == "organizations"

    def test_fallback_literals_match_hermes_cli(self):
        """The ImportError branch is production code for catalog installs."""
        import re

        src = server_path.read_text(encoding="utf-8")
        block = src[src.index("except ImportError:\n    SELF_CONSENT_SCOPES"):src.index("# BASE_SCOPES keeps its historical meaning")]
        found = {name: re.findall(r'"([A-Za-z.]+)"', block[block.index(name):]) for name in ("SELF_CONSENT_SCOPES", "ORG_CONSENT_SCOPES", "ADMIN_SCOPES")}
        assert found["SELF_CONSENT_SCOPES"][: len(server.SELF_CONSENT_SCOPES)] == server.SELF_CONSENT_SCOPES
        assert found["ORG_CONSENT_SCOPES"][: len(server.ORG_CONSENT_SCOPES)] == server.ORG_CONSENT_SCOPES
        assert found["ADMIN_SCOPES"][: len(server.ADMIN_SCOPES)] == server.ADMIN_SCOPES

    def test_get_access_token_probes_tiers_in_order_and_caches(self, monkeypatch):
        server._GRANTED_TIER_CACHE.clear()
        acc = {"home_account_id": "acc-1", "username": "u@example.com"}
        calls = []

        def silent(scopes, account=None):
            calls.append(tuple(scopes))
            return {"access_token": "tok"} if scopes == server.SELF_CONSENT_SCOPES else None

        app = MagicMock()
        app.get_accounts.return_value = [acc]
        app.acquire_token_silent.side_effect = silent
        with patch.object(server, "_get_msal_app", return_value=app), patch.object(server, "_save_cache"):
            assert server._get_access_token() == "tok"
            assert calls == [tuple(server.ALL_SCOPES), tuple(server.STANDARD_SCOPES), tuple(server.SELF_CONSENT_SCOPES)]
            calls.clear()
            assert server._get_access_token() == "tok"
            # Cached tier is probed first — no failing network redemptions.
            assert calls == [tuple(server.SELF_CONSENT_SCOPES)]
        server._GRANTED_TIER_CACHE.clear()

    def test_get_access_token_prefers_org_tier_after_consent(self):
        server._GRANTED_TIER_CACHE.clear()
        acc = {"home_account_id": "acc-2"}
        app = MagicMock()
        app.get_accounts.return_value = [acc]
        app.acquire_token_silent.side_effect = lambda scopes, account=None: (
            {"access_token": "wide"} if scopes in (server.ALL_SCOPES, server.STANDARD_SCOPES) else None
        )
        with patch.object(server, "_get_msal_app", return_value=app), patch.object(server, "_save_cache"):
            assert server._get_access_token() == "wide"
            assert server._GRANTED_TIER_CACHE["acc-2"][0] == "admin"
        server._GRANTED_TIER_CACHE.clear()

    def test_login_scopes_from_argv(self):
        with patch.object(server.sys, "argv", ["server.py", "--login"]), patch.dict(server.os.environ, {}, clear=False):
            server.os.environ.pop("M365_REQUEST_ADMIN_SCOPES", None)
            assert server._login_scopes_from_argv() == server.LOGIN_SCOPES
        with patch.object(server.sys, "argv", ["server.py", "--login", "--standard"]):
            assert server._login_scopes_from_argv() == server.STANDARD_SCOPES
        with patch.object(server.sys, "argv", ["server.py", "--login", "--admin"]):
            assert server._login_scopes_from_argv() == server.ALL_SCOPES

    def test_admin_consent_url_defaults_client_and_tenant_and_fq_scopes(self, tmp_path):
        env = {"HERMES_HOME": str(tmp_path)}
        for var in ("M365_CLIENT_ID", "OUTLOOK_CLIENT_ID", "TEAMS_CLIENT_ID", "M365_TENANT_ID", "OUTLOOK_TENANT_ID", "TEAMS_TENANT_ID"):
            server.os.environ.pop(var, None)
        with patch.dict(server.os.environ, env):
            res = server.m365_generate_admin_consent_url()
        assert res["success"] is True
        assert res["client_id"] == server.DEFAULT_CLIENT_ID
        assert res["tenant_id"] == "organizations"
        url = res["admin_consent_url"]
        assert url.startswith("https://login.microsoftonline.com/organizations/v2.0/adminconsent?")
        assert "common" not in url
        assert "graph.microsoft.com%2FChat.ReadWrite" in url
        assert "redirect_uri=http%3A%2F%2Flocalhost%3A8400" in url
        with patch.dict(server.os.environ, env):
            res = server.m365_generate_admin_consent_url(use_default_scope=True)
        assert "graph.microsoft.com%2F.default" in res["admin_consent_url"]

    def test_admin_consent_url_never_uses_common_tenant(self, tmp_path):
        with patch.dict(server.os.environ, {"HERMES_HOME": str(tmp_path), "M365_TENANT_ID": "common", "M365_CLIENT_ID": "app-1"}):
            res = server.m365_generate_admin_consent_url()
        assert res["tenant_id"] == "organizations"
        assert "/organizations/v2.0/adminconsent" in res["admin_consent_url"]

    def test_graph_403_hint_includes_consent_url(self):
        fake_response = MagicMock(status_code=403, is_error=True, text='{"error":{"code":"Authorization_RequestDenied"}}')
        fake_client = MagicMock()
        fake_client.__enter__.return_value.request.return_value = fake_response
        with patch.object(server, "_get_access_token", return_value="tok"), patch.object(server.httpx, "Client", return_value=fake_client):
            with pytest.raises(RuntimeError) as exc:
                server._graph_request("GET", "/me/chats")
            assert "org-wide admin consent" in str(exc.value)
            assert "v2.0/adminconsent" in str(exc.value)
            with pytest.raises(RuntimeError) as exc:
                server._graph_request("GET", "/users")
            assert "admin tier" in str(exc.value)

    def test_complete_login_reports_granted_tier_and_consent_hint(self):
        app = MagicMock()
        app.acquire_token_by_device_flow.return_value = {"access_token": "t", "id_token_claims": {"preferred_username": "u@example.com"}}
        acc = {"home_account_id": "acc-3"}
        app.get_accounts.return_value = [acc]
        app.acquire_token_silent.side_effect = lambda scopes, account=None: (
            {"access_token": "t"} if scopes == server.SELF_CONSENT_SCOPES else None
        )
        server._GRANTED_TIER_CACHE.clear()
        with patch.object(server, "_get_msal_app", return_value=app), patch.object(server, "_save_cache"):
            res = server.m365_complete_login({"user_code": "X"})
        assert res["success"] is True
        assert res["granted_tier"] == "self"
        assert "v2.0/adminconsent" in res["admin_consent_hint"]["admin_consent_url"]
        server._GRANTED_TIER_CACHE.clear()

    def test_complete_login_consent_failure_returns_url(self):
        app = MagicMock()
        app.acquire_token_by_device_flow.return_value = {
            "error": "invalid_grant",
            "error_description": "AADSTS90094: The grant requires admin permission.",
        }
        with patch.object(server, "_get_msal_app", return_value=app):
            res = server.m365_complete_login({"user_code": "X"})
        assert res["category"] == "consent"
        assert res["error_code"] == "AADSTS90094"
        assert res["admin_consent_required"] is True
        assert "v2.0/adminconsent" in res["admin_consent_url"]

    def test_complete_login_declined_has_no_consent_url(self):
        app = MagicMock()
        app.acquire_token_by_device_flow.return_value = {"error": "authorization_declined"}
        with patch.object(server, "_get_msal_app", return_value=app):
            res = server.m365_complete_login({"user_code": "X"})
        assert res["category"] == "declined"
        assert "admin_consent_url" not in res


# --------------------------------------------------------------------------- AIS-286 Teams smart-send

ME = {"id": "me-1", "displayName": "Johannes Huchler", "userPrincipalName": "johannes@example.com"}


def _chat(cid, ctype, members, topic=None, preview_from=None, preview="", updated="2026-09-03T08:00:00Z"):
    return {
        "id": cid,
        "chatType": ctype,
        "topic": topic,
        "lastUpdatedDateTime": updated,
        "members": [{"userId": m[0], "displayName": m[1], "email": m[2]} for m in members],
        "lastMessagePreview": {
            "body": {"content": preview, "contentType": "html"},
            "from": {"user": {"displayName": preview_from or ""}},
            "createdDateTime": updated,
        } if preview else None,
    }


CHATS = [
    _chat("c-fischi", "oneOnOne", [("me-1", "Johannes Huchler", "johannes@example.com"), ("u-2", "Martin Fischerauer", "martin.fischerauer@example.com")], preview_from="Martin Fischerauer", preview="<p>passt, <b>danke</b></p>"),
    _chat("c-martin2", "oneOnOne", [("me-1", "Johannes Huchler", "johannes@example.com"), ("u-3", "Martin Berger", "martin.berger@example.com")]),
    _chat("c-group", "group", [("me-1", "Johannes Huchler", "johannes@example.com"), ("u-2", "Martin Fischerauer", "martin.fischerauer@example.com"), ("u-4", "Anna Schmidt", "anna@example.com")], topic="Projekt Hermes Rollout"),
    _chat("c-anna", "oneOnOne", [("me-1", "Johannes Huchler", "johannes@example.com"), ("u-4", "Anna Schmidt", "anna@example.com")]),
]


def _teams_graph(chats=CHATS, messages=None, sent=None):
    sent = sent if sent is not None else []

    def side_effect(method, endpoint, json_data=None, params=None, extra_headers=None, account=None, **kwargs):
        if endpoint == "/me":
            return ME
        if endpoint == "/me/chats":
            return {"value": chats}
        if endpoint.endswith("/messages") and method == "POST":
            sent.append((endpoint, json_data))
            return {"id": "msg-new"}
        if endpoint.endswith("/messages"):
            return {"value": messages or []}
        if endpoint == "/users":
            raise RuntimeError("MS Graph API Error [403]: Authorization_RequestDenied")
        return {"value": []}

    return side_effect, sent


class TestFindChat:
    def setup_method(self):
        server._MY_IDENTITY_CACHE.clear()

    def test_exact_email_is_unique(self):
        with patch.object(server, "_graph_request", side_effect=_teams_graph()[0]):
            res = server.m365_find_chat("martin.fischerauer@example.com")
        assert res["resolution"] == "unique"
        assert res["chat_id"] == "c-fischi"
        assert res["candidates"][0]["match_reason"].startswith("exact email")
        top = res["candidates"][0]
        assert top["members"][1] == {"displayName": "Martin Fischerauer", "email": "martin.fischerauer@example.com", "user_id": "u-2"}
        assert top["last_message"]["preview"] == "passt, danke"  # HTML stripped
        assert "lastMessagePreview" not in top

    def test_full_name_prefers_direct_chat_over_group(self):
        with patch.object(server, "_graph_request", side_effect=_teams_graph()[0]):
            res = server.m365_find_chat("Martin Fischerauer")
        assert res["resolution"] == "unique"
        assert res["chat_id"] == "c-fischi"
        assert [c["chat_id"] for c in res["candidates"][:2]] == ["c-fischi", "c-group"]

    def test_first_name_with_two_people_is_ambiguous(self):
        with patch.object(server, "_graph_request", side_effect=_teams_graph()[0]):
            res = server.m365_find_chat("Martin")
        assert res["resolution"] == "ambiguous"
        assert "chat_id" not in res
        assert {c["chat_id"] for c in res["candidates"][:2]} == {"c-fischi", "c-martin2"}
        assert "ask" in res["next_step"].lower()

    def test_nickname_prefix_matches_surname(self):
        with patch.object(server, "_graph_request", side_effect=_teams_graph()[0]):
            res = server.m365_find_chat("Fischi")
        assert res["resolution"] == "unique"
        assert res["chat_id"] == "c-fischi"

    def test_group_topic(self):
        with patch.object(server, "_graph_request", side_effect=_teams_graph()[0]):
            res = server.m365_find_chat("Hermes Rollout", prefer="group")
        assert res["resolution"] == "unique"
        assert res["chat_id"] == "c-group"

    def test_no_match_gives_direct_chat_hint_for_email(self):
        with patch.object(server, "_graph_request", side_effect=_teams_graph()[0]):
            res = server.m365_find_chat("nobody@example.com")
        assert res["resolution"] == "none"
        assert res["direct_chat_hint"]["tool"] == "m365_get_or_create_direct_chat"
        assert "direct_chat_hint" not in server.m365_find_chat.__wrapped__("x") if hasattr(server.m365_find_chat, "__wrapped__") else True

    def test_empty_query_rejected(self):
        assert "error" in server.m365_find_chat("  ")


class TestSendChatMessageSmart:
    def setup_method(self):
        server._MY_IDENTITY_CACHE.clear()

    def test_to_unique_sends_markdown_as_html_and_reports_recipient(self):
        side_effect, sent = _teams_graph()
        with patch.object(server, "_graph_request", side_effect=side_effect):
            res = server.m365_send_chat_message(to="Fischi", content="Hi Martin,\n\nam **09./10.09** baue ich Überstunden ab:\n- Di frei\n- Mi ab 12")
        assert res["sent"] is True
        assert res["chat_id"] == "c-fischi"
        assert res["recipient"]["members"][0]["displayName"] == "Martin Fischerauer"
        assert res["chat_type"] == "oneOnOne"
        assert sent[0][0] == "/me/chats/c-fischi/messages"
        html = sent[0][1]["body"]["content"]
        assert sent[0][1]["body"]["contentType"] == "html"
        assert html == "<p>Hi Martin,</p><p>am <strong>09./10.09</strong> baue ich Überstunden ab:</p><ul><li>Di frei</li><li>Mi ab 12</li></ul>"
        assert res["rendered_html"] == html
        assert "**" not in res["plain_text"] and "Di frei" in res["plain_text"]
        assert res["message_id"] == "msg-new"

    def test_to_ambiguous_does_not_send(self):
        side_effect, sent = _teams_graph()
        with patch.object(server, "_graph_request", side_effect=side_effect):
            res = server.m365_send_chat_message(to="Martin", content="hi")
        assert res["sent"] is False
        assert res["resolution"] == "ambiguous"
        assert "ambiguous" in res["error"]
        assert sent == []

    def test_to_unknown_does_not_send(self):
        side_effect, sent = _teams_graph()
        with patch.object(server, "_graph_request", side_effect=side_effect):
            res = server.m365_send_chat_message(to="Zaphod", content="hi")
        assert res["sent"] is False and res["resolution"] == "none" and sent == []

    def test_dry_run_never_sends(self):
        side_effect, sent = _teams_graph()
        with patch.object(server, "_graph_request", side_effect=side_effect):
            res = server.m365_send_chat_message(to="martin.fischerauer@example.com", content="*kurz*", dry_run=True)
        assert res["dry_run"] is True and res["sent"] is False
        assert res["rendered_html"] == "<p><em>kurz</em></p>"
        assert res["recipient"]["chat_id"] == "c-fischi"
        assert sent == []

    def test_missing_chat_id_and_to_raises(self):
        with pytest.raises(ValueError):
            server.m365_send_chat_message(content="hi")

    def test_existing_html_passes_through_and_text_mode_is_verbatim(self):
        with patch.object(server, "_graph_request", return_value={"id": "m"}) as mock_req:
            server.m365_send_chat_message("chat-1", "<p>Hallo <b>Welt</b></p>")
            assert mock_req.call_args.kwargs["json_data"]["body"]["content"] == "<p>Hallo <b>Welt</b></p>"
        with patch.object(server, "_graph_request", return_value={"id": "m"}) as mock_req:
            res = server.m365_send_chat_message("chat-1", "**raw**", content_type="text")
            assert mock_req.call_args.kwargs["json_data"]["body"] == {"contentType": "text", "content": "**raw**"}
            assert res["rendered_html"] is None


class TestMarkdownToTeamsHtml:
    def test_blocks_and_inline(self):
        md = "# Update\n\nHallo **Team**, kurzer *Stand*:\n\n1. erledigt\n2. offen\n\n> Zitat\n\nLink: [Doku](https://ex.ample/d) und `code` <3"
        html = server._markdown_to_teams_html(md)
        assert html == (
            "<p><strong>Update</strong></p><p>Hallo <strong>Team</strong>, kurzer <em>Stand</em>:</p>"
            "<ol><li>erledigt</li><li>offen</li></ol><blockquote>Zitat</blockquote>"
            '<p>Link: <a href="https://ex.ample/d">Doku</a> und <code>code</code> &lt;3</p>'
        )

    def test_line_breaks_inside_paragraph_and_escaping(self):
        assert server._markdown_to_teams_html("a\nb\n\nc & d") == "<p>a<br>b</p><p>c &amp; d</p>"
        assert server._markdown_to_teams_html("") == ""
        assert server._markdown_to_teams_html("<ul><li>x</li></ul>") == "<ul><li>x</li></ul>"

    def test_html_to_text(self):
        assert server._html_to_text("<p>Hi <b>x</b></p><ul><li>a</li><li>b</li></ul>&nbsp;") == "Hi x\n• a\n• b"


class TestCompactRecords:
    def test_list_chats_compact_and_raw(self):
        side_effect, _ = _teams_graph()
        with patch.object(server, "_graph_request", side_effect=side_effect):
            res = server.m365_list_chats(top=5)
            assert res["count"] == 4
            assert set(res["chats"][0]) == {"chat_id", "chat_type", "topic", "members", "last_message", "updated_at", "web_url"}
            raw = server.m365_list_chats(top=5, raw=True)
            assert raw["value"][0]["id"] == "c-fischi"

    def test_list_chat_messages_compact_strips_html_and_system_events(self):
        messages = [
            {"id": "m1", "messageType": "message", "createdDateTime": "2026-09-03T08:00:00Z", "from": {"user": {"id": "u-2", "displayName": "Martin"}}, "body": {"contentType": "html", "content": "<p>Hallo <b>du</b></p>"}},
            {"id": "m2", "messageType": "systemEventMessage", "body": {"contentType": "html", "content": "<systemEventMessage/>"}},
        ]
        side_effect, _ = _teams_graph(messages=messages)
        with patch.object(server, "_graph_request", side_effect=side_effect):
            res = server.m365_list_chat_messages("c-fischi", top=5)
        assert res["count"] == 1
        assert res["messages"][0]["from"] == "Martin" and res["messages"][0]["text"] == "Hallo du"
        assert res["messages"][0]["from_user_id"] == "u-2"


class TestDirectChatWithoutDirectory:
    def setup_method(self):
        server._MY_IDENTITY_CACHE.clear()

    def test_existing_direct_chat_is_returned_not_created(self):
        side_effect, sent = _teams_graph()
        with patch.object(server, "_graph_request", side_effect=side_effect) as mock_req:
            res = server.m365_get_or_create_direct_chat("Martin Fischerauer")
        assert res["id"] == "c-fischi" and res["existing"] is True
        assert all(c.args[1] != "/chats" for c in mock_req.call_args_list)

    def test_ambiguous_name_asks_instead_of_creating(self):
        side_effect, _ = _teams_graph()
        with patch.object(server, "_graph_request", side_effect=side_effect):
            res = server.m365_get_or_create_direct_chat("Martin")
        assert "error" in res and len(res["candidates"]) >= 2

    def test_unknown_email_without_directory_binds_upn_directly(self):
        created = {}

        def side_effect(method, endpoint, json_data=None, params=None, **kwargs):
            if endpoint == "/me":
                return ME
            if endpoint == "/me/chats":
                return {"value": []}
            if endpoint.startswith("/users"):
                raise RuntimeError("MS Graph API Error [403]: Authorization_RequestDenied")
            if endpoint == "/chats":
                created.update(json_data)
                return {"id": "19:new"}
            return {"value": []}

        with patch.object(server, "_graph_request", side_effect=side_effect):
            res = server.m365_get_or_create_direct_chat("new.person@example.com")
        assert res["id"] == "19:new"
        assert created["members"][1]["user@odata.bind"].endswith("/users/new.person@example.com")

    def test_unknown_name_without_directory_returns_actionable_error(self):
        def side_effect(method, endpoint, json_data=None, params=None, **kwargs):
            if endpoint == "/me":
                return ME
            if endpoint.startswith("/users"):
                raise RuntimeError("403")
            return {"value": []}

        with patch.object(server, "_graph_request", side_effect=side_effect):
            res = server.m365_get_or_create_direct_chat("Zaphod Beeblebrox")
        assert "email" in res["error"]


class TestChatStyle:
    def setup_method(self):
        server._MY_IDENTITY_CACHE.clear()

    def _messages(self, mine, theirs=("Alles klar, danke!",)):
        out = []
        for i, t in enumerate(mine):
            out.append({"id": f"me{i}", "messageType": "message", "createdDateTime": "2026-09-01T08:00:00Z", "from": {"user": {"id": "me-1", "displayName": "Johannes Huchler"}}, "body": {"contentType": "text", "content": t}})
        for i, t in enumerate(theirs):
            out.append({"id": f"th{i}", "messageType": "message", "createdDateTime": "2026-09-01T09:00:00Z", "from": {"user": {"id": "u-2", "displayName": "Martin"}}, "body": {"contentType": "html", "content": f"<p>{t}</p>"}})
        return out

    def test_profile_from_own_messages_casual(self):
        mine = ["Hi Martin, bist du am Mi da? VG", "Danke dir! 👍", "Moin, ich bin morgen im Homeoffice, melde mich dann. VG"]
        side_effect, _ = _teams_graph(messages=self._messages(mine))
        with patch.object(server, "_graph_request", side_effect=side_effect):
            res = server.m365_get_chat_style(to="Fischi")
        prof = res["profile"]
        assert res["source_messages"] == 3 and res["their_messages_seen"] == 1
        assert prof["language"] == "de" and prof["address"] == "du" and prof["formality"] == "casual"
        assert prof["sign_off"] == "vg" and prof["emoji"] == "sometimes"
        assert prof["typical_length_words"] <= 12
        assert len(res["examples"]) == 3 and res["recipient"]["chat_type"] == "oneOnOne"
        assert "Teams style with" in res["how_to_use"]

    def test_profile_formal_sie(self):
        mine = ["Sehr geehrter Herr Müller,\n\nkönnten Sie mir bitte die Unterlagen bis Freitag zusenden? Ich benötige sie für den Bericht.\n\nViele Grüße\nJohannes Huchler"] * 3
        side_effect, _ = _teams_graph(messages=self._messages(mine))
        with patch.object(server, "_graph_request", side_effect=side_effect):
            res = server.m365_get_chat_style(chat_id="c-fischi")
        prof = res["profile"]
        assert prof["address"] == "Sie" and prof["formality"] == "formal"
        assert prof["greeting"] == "sehr geehrter" and prof["sign_off"].startswith("viele gr")

    def test_no_history_returns_teams_defaults(self):
        side_effect, _ = _teams_graph(messages=self._messages([]))
        with patch.object(server, "_graph_request", side_effect=side_effect):
            res = server.m365_get_chat_style(chat_id="c-fischi")
        assert res["profile"] is None and res["source_messages"] == 0
        assert res["defaults"]["sign_off"] == "none" and res["defaults"]["signature"] is False and res["defaults"]["attribution"] is False

    def test_ambiguous_recipient(self):
        side_effect, _ = _teams_graph()
        with patch.object(server, "_graph_request", side_effect=side_effect):
            res = server.m365_get_chat_style(to="Martin")
        assert res["error"] == "recipient ambiguous" and len(res["candidates"]) >= 2


# --------------------------------------------------------------------------- AIS-288 Teams links + chat files

CHAT_LINK = "https://teams.microsoft.com/l/chat/19%3A6bd3df1234%40thread.v2/0?context=%7B%22contextType%22%3A%22chat%22%7D"
MSG_LINK = "https://teams.microsoft.com/l/message/19%3A6bd3df1234%40thread.v2/1725000000002?tenantId=t&context=c"


class TestTeamsLinks:
    def test_parse_chat_and_message_links(self):
        assert server._parse_teams_link(CHAT_LINK) == {"chat_id": "19:6bd3df1234@thread.v2", "message_id": None, "kind": "chat"}
        assert server._parse_teams_link(MSG_LINK) == {"chat_id": "19:6bd3df1234@thread.v2", "message_id": "1725000000002", "kind": "message"}
        assert server._parse_teams_link("Fischi") is None
        assert server._parse_teams_link("") is None
        assert server._coerce_chat_ref(CHAT_LINK) == "19:6bd3df1234@thread.v2"
        assert server._coerce_chat_ref(" 19:abc@thread.v2 ") == "19:abc@thread.v2"
        assert server._coerce_chat_ref(None) is None

    def test_find_chat_with_link_is_unique_without_ranking(self):
        server._MY_IDENTITY_CACHE.clear()
        calls = []

        def side_effect(method, endpoint, json_data=None, params=None, extra_headers=None, account=None, **kw):
            calls.append(endpoint)
            if endpoint == "/me/chats/19:6bd3df1234@thread.v2":
                return _chat("19:6bd3df1234@thread.v2", "oneOnOne", [("me-1", "Johannes Huchler", "johannes@example.com"), ("u-2", "Martin Fischerauer", "m@example.com")])
            return {"value": []}

        with patch.object(server, "_graph_request", side_effect=side_effect):
            res = server.m365_find_chat(MSG_LINK)
        assert res["resolution"] == "unique" and res["chat_id"] == "19:6bd3df1234@thread.v2"
        assert res["message_id"] == "1725000000002"
        assert res["candidates"][0]["match_reason"] == "teams link"
        assert "/me/chats" not in calls  # no scan of all chats

    def test_list_chat_messages_and_style_accept_links(self):
        with patch.object(server, "_graph_request", return_value={"value": []}) as mock_req:
            server.m365_list_chat_messages(CHAT_LINK, top=3)
            assert mock_req.call_args.args[1] == "/me/chats/19:6bd3df1234@thread.v2/messages"
        with patch.object(server, "_graph_request", return_value={"value": []}) as mock_req:
            server.m365_list_teams_message_attachments(message_id="m1", chat_id=CHAT_LINK)
            assert mock_req.call_args_list[0].args[1] == "/me/chats/19:6bd3df1234@thread.v2/messages/m1"

    def test_send_chat_message_accepts_link_as_to(self):
        with patch.object(server, "_graph_request", return_value={"id": "msg"}) as mock_req:
            res = server.m365_send_chat_message(to=CHAT_LINK, content="hi")
        assert res["sent"] is True and res["chat_id"] == "19:6bd3df1234@thread.v2"
        assert mock_req.call_args.args[1] == "/me/chats/19:6bd3df1234@thread.v2/messages"


class TestDownloadChatFiles:
    CHAT = "19:6bd3df1234@thread.v2"

    def _messages(self):
        return [
            {"id": "1725000000003", "messageType": "message", "createdDateTime": "2026-09-04T07:00:00Z",
             "from": {"user": {"id": "u-2", "displayName": "Martin"}}, "body": {"contentType": "text", "content": "danke"}},
            {"id": "1725000000002", "messageType": "message", "createdDateTime": "2026-09-04T06:59:00Z",
             "from": {"user": {"id": "u-2", "displayName": "Martin"}},
             "body": {"contentType": "html", "content": "<p>anbei</p><attachment id=\"a1\"></attachment>"},
             "attachments": [{"id": "a1", "name": "Angebot.docx", "contentType": "reference", "contentUrl": "https://iamds.sharepoint.com/sites/x/Angebot.docx"}]},
            {"id": "1725000000001", "messageType": "message", "createdDateTime": "2026-09-04T06:58:00Z",
             "from": {"user": {"id": "me-1", "displayName": "Johannes Huchler"}},
             "body": {"contentType": "html", "content": '<p>screenshot</p><img src="https://graph.microsoft.com/v1.0/chats/x/messages/y/hostedContents/hc1/$value">'}},
            {"id": "1725000000000", "messageType": "systemEventMessage", "body": {"contentType": "html", "content": "<systemEventMessage/>"}},
        ]

    def _graph(self, messages):
        def side_effect(method, endpoint, json_data=None, params=None, extra_headers=None, account=None, **kw):
            if endpoint == "/me":
                return ME
            if endpoint == f"/me/chats/{self.CHAT}/messages":
                return {"value": messages}
            if endpoint == f"/me/chats/{self.CHAT}":
                return _chat(self.CHAT, "oneOnOne", [("me-1", "Johannes Huchler", "johannes@example.com"), ("u-2", "Martin Fischerauer", "martin.fischerauer@example.com")])
            if endpoint == "/me/chats":
                return {"value": CHATS}
            return {"value": []}
        return side_effect

    def test_downloads_files_from_recent_messages_into_vault(self, tmp_path, monkeypatch):
        server._MY_IDENTITY_CACHE.clear()
        vault = tmp_path / "vault"; vault.mkdir()
        monkeypatch.setenv("HERMES_VAULT_PATH", str(vault))
        downloads = []

        def fake_download(url, account=None):
            downloads.append(url)
            if "/shares/" in url:
                return b"%DOCX%"
            if "hostedContents/hc1" in url:
                return b"\x89PNG"
            raise RuntimeError("unexpected")

        with patch.object(server, "_graph_request", side_effect=self._graph(self._messages())), \
             patch.object(server, "_graph_download_bytes", side_effect=fake_download):
            res = server.m365_download_chat_files(chat_id=CHAT_LINK, last=5)
        assert res["chat_id"] == self.CHAT and res["messages_scanned"] == 3
        assert res["count"] == 1 and res["errors"] == []
        f = res["files"][0]
        assert f["name"] == "Angebot.docx" and f["from"] == "Martin" and f["message_id"] == "1725000000002"
        assert Path(f["saved_path"]).read_bytes() == b"%DOCX%"
        assert Path(f["saved_path"]).parent == vault / "documents" / "m365_attachments" / "Martin_Fischerauer"
        assert all("/shares/u!" in u for u in downloads)  # images skipped by default

    def test_include_images_and_duplicate_names(self, tmp_path, monkeypatch):
        server._MY_IDENTITY_CACHE.clear()
        vault = tmp_path / "vault"; vault.mkdir()
        monkeypatch.setenv("HERMES_VAULT_PATH", str(vault))
        (vault / "documents" / "m365_attachments" / "Martin_Fischerauer").mkdir(parents=True)
        (vault / "documents" / "m365_attachments" / "Martin_Fischerauer" / "Angebot.docx").write_bytes(b"old")

        def fake_download(url, account=None):
            return b"\x89PNG" if "hostedContents" in url else b"%DOCX%"

        with patch.object(server, "_graph_request", side_effect=self._graph(self._messages())), \
             patch.object(server, "_graph_download_bytes", side_effect=fake_download):
            res = server.m365_download_chat_files(chat_id=self.CHAT, last=5, include_images=True)
        names = sorted(f["name"] for f in res["files"])
        assert names == ["Angebot.docx", "inline_image_hc1.png"]
        docx = next(f for f in res["files"] if f["name"] == "Angebot.docx")
        assert docx["saved_path"].endswith("Angebot (2).docx")  # existing file kept

    def test_message_link_restricts_to_that_message(self, tmp_path, monkeypatch):
        server._MY_IDENTITY_CACHE.clear()
        monkeypatch.setenv("HERMES_VAULT_PATH", str(tmp_path))
        with patch.object(server, "_graph_request", side_effect=self._graph(self._messages())), \
             patch.object(server, "_graph_download_bytes", return_value=b"x"):
            res = server.m365_download_chat_files(chat_id=MSG_LINK, last=10)
        assert res["messages_scanned"] == 1 and res["count"] == 1

    def test_resolves_recipient_by_name_and_reports_hint_when_empty(self, tmp_path, monkeypatch):
        server._MY_IDENTITY_CACHE.clear()
        monkeypatch.setenv("HERMES_VAULT_PATH", str(tmp_path))
        messages = [m for m in self._messages() if m["id"] == "1725000000003"]

        def side_effect(method, endpoint, json_data=None, params=None, extra_headers=None, account=None, **kw):
            if endpoint == "/me":
                return ME
            if endpoint == "/me/chats":
                return {"value": CHATS}
            if endpoint == "/me/chats/c-fischi/messages":
                return {"value": messages}
            return {"value": []}

        with patch.object(server, "_graph_request", side_effect=side_effect):
            res = server.m365_download_chat_files(to="Fischi", last=5)
        assert res["chat_id"] == "c-fischi" and res["count"] == 0
        assert res["recipient"]["chat_type"] == "oneOnOne"
        assert "include_images" in res["hint"]

    def test_download_failure_is_reported_not_raised(self, tmp_path, monkeypatch):
        server._MY_IDENTITY_CACHE.clear()
        monkeypatch.setenv("HERMES_VAULT_PATH", str(tmp_path))
        with patch.object(server, "_graph_request", side_effect=self._graph(self._messages())), \
             patch.object(server, "_graph_download_bytes", side_effect=RuntimeError("403")):
            res = server.m365_download_chat_files(chat_id=self.CHAT, last=5)
        assert res["count"] == 0 and res["errors"][0]["name"] == "Angebot.docx"

    def test_requires_chat_or_recipient(self):
        assert "error" in server.m365_download_chat_files()

    def test_manifest_enables_new_tool(self):
        import yaml

        manifest = yaml.safe_load((server_path.parent / "manifest.yaml").read_text(encoding="utf-8"))
        assert "m365_download_chat_files" in manifest["tools"]["default_enabled"]
        assert "m365_download_teams_message_attachment" in manifest["tools"]["default_enabled"]


class TestDownloadEmailAttachments:
    def _graph(self, attachments):
        def side_effect(method, endpoint, json_data=None, params=None, extra_headers=None, account=None, **kw):
            if endpoint == "/me/messages/msg-1":
                return {"subject": "Angebot: LBBW / TP3", "from": {"emailAddress": {"name": "Martin"}}, "receivedDateTime": "2026-09-04T07:00:00Z"}
            if endpoint == "/me/messages/msg-1/attachments":
                return {"value": attachments}
            return {}
        return side_effect

    def test_saves_all_file_attachments_into_vault(self, tmp_path, monkeypatch):
        import base64

        vault = tmp_path / "vault"; vault.mkdir()
        monkeypatch.setenv("HERMES_VAULT_PATH", str(vault))
        atts = [
            {"@odata.type": "#microsoft.graph.fileAttachment", "id": "a1", "name": "Angebot.pdf", "contentType": "application/pdf", "contentBytes": base64.b64encode(b"%PDF").decode()},
            {"@odata.type": "#microsoft.graph.fileAttachment", "id": "a2", "name": "logo.png", "contentType": "image/png", "isInline": True, "contentBytes": base64.b64encode(b"png").decode()},
            {"@odata.type": "#microsoft.graph.fileAttachment", "id": "a3", "name": "big.xlsx", "contentType": "application/x"},
            {"@odata.type": "#microsoft.graph.itemAttachment", "id": "a4", "name": "Fwd: alt"},
        ]
        with patch.object(server, "_graph_request", side_effect=self._graph(atts)), \
             patch.object(server, "_graph_download_bytes", return_value=b"XLSX") as dl:
            res = server.m365_download_email_attachments("msg-1")
        assert res["subject"].startswith("Angebot") and res["from"] == "Martin"
        assert [f["name"] for f in res["files"]] == ["Angebot.pdf", "big.xlsx"]
        assert res["skipped"] == 2 and res["errors"] == []
        assert Path(res["files"][0]["saved_path"]).read_bytes() == b"%PDF"
        assert Path(res["files"][0]["saved_path"]).parent == vault / "documents" / "m365_attachments" / "mail" / "Angebot_LBBW_TP3"
        assert dl.call_args.args[0] == "/me/messages/msg-1/attachments/a3/$value"

    def test_include_inline_and_error_reporting(self, tmp_path, monkeypatch):
        import base64

        monkeypatch.setenv("HERMES_VAULT_PATH", str(tmp_path))
        atts = [
            {"@odata.type": "#microsoft.graph.fileAttachment", "id": "a2", "name": "logo.png", "isInline": True, "contentBytes": base64.b64encode(b"png").decode()},
            {"@odata.type": "#microsoft.graph.fileAttachment", "id": "a3", "name": "broken.bin"},
        ]
        with patch.object(server, "_graph_request", side_effect=self._graph(atts)), \
             patch.object(server, "_graph_download_bytes", side_effect=RuntimeError("404")):
            res = server.m365_download_email_attachments("msg-1", include_inline=True)
        assert [f["name"] for f in res["files"]] == ["logo.png"]
        assert res["errors"][0]["name"] == "broken.bin"

    def test_no_files_gives_hint(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_VAULT_PATH", str(tmp_path))
        with patch.object(server, "_graph_request", side_effect=self._graph([])):
            res = server.m365_download_email_attachments("msg-1")
        assert res["count"] == 0 and "include_inline" in res["hint"]

    def test_manifest_enables_tool(self):
        import yaml

        manifest = yaml.safe_load((server_path.parent / "manifest.yaml").read_text(encoding="utf-8"))
        assert "m365_download_email_attachments" in manifest["tools"]["default_enabled"]


class TestDownloadDriveFileByUrl:
    """AIS-289: `m365_download_drive_file` takes a SharePoint/OneDrive URL and
    resolves it through the shares API — the agent had only the contentUrl of
    a chat attachment and got a 404 from the item-id path."""

    URL = "https://iamds-my.sharepoint.com/personal/m_f_iamds_com/Documents/Microsoft%20Teams-Chatdateien/plan_4.docx"

    def test_url_goes_through_shares_api(self, tmp_path, monkeypatch):
        vault = tmp_path / "vault"; vault.mkdir()
        monkeypatch.setenv("HERMES_VAULT_PATH", str(vault))
        calls = []

        def fake_request(method, endpoint, **kw):
            calls.append(endpoint)
            assert endpoint.startswith("/shares/u!") and endpoint.endswith("/driveItem")
            return {"id": "01ITEM", "name": "plan_4.docx", "size": 6}

        def fake_download(endpoint, account=None):
            calls.append(endpoint)
            assert endpoint.startswith("/shares/u!") and endpoint.endswith("/driveItem/content")
            return b"%DOCX%"

        with patch.object(server, "_graph_request", side_effect=fake_request), \
             patch.object(server, "_graph_download_bytes", side_effect=fake_download):
            res = server.m365_download_drive_file(self.URL)
        assert res["success"] is True and res["source"] == "url" and res["file_id"] == "01ITEM"
        assert Path(res["saved_path"]).read_bytes() == b"%DOCX%"
        assert Path(res["saved_path"]).parent == vault / "documents" / "m365_downloads"
        assert len(calls) == 2

    def test_item_id_path_unchanged(self, tmp_path):
        with patch.object(server, "_graph_request", return_value={"id": "01ITEM", "name": "a.pdf"}) as req, \
             patch.object(server, "_graph_download_bytes", return_value=b"%PDF") as dl:
            res = server.m365_download_drive_file("01ITEM", save_path=str(tmp_path / "a.pdf"))
        assert req.call_args[0][1] == "/me/drive/items/01ITEM"
        assert dl.call_args[0][0] == "/me/drive/items/01ITEM/content"
        assert res["source"] == "item_id" and (tmp_path / "a.pdf").read_bytes() == b"%PDF"

    def test_graph_error_is_returned_not_raised(self):
        with patch.object(server, "_graph_request", return_value={"error": "MS Graph API Error [404]: itemNotFound"}):
            res = server.m365_download_drive_file(self.URL)
        assert "error" in res
