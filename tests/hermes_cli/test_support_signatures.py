"""AIS-344 (B2): the incident digest — signature hits with context, counted
per signature, deterministic, from the recent part of every log."""

from __future__ import annotations

from datetime import datetime, timezone

from hermes_cli import support_signatures as sig

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)


def _write(log_dir, name, text):
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / name).write_text(text, encoding="utf-8")


def test_catalog_is_unique_and_covers_the_known_failures():
    assert len(sig.SIGNATURE_IDS) == len(set(sig.SIGNATURE_IDS))
    assert {"boot.port_in_use", "boot.backend_exited_before_ready", "boot.backend_not_ready", "update.handoff",
            "update.failed", "update.web_build_failed", "mcp.server_start_failed", "mcp.tools_missing",
            "graph.403_consent", "graph.group_uri", "graph.400_invalid_id", "worktime.profile_unknown",
            "python.traceback", "generic.error"} <= set(sig.SIGNATURE_IDS)
    # Every signature carries a human title + hint and a known severity.
    for s in sig.SIGNATURES:
        assert s.title and s.hint and s.severity in {"high", "medium", "low", "info"}


def test_classify_text_prefers_the_specific_signature_over_generic_error():
    assert sig.classify_text("ERROR: [Errno 48] address already in use").id == "boot.port_in_use"
    assert sig.classify_text("[WinError 10048] / [Errno 10048] error while attempting to bind").id == "boot.port_in_use"
    assert sig.classify_text("Hermes backend exited before it became ready (1).").id == "boot.backend_exited_before_ready"
    assert sig.classify_text("Graph error: ErrorGroupIsUsedInNonGroupURI").id == "graph.group_uri"
    assert sig.classify_text("ERROR something else").id == "generic.error"
    assert sig.classify_text("all good") is None


def test_digest_counts_signatures_with_context_and_dedupes_repeats(tmp_path):
    log_dir = tmp_path / "logs"
    lines = ["2026-09-15 11:13:00,000 INFO boot: starting"]
    for i in range(6):
        lines.append(f"2026-09-15 11:13:0{i} INFO uvicorn: attempt {i}")
        lines.append(f"2026-09-15 11:13:0{i} ERROR uvicorn: [Errno 48] address already in use 127.0.0.1:912{i}")
    lines.append("2026-09-15 11:14:00 INFO boot: Hermes backend exited before it became ready (1).")
    _write(log_dir, "agent.log", "\n".join(lines) + "\n")

    digest = sig.build_incident_digest(log_dir, now=NOW)

    by_id = {s["id"]: s for s in digest.signals}
    assert by_id["boot.port_in_use"]["count"] == 6
    assert by_id["boot.backend_exited_before_ready"]["count"] == 1
    assert by_id["boot.port_in_use"]["first"] == "2026-09-15T11:13:00"
    assert by_id["boot.port_in_use"]["last"] == "2026-09-15T11:13:05"
    assert by_id["boot.port_in_use"]["files"] == ["agent.log"]
    # High severity first, then count.
    assert [s["id"] for s in digest.signals][:2] == ["boot.port_in_use", "boot.backend_exited_before_ready"]
    # Context lines around the hit, hit marked with its signature id.
    assert "[boot.port_in_use]" in digest.text
    assert "INFO uvicorn: attempt 0" in digest.text
    # Repeats beyond three are not listed separately; the ones that fall into
    # the context of an earlier hit still carry their marker, the rest fold.
    assert 3 <= digest.text.count("[boot.port_in_use]") <= 6
    assert digest.files == [{"name": "agent.log", "hits": 7, "lines_scanned": 14}]
    assert digest.top_signature == "boot.port_in_use"
    # Deterministic.
    assert sig.build_incident_digest(log_dir, now=NOW).text == digest.text
    # Twelve more identical hits fold into one "… ×N more" line.
    for i in range(12):
        lines.append(f"2026-09-15 11:20:{i:02d} ERROR uvicorn: [Errno 48] address already in use 127.0.0.1:9130")
    _write(log_dir, "agent.log", "\n".join(lines) + "\n")
    folded = sig.build_incident_digest(log_dir, now=NOW)
    assert "address already in use" in folded.text
    assert "more" in folded.text and "×" in folded.text
    assert folded.signals[0]["count"] == 18


