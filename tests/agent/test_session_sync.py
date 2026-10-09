"""Transcript sync to the Suite memory (AIS-469)."""

import json
import time

import pytest

from agent import session_sync
from hermes_state import SessionDB


class FakeRemote:
    """Records memory_session calls and keeps a server-like transcript."""

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail
        self.sessions = {}  # remote id -> {message_id: content}, ordered by first append

    def call(self, args):
        self.calls.append(json.loads(json.dumps(args)))
        if self.fail:
            raise RuntimeError("offline")
        sid = args["session_id"]
        store = self.sessions.setdefault(sid, {})
        if args["action"] == "append":
            for m in args["messages"]:
                store[m["message_id"]] = m["content"]
        elif args["action"] == "truncate":
            keys = list(store)
            if args["from_message_id"] in keys:
                for k in keys[keys.index(args["from_message_id"]):]:
                    del store[k]
        elif args["action"] == "delete":
            self.sessions.pop(sid, None)
        elif args["action"] == "read":
            # Like the server: long messages come back as overlapping parts.
            chunks = []
            for mid, text in store.items():
                size, overlap, start, part = 1600, 200, 0, 0
                while True:
                    chunks.append({"message_id": mid, "part": part, "content": text[start:start + size].strip()})
                    if start + size >= len(text):
                        break
                    start, part = start + size - overlap, part + 1
            return {"chunks": chunks}
        return {"ok": True}

    def appended_ids(self):
        return [m["message_id"] for c in self.calls if c["action"] == "append" for m in c["messages"]]


@pytest.fixture()
def db(tmp_path):
    d = SessionDB(tmp_path / "state.db")
    yield d
    d.close()


def chat(db, sid, msgs, source="tui", title=None, **kw):
    db.create_session(sid, source, **kw)
    if title:
        db._execute_write(lambda c: c.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, sid)))
    for role, content in msgs:
        db.append_message(sid, role, content)


def run(db, remote):
    return session_sync.run_once(db, remote)


def test_appends_new_messages_once_and_incrementally(db):
    chat(db, "s1", [("user", "Plane den Mail-Umzug"), ("assistant", "Drei Wellen")], title="Umzug")
    r = FakeRemote()
    stats = run(db, r)
    assert stats["sessions"] == 1 and stats["messages"] == 2
    assert r.calls[0]["session_id"] == "s1" and r.calls[0]["title"] == "Umzug"
    assert r.appended_ids() == ["s1:0", "s1:1"]

    # Nothing changed: the session is clean and not picked again.
    assert run(db, r)["sessions"] == 0

    db.append_message("s1", "user", "Und die Kalender?")
    run(db, r)
    assert r.appended_ids()[-1] == "s1:2"
    assert len(r.appended_ids()) == 3  # only the new message was sent


def test_edit_and_rewind_resend_and_truncate(db):
    chat(db, "s2", [("user", "a"), ("assistant", "b"), ("user", "c"), ("assistant", "d")])
    r = FakeRemote()
    run(db, r)
    rows = db.sync_messages("s2")

    # Rewind from the 3rd message: the remote tail must go.
    db.rewind_to_message("s2", rows[2]["id"])
    run(db, r)
    trunc = [c for c in r.calls if c["action"] == "truncate"]
    assert trunc and trunc[-1]["from_message_id"] == "s2:2"
    assert list(r.sessions["s2"]) == ["s2:0", "s2:1"]

    # Edit in place: re-sent with the same ids (the server replaces it).
    db._execute_write(lambda c: c.execute("UPDATE messages SET content = 'b2' WHERE id = ?", (rows[1]["id"],)))
    run(db, r)
    assert r.sessions["s2"]["s2:1"] == "b2"


def test_replace_messages_with_same_content_keeps_ids(db):
    chat(db, "s3", [("user", "x"), ("assistant", "y")])
    r = FakeRemote()
    run(db, r)
    db.replace_messages("s3", [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}])
    run(db, r)
    assert not [c for c in r.calls if c["action"] == "truncate"]
    assert list(r.sessions["s3"]) == ["s3:0", "s3:1"]


def test_tool_output_capped_redacted_and_summaries_skipped(db):
    from agent.context_compressor import SUMMARY_PREFIX

    db.create_session("s4", "tui")
    db.append_message("s4", "user", SUMMARY_PREFIX + " earlier context …")
    db.append_message("s4", "assistant", "", tool_calls=[{"function": {"name": "mail_search", "arguments": "{}"}}])
    db.append_message("s4", "tool", "x" * 5000 + " data:image/png;base64," + "A" * 200, tool_name="mail_search")
    db.append_message("s4", "user", [{"type": "text", "text": "Was ist das?"}, {"type": "image_url", "image_url": {"url": "data:..."}}])
    r = FakeRemote()
    run(db, r)
    sent = {m["message_id"]: m for c in r.calls for m in c["messages"]}
    assert "s4:0" not in sent  # compaction summary
    assert sent["s4:1"]["content"] == "[tools: mail_search]"
    tool = sent["s4:2"]["content"]
    assert tool.startswith("[mail_search] ") and "chars omitted" in tool and len(tool) < 2200
    assert sent["s4:3"]["content"] == "Was ist das?\n[image]"


