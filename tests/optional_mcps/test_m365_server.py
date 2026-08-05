import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

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
    mock_res = {"value": [{"id": "mem-1", "displayName": "Gonzalo"}]}
    with patch.object(server, "_graph_request", return_value=mock_res) as mock_req:
        res = server.m365_get_chat_members("chat-123")
        assert res["value"][0]["id"] == "mem-1"
        mock_req.assert_called_once_with("GET", "/me/chats/chat-123/members")


def test_m365_get_or_create_direct_chat():
    me_res = {"id": "my-id-999"}
    search_res = {"value": [{"id": "gonzalo-id-123"}]}
    chat_created = {"id": "19:direct-chat-id"}

    def side_effect(method, endpoint, json_data=None, params=None, extra_headers=None):
        if endpoint == "/me":
            return me_res
        if endpoint == "/users":
            return search_res
        if endpoint == "/chats":
            assert json_data["chatType"] == "oneOnOne"
            assert len(json_data["members"]) == 2
            return chat_created
        return {}

    with patch.object(server, "_graph_request", side_effect=side_effect):
        res = server.m365_get_or_create_direct_chat("gonzalo@example.com")
        assert res["id"] == "19:direct-chat-id"


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
        assert res["value"][0]["id"] == "msg-123"
        mock_req.assert_called_with("GET", "/me/chats/chat-1/messages", params={"$top": 10})

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

    def test_initiate_login_defaults_to_base_scopes(self):
        mock_app = MagicMock()
        mock_app.initiate_device_flow.return_value = {"user_code": "ABC123", "verification_uri": "https://example.com"}
        with patch.object(server, "_get_msal_app", return_value=mock_app):
            res = server.m365_initiate_login()
            assert res["requested_admin_scopes"] is False
            mock_app.initiate_device_flow.assert_called_once_with(scopes=server.BASE_SCOPES)

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
