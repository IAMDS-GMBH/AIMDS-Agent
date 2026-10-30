"""Gateway side of the per-turn "understood" line (AIS-789)."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hermes_state import SessionDB


@pytest.fixture()
def server():
    import tui_gateway.server as mod

    yield mod
    mod._sessions.clear()


@pytest.fixture()
def db(tmp_path):
    session_db = SessionDB(db_path=tmp_path / "state.db")
    yield session_db
    session_db.close()


@pytest.fixture(autouse=True)
def _understood_enabled(monkeypatch):
    monkeypatch.delenv("HERMES_DISABLE_UNDERSTOOD", raising=False)


def test_only_a_desktop_backend_spends_the_call(server, db, monkeypatch):
    monkeypatch.delenv("HERMES_DESKTOP", raising=False)
    agent = SimpleNamespace(_session_db=db, session_id="s")

    with patch("agent.understood.generate_understood") as generate:
        assert server._start_turn_understanding("sid", {}, agent, "Do X", []) is None
    generate.assert_not_called()


def test_emits_and_stores_the_line_on_the_turns_user_message(server, db, monkeypatch):
    monkeypatch.setenv("HERMES_DESKTOP", "1")
    db.create_session("s", "tui")
    db.append_message("s", role="user", content="earlier")
    agent = SimpleNamespace(_session_db=db, session_id="s", model="m", provider="p")
    emitted = []

    with patch("agent.understood.generate_understood", return_value="You want X.") as generate, patch.object(
        server, "_emit", side_effect=lambda event, sid, payload=None: emitted.append((event, sid, payload))
    ):
        turn = server._start_turn_understanding(
            "sid", {"session_key": "s"}, agent, "Do X", [{"role": "assistant", "content": "Shall I do X?"}]
        )
        assert turn is not None
        turn.thread.join(2)
        # The agent stores the user message only with the turn.
        db.append_message("s", role="user", content="Do X")
        turn.turn_stored()

    assert generate.call_args.args[:2] == ("Do X", "Shall I do X?")
    assert emitted == [("message.understood", "sid", {"text": "You want X."})]
    rows = [(row["content"], row["understood"]) for row in db.get_messages("s")]
    assert rows == [("earlier", None), ("Do X", "You want X.")]


def test_restates_what_the_user_typed(server):
    # The desktop sends display_text="" for an ordinary message.
    assert server._typed_request("Vergleiche die Optionen", "") == "Vergleiche die Optionen"
    assert server._typed_request("expanded skill prompt …", "/plan the rollout") == "/plan the rollout"
    assert server._typed_request("Do X", None) == "Do X"


def test_display_histories_carry_the_line(server, db):
    db.create_session("s", "tui")
    after = db.max_message_id()
    db.append_message("s", role="user", content="Do X")
    db.append_message("s", role="assistant", content="Done.")
    db.set_turn_understood("s", after, "You want X.")

    messages = server._history_to_messages(server._display_conversation(db, "s"))

    assert messages[0] == {"role": "user", "text": "Do X", "understood": "You want X."}
    assert "understood" not in messages[1]


def test_display_conversation_falls_back_for_minimal_stores(server):
    class MinimalStore:
        def get_messages_as_conversation(self, target, include_ancestors=False):
            return [{"role": "user", "content": target}]

    assert server._display_conversation(MinimalStore(), "s") == [{"role": "user", "content": "s"}]
