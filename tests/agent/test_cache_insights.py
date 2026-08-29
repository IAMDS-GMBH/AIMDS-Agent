"""agent/cache_insights — hit rate, steady-state rate and served-model switches per session."""

from __future__ import annotations

from agent.cache_insights import cache_report, format_cache_report, session_stats


def _call(sid, ts, inp, read, write, served="claude-haiku-4.5", latency=1000):
    return {"session_id": sid, "ts": ts, "id": int(ts), "input_tokens": inp, "cache_read_tokens": read,
            "cache_write_tokens": write, "served_model": served, "latency_ms": latency}


class TestSessionStats:
    def test_first_call_is_excluded_from_the_steady_state_rate(self):
        calls = [_call("s", 1, 20_000, 0, 18_000), _call("s", 2, 1_000, 38_000, 800), _call("s", 3, 900, 39_000, 700)]
        s = session_stats(calls)
        assert s["calls"] == 3 and s["model_switches"] == 0
        assert s["hit_pct"] < s["steady_hit_pct"] and s["steady_hit_pct"] > 90
        assert s["reasons"] == []

    def test_model_switch_is_named(self):
        calls = [_call("s", 1, 20_000, 0, 18_000), _call("s", 2, 21_000, 0, 19_000, served="gemini-3.6-flash"),
                 _call("s", 3, 22_000, 0, 20_000, served="claude-haiku-4.5")]
        s = session_stats(calls)
        assert s["model_switches"] == 2
        assert any("served model changed 2×" in r and "gemini-3.6-flash" in r for r in s["reasons"])
        assert any("more cache writes than reads" in r for r in s["reasons"])

    def test_two_call_session_is_explained(self):
        s = session_stats([_call("s", 1, 20_000, 0, 18_000), _call("s", 2, 5_000, 18_000, 0)])
        assert any("only 1–2 calls" in r for r in s["reasons"])


class _Db:
    def __init__(self, rows):
        self.rows = rows

    def get_api_calls(self, session_id=None, days=None):
        return [r for r in self.rows if not session_id or r["session_id"] == session_id]


def test_report_and_formatting():
    rows = [_call("good", 1, 20_000, 0, 18_000), _call("good", 2, 1_000, 38_000, 800), _call("good", 3, 900, 39_000, 700),
            _call("bad", 10, 20_000, 0, 18_000), _call("bad", 11, 21_000, 0, 19_000, served="gemini-3.6-flash"),
            _call("bad", 12, 22_000, 0, 20_000, served="claude-haiku-4.5")]
    report = cache_report(_Db(rows), days=7)
    assert report["calls"] == 6 and len(report["sessions"]) == 2
    assert report["sessions_with_switches"] == 1
    assert report["worst"][0]["session_id"] == "bad"
    text = format_cache_report(report)
    assert "worst sessions" in text and "bad" in text and "served model changed" in text

    one = cache_report(_Db(rows), session_id="good")
    assert one["session_id"] == "good" and one["calls"] == 3
    assert "steady" in format_cache_report(one)
    assert "no api_calls rows yet" in format_cache_report(cache_report(_Db([]), days=7))
