import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

server_path = Path(__file__).parent.parent.parent / "optional-mcps" / "email" / "server.py"
spec = importlib.util.spec_from_file_location("email_server", server_path)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def test_decode_header():
    # Plain text
    assert server._decode_header("Test Subject") == "Test Subject"
    # Encoded MIME header
    encoded = "=?utf-8?b?VGVzdCBTdWJqZWN0?="
    assert server._decode_header(encoded) == "Test Subject"


def test_get_configs():
    with patch.dict(server.os.environ, {
        "EMAIL_IMAP_HOST": "imap.test.com",
        "EMAIL_IMAP_USER": "user@test.com",
        "EMAIL_IMAP_MODE": "ssl",
        "EMAIL_SMTP_HOST": "smtp.test.com",
        "EMAIL_SMTP_USER": "user@test.com",
        "EMAIL_SMTP_MODE": "starttls",
    }):
        imap_cfg = server._get_imap_config()
        assert imap_cfg["host"] == "imap.test.com"
        assert imap_cfg["port"] == 993
        assert imap_cfg["mode"] == "ssl"

        smtp_cfg = server._get_smtp_config()
        assert smtp_cfg["host"] == "smtp.test.com"
        assert smtp_cfg["port"] == 587
        assert smtp_cfg["mode"] == "starttls"


def test_email_send_message():
    mock_smtp = MagicMock()
    with patch.object(server, "_connect_smtp", return_value=mock_smtp), patch.object(server, "_get_smtp_config", return_value={"user": "sender@test.com"}):
        res = server.email_send_message(
            to=["recipient@test.com"],
            subject="Hello",
            body="World",
            cc=["cc@test.com"],
        )
        assert res["success"] is True
        assert res["subject"] == "Hello"
        mock_smtp.sendmail.assert_called_once()
        args, _ = mock_smtp.sendmail.call_args
        assert args[0] == "sender@test.com"
        assert "recipient@test.com" in args[1]
        assert "cc@test.com" in args[1]


def test_email_test_connection_mock():
    mock_imap = MagicMock()
    mock_smtp = MagicMock()
    with patch.object(server, "_connect_imap", return_value=mock_imap), patch.object(server, "_connect_smtp", return_value=mock_smtp):
        res = server.email_test_connection()
        assert res["success"] is True
        assert res["details"]["imap"]["ok"] is True
        assert res["details"]["smtp"]["ok"] is True
