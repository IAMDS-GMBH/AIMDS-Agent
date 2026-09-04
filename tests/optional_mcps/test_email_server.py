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


# ---------------------------------------------------------------------------
# AIS-231: no hard delete, audited mail writes (IMAP/SMTP)
# ---------------------------------------------------------------------------

_HEADER = b"Subject: Angebot\r\nFrom: Martin <martin@example.com>\r\n\r\n"


def _imap_mock(list_lines):
    imap = MagicMock()
    imap.list.return_value = ("OK", list_lines)
    imap.copy.return_value = ("OK", [b"COPY completed"])
    imap.fetch.return_value = ("OK", [(b"1 (BODY[HEADER.FIELDS (SUBJECT FROM)] {60}", _HEADER)])
    imap.store.return_value = ("OK", [b"1 (FLAGS (\\Deleted))"])
    imap.expunge.return_value = ("OK", [b"1"])
    return imap


def test_trash_uses_the_special_use_folder_and_is_audited(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    imap = _imap_mock([b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Trash) "/" "Gel&APY-schte Elemente"', b'(\\HasNoChildren) "/" "Trash"'])
    with patch.object(server, "_connect_imap", return_value=imap):
        res = server.email_trash_message("1")
    assert res["success"] and res["action"] == "trash" and res["destination"] == "Gel&APY-schte Elemente"
    assert "Hard delete" in res["note"] and res["subject"] == "Angebot"
    imap.copy.assert_called_once_with("1", '"Gel&APY-schte Elemente"')
    imap.store.assert_called_once_with("1", "+FLAGS", "(\\Deleted)")
    imap.expunge.assert_called_once()
    log = server.email_get_audit_log()
    assert log["count"] == 1
    e = log["entries"][0]
    assert e["tool"] == "email_trash_message" and e["action"] == "trash" and e["target_id"] == "1"
    assert e["counterpart"] == "Martin <martin@example.com>" and e["details"]["destination"] == "Gel&APY-schte Elemente"
    assert (tmp_path / "state" / "email_audit.sqlite").exists()


def test_trash_folder_fallbacks_name_and_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    imap = _imap_mock([b'(\\HasNoChildren) "." "INBOX"', b'(\\HasNoChildren) "." "INBOX.Papierkorb"'])
    assert server._find_trash_folder(imap) == "INBOX.Papierkorb"
    imap2 = _imap_mock([b'(\\HasNoChildren) "/" "INBOX"'])
    assert server._find_trash_folder(imap2) == "Trash"
    monkeypatch.setenv("EMAIL_TRASH_FOLDER", "Deleted Messages")
    assert server._find_trash_folder(imap2) == "Deleted Messages"


def test_delete_is_an_alias_for_trash_and_move_targets_named_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    imap = _imap_mock([b'(\\HasNoChildren \\Trash) "/" "Trash"'])
    with patch.object(server, "_connect_imap", return_value=imap):
        deleted = server.email_delete_message("7")
        moved = server.email_move_message("8", "Archive", folder="INBOX")
        bad = server.email_move_message("x", "Archive")
    assert deleted["action"] == "trash" and "disabled" in deleted["note"]
    assert moved["action"] == "move" and moved["destination"] == "Archive"
    assert imap.copy.call_args_list[1].args == ("8", '"Archive"')
    assert "numeric id" in bad["error"]
    entries = server.email_get_audit_log()["entries"]
    assert [e["action"] for e in entries] == ["move", "trash"]
    assert entries[1]["tool"] == "email_delete_message"


def test_copy_failure_is_reported_and_audited_without_expunge(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    imap = _imap_mock([b'(\\HasNoChildren \\Trash) "/" "Trash"'])
    imap.copy.return_value = ("NO", [b"[TRYCREATE] No such mailbox"])
    with patch.object(server, "_connect_imap", return_value=imap):
        res = server.email_move_message("3", "Nope")
    assert "failed" in res["error"]
    imap.store.assert_not_called() and imap.expunge.assert_not_called()
    e = server.email_get_audit_log(action="move")["entries"][0]
    assert e["result"] == "error" and "COPY" in e["error"]


def test_send_is_audited(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    mock_smtp = MagicMock()
    with patch.object(server, "_connect_smtp", return_value=mock_smtp), patch.object(server, "_get_smtp_config", return_value={"user": "sender@test.com"}):
        res = server.email_send_message(to=["recipient@test.com"], subject="Hello", body="World", cc=["cc@test.com"])
    assert res["audited"] is True
    e = server.email_get_audit_log(action="send")["entries"][0]
    assert e["subject"] == "Hello" and "recipient@test.com" in e["counterpart"] and e["details"]["cc"] == ["cc@test.com"]