def test_compression_chain_shares_the_root_transcript_in_order(db):
    chat(db, "root", [("user", "Teil 1")], title="Projekt")
    db.end_session("root", "compression")
    time.sleep(0.01)
    chat(db, "tip", [("user", "Teil 2")], title="Projekt #2", parent_session_id="root")
    r = FakeRemote()
    run(db, r)
    assert {c["session_id"] for c in r.calls} == {"root"}
    assert r.appended_ids() == ["root:0", "tip:0"]
    assert db.sync_remote_id("tip") == "root"


def test_cron_and_subagent_sessions_are_not_synced(db):
    chat(db, "cronjob", [("user", "brief")], source="cron")
    chat(db, "parent", [("user", "do it")], title="Chat")
    chat(db, "child", [("user", "subtask")], parent_session_id="parent")  # untitled delegate run
    r = FakeRemote()
    run(db, r)
    assert {c["session_id"] for c in r.calls} == {"parent"}


def test_user_delete_is_sent_but_prune_is_not(db):
    chat(db, "gone", [("user", "weg damit")])
    chat(db, "old", [("user", "alt")])
    r = FakeRemote()
    run(db, r)
    db.delete_sessions(["gone"], whole_conversation=True)
    run(db, r)
    assert {"action": "delete", "session_id": "gone"} in [{k: c[k] for k in ("action", "session_id")} for c in r.calls]
    assert "gone" not in r.sessions

    db._execute_write(lambda c: c.execute("UPDATE sessions SET ended_at = ?, started_at = ? WHERE id = 'old'",
                                          (time.time() - 400 * 86400, time.time() - 400 * 86400)))
    db._execute_write(lambda c: c.execute("UPDATE messages SET timestamp = ? WHERE session_id = 'old'",
                                          (time.time() - 400 * 86400,)))
    assert db.prune_sessions(older_than_days=90) == 1
    run(db, r)
    assert "old" in r.sessions, "local retention must keep the remote copy"


def test_failure_backs_off_and_keeps_the_session_dirty(db):
    chat(db, "s5", [("user", "hallo")])
    down = FakeRemote(fail=True)
    stats = run(db, down)
    assert stats["errors"] == 1
    assert db.sync_due_sessions() == []  # backing off
    status = db.sync_status()
    assert status["pending"] == 1 and status["failing"] == 1

    db._execute_write(lambda c: c.execute("UPDATE sessions SET sync_next_at = 0"))
    up = FakeRemote()
    run(db, up)
    assert up.appended_ids() == ["s5:0"]
    assert db.sync_status()["failing"] == 0


def test_backfill_marks_existing_chats_once(db):
    chat(db, "legacy", [("user", "vor dem Update")])
    db._execute_write(lambda c: c.execute("UPDATE sessions SET sync_dirty = 0"))
    assert db.sync_mark_backfill() == 1
    assert db.sync_mark_backfill() == 0
    r = FakeRemote()
    run(db, r)
    assert r.appended_ids() == ["legacy:0"]


def test_maybe_run_without_memory_session_tool_is_a_noop(db, monkeypatch):
    monkeypatch.setattr(session_sync, "_last_run", 0.0)
    monkeypatch.setattr(session_sync.SessionRemote, "for_process", classmethod(lambda cls: None))
    chat(db, "s6", [("user", "x")])
    assert session_sync.maybe_run(db) is None
    assert db.sync_status()["pending"] == 1  # kept for later


def test_maybe_run_respects_the_setting(db, monkeypatch):
    monkeypatch.setattr(session_sync, "_last_run", 0.0)
    monkeypatch.setattr(session_sync, "enabled", lambda: False)
    called = []
    monkeypatch.setattr(session_sync.SessionRemote, "for_process", classmethod(lambda cls: called.append(1)))
    assert session_sync.maybe_run(db) is None and not called


def test_session_summary_lands_on_the_synced_transcript(monkeypatch):
    from agent import memory_facade as mf

    facade = mf.MemoryFacade(mode=mf.MODE_MCP, valid_tool_names={"memory_summarize_session", "memory_save"})
    sent = {}
    monkeypatch.setattr(facade, "_call", lambda tool, args: sent.update(args) or {"result": {"slug": "session-root", "saved": True}})
    monkeypatch.setattr(mf, "_remote_session_id", lambda sid: "root" if sid == "tip" else sid)
    res = facade.summarize_session(summary="Umzug geplant", session_id="tip")
    assert res.ok and sent["session_id"] == "root"


