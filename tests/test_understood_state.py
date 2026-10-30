"""SessionDB storage of the per-turn "understood" line (AIS-789).

The line lives on the user message's row, so it must survive what happens to
a conversation after the turn: a reload (display histories carry it, the
agent's own history never does), a transcript rewrite (replace_messages), a
compression rotation (the continuation replays recent user messages as new
rows) and the transcript sync (no re-send, user rows are never slimmed).
"""

import time

import pytest

from hermes_state import SessionDB


@pytest.fixture()
def db(tmp_path):
    session_db = SessionDB(db_path=tmp_path / "state.db")
    yield session_db
    session_db.close()


def _understood_rows(db, session_id):
    return [
        (row["content"], row["understood"])
        for row in db.get_messages(session_id)
        if row["role"] == "user"
    ]


def _rotate(db, parent, child):
    """A compression continuation: the parent ended, the child started after."""
    now = time.time()
    db.end_session(parent, end_reason="compression")
    db._conn.execute("UPDATE sessions SET ended_at = ? WHERE id = ?", (now - 10, parent))
    db.create_session(session_id=child, source="tui", parent_session_id=parent)
    db._conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?", (now - 5, child))


class TestSetTurnUnderstood:
    def test_targets_the_first_user_message_after_the_turn_began(self, db):
        db.create_session("s", "tui")
        db.append_message("s", role="user", content="earlier")
        db.append_message("s", role="assistant", content="answer")
        after = db.max_message_id()

        assert db.set_turn_understood("s", after, "You want X.") is False  # row not there yet

        db.append_message("s", role="user", content="Do X")
        db.append_message("s", role="assistant", content="Done")
        assert db.set_turn_understood("s", after, "You want X.") is True
        assert _understood_rows(db, "s") == [("earlier", None), ("Do X", "You want X.")]

    def test_finds_the_row_in_a_continuation_and_marks_its_replays(self, db):
        db.create_session("root", "tui")
        after = db.max_message_id()
        db.append_message("root", role="user", content="Do X")
        _rotate(db, "root", "tip")
        db.append_message("tip", role="user", content="Do X")  # the compacted replay

        # Called with the key from before the rotation: the tip is found too.
        assert db.set_turn_understood("root", after, "You want X.") is True
        assert _understood_rows(db, "root") == [("Do X", "You want X.")]
        assert _understood_rows(db, "tip") == [("Do X", "You want X.")]

    def test_the_turn_line_wins_over_an_inherited_one(self, db):
        db.create_session("s", "tui")
        db.append_message("s", role="user", content="ja")
        db._conn.execute("UPDATE messages SET understood = 'old'")
        after = db.max_message_id()
        db.append_message("s", role="user", content="ja")
        db._conn.execute("UPDATE messages SET understood = 'inherited' WHERE id = ?", (db.max_message_id(),))

        assert db.set_turn_understood("s", after, "You confirm option B.") is True
        assert _understood_rows(db, "s") == [("ja", "old"), ("ja", "You confirm option B.")]


class TestSurvival:
    def test_display_history_carries_it_the_agent_history_does_not(self, db):
        db.create_session("s", "tui")
        after = db.max_message_id()
        db.append_message("s", role="user", content="Do X")
        db.set_turn_understood("s", after, "You want X.")

        display = db.get_messages_as_conversation("s", include_ancestors=True, include_understood=True)
        agent_history = db.get_messages_as_conversation("s")

        assert display[0]["understood"] == "You want X."
        assert "understood" not in agent_history[0]
        assert db.get_messages("s")[0]["understood"] == "You want X."  # REST transcript

    def test_replace_messages_keeps_lines_of_unchanged_user_messages(self, db):
        db.create_session("s", "tui")
        db.append_message("s", role="user", content="ja")
        db.append_message("s", role="assistant", content="ok")
        db.append_message("s", role="user", content="ja")
        db.append_message("s", role="user", content="gone")
        rows = db.get_messages("s")
        db._conn.execute("UPDATE messages SET understood = 'first' WHERE id = ?", (rows[0]["id"],))
        db._conn.execute("UPDATE messages SET understood = 'second' WHERE id = ?", (rows[2]["id"],))
        db._conn.execute("UPDATE messages SET understood = 'third' WHERE id = ?", (rows[3]["id"],))

        db.replace_messages(
            "s",
            [
                {"role": "user", "content": "ja"},
                {"role": "assistant", "content": "ok"},
                {"role": "user", "content": "ja"},
                {"role": "user", "content": "new"},
            ],
        )

        assert _understood_rows(db, "s") == [("ja", "first"), ("ja", "second"), ("new", None)]

    def test_inherit_copies_lines_onto_replayed_user_messages(self, db):
        db.create_session("root", "tui")
        db.append_message("root", role="user", content="Do X")
        db.append_message("root", role="user", content="Do Y")
        db._conn.execute("UPDATE messages SET understood = 'You want ' || substr(content, 4) || '.'")
        _rotate(db, "root", "tip")
        db.append_message("tip", role="user", content="Do Y")  # replayed
        db.append_message("tip", role="user", content="Do Z")  # new turn

        assert db.inherit_understood("tip") == 1
        assert _understood_rows(db, "tip") == [("Do Y", "You want Y."), ("Do Z", None)]
        assert db.inherit_understood("root") == 0  # nothing left, either end of the chain

    def test_writing_the_line_does_not_resend_the_transcript(self, db):
        db.create_session("s", "tui")
        after = db.max_message_id()
        db.append_message("s", role="user", content="Do X")
        db._conn.execute("UPDATE sessions SET sync_dirty = 0, sync_gen = 0")

        db.set_turn_understood("s", after, "You want X.")

        row = db._conn.execute("SELECT sync_dirty, sync_gen FROM sessions WHERE id = 's'").fetchone()
        assert tuple(row) == (0, 0)
