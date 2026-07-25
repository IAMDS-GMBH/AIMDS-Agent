import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

server_path = Path(__file__).parent.parent.parent / "optional-mcps" / "MSOffice365MCP" / "server.py"
spec = importlib.util.spec_from_file_location("m365_server", server_path)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def test_m365_generate_admin_consent_url():
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