# ── slimming + hydration ────────────────────────────────────────────────


def _age(db, sid, days):
    old = time.time() - days * 86400
    db._execute_write(lambda c: c.execute("UPDATE messages SET timestamp = ? WHERE session_id = ?", (old, sid)))


def _long_chat(db, sid, n_pairs=15):
    db.create_session(sid, "tui")
    for i in range(n_pairs):
        db.append_message(sid, "user", f"Frage {i}")
        db.append_message(sid, "assistant", f"Antwort {i}: " + ("Details zur Migration der Postfächer. " * 90))


def test_slim_replaces_old_long_messages_and_hydrate_restores_them(db):
    _long_chat(db, "s7")
    r = FakeRemote()
    run(db, r)
    original = {m["id"]: m["content"] for m in db.sync_messages("s7")}
    _age(db, "s7", 40)

    n = session_sync.slim_once(db, after_days=30)
    rows = db.sync_messages("s7")
    slimmed = [m for m in rows if str(m["content"]).startswith(session_sync.MARKER_PREFIX)]
    # 30 messages, last 20 kept, of the first 10 only the 5 long assistant ones go.
    assert n == 5 and len(slimmed) == 5
    assert all(m["role"] == "assistant" for m in slimmed)
    assert all(not str(m["content"]).startswith(session_sync.MARKER_PREFIX) for m in rows[-20:])
    assert "Details zur Migration" in slimmed[0]["content"]  # the hint
    assert "Suite" in slimmed[0]["content"]  # "… [N more characters in the Suite memory]" (display language)

    # Slimming is not a change to sync: the session stays clean, nothing is re-sent.
    calls_before = len(r.calls)
    assert db.sync_due_sessions() == [] and run(db, r)["messages"] == 0 and len(r.calls) == calls_before

    restored = session_sync.hydrate_messages([dict(m) for m in rows], remote=r)
    for m in restored:
        assert " ".join(m["content"].split()) == " ".join(original[m["id"]].split())


def test_markers_are_never_sent_even_after_a_rewrite(db):
    _long_chat(db, "s8", n_pairs=12)
    r = FakeRemote()
    run(db, r)
    _age(db, "s8", 40)
    session_sync.slim_once(db, after_days=30)
    full_before = dict(r.sessions["s8"])

    # A rewrite that carries the markers (e.g. replace_messages while offline).
    rows = db.sync_messages("s8")
    db.replace_messages("s8", [{"role": m["role"], "content": m["content"]} for m in rows])
    run(db, r)
    sent = [m for c in r.calls for m in c.get("messages", [])]
    assert not any(m["content"].startswith(session_sync.MARKER_PREFIX) for m in sent)
    assert r.sessions["s8"] == full_before, "the server copy must keep the full text"


def test_no_slimming_for_recent_or_unsynced_chats(db):
    _long_chat(db, "recent")
    _long_chat(db, "unsynced")
    _age(db, "unsynced", 40)
    run(db, FakeRemote(fail=True))  # unsynced stays dirty
    _long_chat(db, "synced_recent")
    assert session_sync.slim_once(db, after_days=30) == 0
    assert session_sync.slim_once(db, after_days=0) == 0


def test_hydrate_offline_keeps_the_hint(db):
    msgs = [{"role": "assistant", "content": session_sync.marker("root", "s:3", "Lange Antwort " * 50)}]
    out = session_sync.hydrate_messages(msgs, remote=FakeRemote(fail=True))
    assert not out[0]["content"].startswith(session_sync.MARKER_PREFIX)
    assert out[0]["content"].startswith("Lange Antwort") and "Suite" in out[0]["content"]


class HangingRemote(FakeRemote):
    """memory_session that times out like the facade does (AIS-524)."""

    def call(self, args):
        self.calls.append(json.loads(json.dumps(args)))
        raise TimeoutError("mcp_AIMDSSuiteMCP_mcp_memory_memory_session timed out after 60s")


def test_a_timeout_stops_the_run_and_leaves_the_rest_due(db):
    for sid in ("t1", "t2", "t3"):
        chat(db, sid, [("user", f"hallo {sid}")])
    hanging = HangingRemote()
    stats = run(db, hanging)

    assert len(hanging.calls) == 1, "one timeout per run, not one per session"
    assert stats["errors"] == 1 and stats["stopped"] == 1
    assert len(db.sync_due_sessions()) == 2  # untouched sessions go out next tick

    up = FakeRemote()
    run(db, up)
    assert sorted(up.appended_ids()) == ["t2:0", "t3:0"]


def test_a_plain_failure_does_not_stop_the_run(db):
    for sid in ("f1", "f2"):
        chat(db, sid, [("user", "hallo")])
    down = FakeRemote(fail=True)
    stats = run(db, down)
    assert stats["errors"] == 2 and "stopped" not in stats