def test_digest_only_reads_the_time_window_of_timestamped_logs(tmp_path):
    log_dir = tmp_path / "logs"
    _write(
        log_dir,
        "errors.log",
        "2026-09-14 08:00:00 ERROR old: Traceback (most recent call last):\n"
        "2026-09-15 11:50:00 ERROR new: Traceback (most recent call last):\n"
        "  File \"x.py\", line 1\n"
        "ValueError: boom\n",
    )
    digest = sig.build_incident_digest(log_dir, now=NOW, window_minutes=60)
    by_id = {s["id"]: s for s in digest.signals}
    assert by_id["python.traceback"]["count"] == 1
    assert "old:" not in digest.text
    # Continuation lines inherit the previous timestamp and appear as context.
    assert "ValueError: boom" in digest.text


def test_digest_scans_the_tail_of_logs_without_timestamps(tmp_path):
    log_dir = tmp_path / "logs"
    body = ["[hermes] [boot] Resolving Hermes backend", "[hermes] ERROR: [Errno 48] address already in use", "[hermes] Desktop boot failed: ECONNREFUSED"] * 4
    body += ["[hermes] [boot] Resolving Hermes backend", "[hermes] Hermes backend is ready"]
    _write(log_dir, "desktop.log", "\n".join(body) + "\n")
    digest = sig.build_incident_digest(log_dir, now=NOW)
    by_id = {s["id"]: s for s in digest.signals}
    # The failed boots *before* the working one are the restart-hang evidence.
    assert by_id["boot.port_in_use"]["count"] == 4
    assert by_id["boot.backend_not_ready"]["count"] == 4
    assert by_id["boot.port_in_use"]["first"] is None  # no timestamps to report
    assert sig.last_boot_section(body) == ["[hermes] [boot] Resolving Hermes backend", "[hermes] Hermes backend is ready"]


def test_digest_respects_line_budget_and_redacts(tmp_path):
    log_dir = tmp_path / "logs"
    _write(log_dir, "agent.log", "".join(f"2026-09-15 11:5{i % 10}:00 ERROR token=sk-abcdefghijklmnopqrstuv step {i}\n" for i in range(50)))
    digest = sig.build_incident_digest(
        log_dir, now=NOW, max_lines=12, max_repeats=100, redact=lambda s: s.replace("sk-abcdefghijklmnopqrstuv", "***")
    )
    body_lines = [line for line in digest.text.splitlines() if line.startswith("  ")]
    assert len(body_lines) <= 14  # budget + truncation marker + fold line
    assert "sk-abcdefghijklmnopqrstuv" not in digest.text
    assert "truncated to the newest hits" in digest.text
    assert digest.signals[0]["count"] == 50


def test_digest_without_hits_or_logs(tmp_path):
    log_dir = tmp_path / "logs"
    _write(log_dir, "agent.log", "2026-09-15 11:59:00 INFO all fine\n")
    digest = sig.build_incident_digest(log_dir, now=NOW)
    assert digest.signals == [] and digest.files == []
    assert "none (no signature matched" in digest.text
    empty = sig.build_incident_digest(tmp_path / "missing", now=NOW)
    assert empty.signals == []


def test_signal_summary_is_compact():
    digest_signals = [
        {"id": "boot.port_in_use", "count": 3, "severity": "high", "title": "t", "hint": "long hint", "first": "a", "last": "b", "files": ["desktop.log"]},
    ]
    out = sig.signal_summary(digest_signals)
    assert out == [{"id": "boot.port_in_use", "count": 3, "severity": "high", "title": "t", "first": "a", "last": "b", "files": ["desktop.log"]}]
    assert "hint" not in out[0]
