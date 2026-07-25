import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

server_path = Path(__file__).parent.parent.parent / "optional-mcps" / "ntfy" / "server.py"
spec = importlib.util.spec_from_file_location("ntfy_server", server_path)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def test_ntfy_config_defaults():
    with patch.dict(server.os.environ, {"NTFY_SERVER_URL": "https://ntfy.example.com", "NTFY_DEFAULT_TOPIC": "alerts"}):
        cfg = server._get_ntfy_config()
        assert cfg["server_url"] == "https://ntfy.example.com"
        assert cfg["default_topic"] == "alerts"


def test_ntfy_publish_message():
    mock_resp = MagicMock()
    mock_resp.is_error = False
    mock_resp.json.return_value = {"id": "msg-123", "event": "message", "topic": "test-topic"}

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.post.return_value = mock_resp

    with patch.object(server.httpx, "Client", return_value=mock_client):
        res = server.ntfy_publish_message(
            topic="test-topic",
            message="Hello world",
            title="Test Title",
            priority=5,
            tags=["warning", "test"],
        )
        assert res["id"] == "msg-123"
        mock_client.post.assert_called_once()
        args, kwargs = mock_client.post.call_args
        assert args[0] == "https://ntfy.sh/test-topic"
        assert kwargs["headers"]["Title"] == "Test Title"
        assert kwargs["headers"]["Priority"] == "5"
        assert kwargs["headers"]["Tags"] == "warning,test"


def test_ntfy_poll_topic():
    ndjson_stream = (
        '{"id":"1","time":1700000000,"event":"message","topic":"my-topic","message":"First message"}\n'
        '{"id":"2","time":1700000001,"event":"message","topic":"my-topic","message":"Second message"}\n'
    )
    mock_resp = MagicMock()
    mock_resp.is_error = False
    mock_resp.text = ndjson_stream

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.get.return_value = mock_resp

    with patch.object(server.httpx, "Client", return_value=mock_client):
        res = server.ntfy_poll_topic(topic="my-topic", since="1h", limit=10)
        assert res["topic"] == "my-topic"
        assert res["count"] == 2
        assert res["messages"][0]["message"] == "First message"
        assert res["messages"][1]["message"] == "Second message"


def test_ntfy_test_connection():
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.get.return_value = mock_resp

    with patch.object(server.httpx, "Client", return_value=mock_client):
        res = server.ntfy_test_connection()
        assert res["ok"] is True
