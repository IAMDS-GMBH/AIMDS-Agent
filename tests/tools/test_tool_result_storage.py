"""Tests for tools/tool_result_storage.py -- 3-layer tool result persistence."""

import pytest
from unittest.mock import MagicMock, patch

from tools.budget_config import (
    DEFAULT_RESULT_SIZE_CHARS,
    DEFAULT_PREVIEW_SIZE_CHARS,
    BudgetConfig,
)
from tools.tool_result_storage import (
    HEREDOC_MARKER,
    PERSISTED_OUTPUT_TAG,
    PERSISTED_OUTPUT_CLOSING_TAG,
    STORAGE_DIR,
    _build_persisted_message,
    _heredoc_marker,
    _resolve_storage_dir,
    _write_to_sandbox,
    enforce_turn_budget,
    generate_preview,
    maybe_persist_tool_result,
)


# ── generate_preview ──────────────────────────────────────────────────

class TestGeneratePreview:
    def test_short_content_unchanged(self):
        text = "short result"
        preview, has_more = generate_preview(text)
        assert preview == text
        assert has_more is False

    def test_long_content_truncated(self):
        text = "x" * 5000
        preview, has_more = generate_preview(text, max_chars=2000)
        assert len(preview) <= 2000
        assert has_more is True

    def test_truncates_at_newline_boundary(self):
        # 1500 chars + newline + 600 chars  (past halfway)
        text = "a" * 1500 + "\n" + "b" * 600
        preview, has_more = generate_preview(text, max_chars=2000)
        assert preview == "a" * 1500 + "\n"
        assert has_more is True

    def test_ignores_early_newline(self):
        # Newline at position 100, well before halfway of 2000
        text = "a" * 100 + "\n" + "b" * 3000
        preview, has_more = generate_preview(text, max_chars=2000)
        assert len(preview) == 2000
        assert has_more is True

    def test_empty_content(self):
        preview, has_more = generate_preview("")
        assert preview == ""
        assert has_more is False

    def test_exact_boundary(self):
        text = "x" * DEFAULT_PREVIEW_SIZE_CHARS
        preview, has_more = generate_preview(text)
        assert preview == text
        assert has_more is False


# ── _heredoc_marker ───────────────────────────────────────────────────

class TestHeredocMarker:
    def test_default_marker_when_no_collision(self):
        assert _heredoc_marker("normal content") == HEREDOC_MARKER

    def test_uuid_marker_on_collision(self):
        content = f"some text with {HEREDOC_MARKER} embedded"
        marker = _heredoc_marker(content)
        assert marker != HEREDOC_MARKER
        assert marker.startswith("HERMES_PERSIST_")
        assert marker not in content


# ── _write_to_sandbox ─────────────────────────────────────────────────

class TestWriteToSandbox:
    def test_success(self):
        env = MagicMock()
        env.execute.return_value = {"output": "", "returncode": 0}
        result = _write_to_sandbox("hello world", "/tmp/hermes-results/abc.txt", env)
        assert result is True
        env.execute.assert_called_once()
        cmd = env.execute.call_args[0][0]
        assert "mkdir -p" in cmd
        # Content travels through stdin, NOT inside the command string —
        # otherwise large content would hit Linux's 128 KB MAX_ARG_STRLEN
        # ceiling on `bash -c <cmd>` (#22906).
        assert "hello world" not in cmd
        assert env.execute.call_args[1]["stdin_data"] == "hello world"

    def test_failure_returns_false(self):
        env = MagicMock()
        env.execute.return_value = {"output": "error", "returncode": 1}
        result = _write_to_sandbox("content", "/tmp/hermes-results/abc.txt", env)
        assert result is False

    def test_large_content_via_stdin(self):
        """Regression: 200 KB content exceeds Linux MAX_ARG_STRLEN (128 KB).
        It must travel via stdin, never inside the command string."""
        env = MagicMock()
        env.execute.return_value = {"output": "", "returncode": 0}
        big = "x" * 200_000
        _write_to_sandbox(big, "/tmp/hermes-results/big.txt", env)
        cmd = env.execute.call_args[0][0]
        assert len(cmd) < 1_000  # cmd is just `mkdir -p X && cat > Y`
        assert env.execute.call_args[1]["stdin_data"] == big

    def test_timeout_passed(self):
        env = MagicMock()
        env.execute.return_value = {"output": "", "returncode": 0}
        _write_to_sandbox("content", "/tmp/hermes-results/abc.txt", env)
        assert env.execute.call_args[1]["timeout"] == 30

    def test_uses_parent_dir_of_remote_path(self):
        env = MagicMock()
        env.execute.return_value = {"output": "", "returncode": 0}
        remote_path = "/data/data/com.termux/files/usr/tmp/hermes-results/abc.txt"
        _write_to_sandbox("content", remote_path, env)
        cmd = env.execute.call_args[0][0]
        assert "mkdir -p /data/data/com.termux/files/usr/tmp/hermes-results" in cmd

    def test_path_with_spaces_is_quoted(self):
        env = MagicMock()
        env.execute.return_value = {"output": "", "returncode": 0}
        remote_path = "/tmp/hermes results/abc file.txt"
        _write_to_sandbox("content", remote_path, env)
        cmd = env.execute.call_args[0][0]
        assert "'/tmp/hermes results'" in cmd
        assert "'/tmp/hermes results/abc file.txt'" in cmd

    def test_shell_metacharacters_neutralized(self):
        """Paths with shell metacharacters must be quoted to prevent injection."""
        env = MagicMock()
        env.execute.return_value = {"output": "", "returncode": 0}
        malicious_path = "/tmp/hermes-results/$(whoami).txt"
        _write_to_sandbox("content", malicious_path, env)
        cmd = env.execute.call_args[0][0]
        # The $() must not appear unquoted — shlex.quote wraps it
        assert "'/tmp/hermes-results/$(whoami).txt'" in cmd

    def test_semicolon_injection_neutralized(self):
        env = MagicMock()
        env.execute.return_value = {"output": "", "returncode": 0}
        malicious_path = "/tmp/x; rm -rf /; echo .txt"
        _write_to_sandbox("content", malicious_path, env)
        cmd = env.execute.call_args[0][0]
        # The semicolons must be inside quotes, not acting as command separators
        assert "'/tmp/x; rm -rf /; echo .txt'" in cmd


class TestResolveStorageDir:
    def test_defaults_to_storage_dir_without_env(self):
        assert _resolve_storage_dir(None) == STORAGE_DIR

    def test_uses_env_temp_dir_when_available(self):
        env = MagicMock()
        env.get_temp_dir.return_value = "/data/data/com.termux/files/usr/tmp"
        assert _resolve_storage_dir(env) == "/data/data/com.termux/files/usr/tmp/hermes-results"


# ── _build_persisted_message ──────────────────────────────────────────

class TestBuildPersistedMessage:
    def test_structure(self):
        msg = _build_persisted_message(
            preview="first 100 chars...",
            has_more=True,
            original_size=50_000,
            file_path="/tmp/hermes-results/test123.txt",
        )
        assert msg.startswith(PERSISTED_OUTPUT_TAG)
        assert msg.endswith(PERSISTED_OUTPUT_CLOSING_TAG)
        assert "50,000 characters" in msg
        assert "/tmp/hermes-results/test123.txt" in msg
        assert "read_file" in msg
        assert "first 100 chars..." in msg
        assert "..." in msg  # has_more indicator

    def test_no_ellipsis_when_complete(self):
        msg = _build_persisted_message(
            preview="complete content",
            has_more=False,
            original_size=16,
            file_path="/tmp/hermes-results/x.txt",
        )
        # Should not have the trailing "..." indicator before closing tag
        lines = msg.strip().split("\n")
        assert lines[-2] != "..."

    def test_large_size_shows_mb(self):
        msg = _build_persisted_message(
            preview="x",
            has_more=True,
            original_size=2_000_000,
            file_path="/tmp/hermes-results/big.txt",
        )
        assert "MB" in msg


# ── maybe_persist_tool_result ─────────────────────────────────────────

class TestMaybePersistToolResult:
    def test_below_threshold_returns_unchanged(self):
        content = "small result"
        result = maybe_persist_tool_result(
            content=content,
            tool_name="terminal",
            tool_use_id="tc_123",
            env=None,
            threshold=50_000,
        )
        assert result == content

    def test_above_threshold_with_env_persists(self):
        env = MagicMock()
        env.execute.return_value = {"output": "", "returncode": 0}
        content = "x" * 60_000
        result = maybe_persist_tool_result(
            content=content,
            tool_name="terminal",
            tool_use_id="tc_456",
            env=env,
            threshold=30_000,
        )
        assert PERSISTED_OUTPUT_TAG in result
        assert "tc_456.txt" in result
        assert len(result) < len(content)
        env.execute.assert_called_once()

    def test_persists_full_content_as_is(self):
        """Content is persisted verbatim — no JSON extraction."""
        import json
        env = MagicMock()
        env.execute.return_value = {"output": "", "returncode": 0}
        raw = "line1\nline2\n" * 5_000
        content = json.dumps({"output": raw, "exit_code": 0, "error": None})
        result = maybe_persist_tool_result(
            content=content,
            tool_name="terminal",
            tool_use_id="tc_json",
            env=env,
            threshold=30_000,
        )
        assert PERSISTED_OUTPUT_TAG in result
        # Content is delivered through stdin (no longer embedded in the
        # command string — see test_large_content_via_stdin for why).
        assert env.execute.call_args[1]["stdin_data"] == content

    def test_above_threshold_no_env_truncates_inline(self):
        content = "x" * 60_000
        result = maybe_persist_tool_result(
            content=content,
            tool_name="terminal",
            tool_use_id="tc_789",
            env=None,
            threshold=30_000,
        )
        assert PERSISTED_OUTPUT_TAG not in result
        assert "Truncated" in result
        assert len(result) < len(content)

    def test_env_write_failure_falls_back_to_truncation(self):
        env = MagicMock()
        env.execute.return_value = {"output": "disk full", "returncode": 1}
        content = "x" * 60_000
        result = maybe_persist_tool_result(
            content=content,
            tool_name="terminal",
            tool_use_id="tc_fail",
            env=env,
            threshold=30_000,
        )
        assert PERSISTED_OUTPUT_TAG not in result
        assert "Truncated" in result

    def test_env_execute_exception_falls_back(self):
        env = MagicMock()
        env.execute.side_effect = RuntimeError("connection lost")
        content = "x" * 60_000
        result = maybe_persist_tool_result(
            content=content,
            tool_name="terminal",
            tool_use_id="tc_exc",
            env=env,
            threshold=30_000,
        )
        assert "Truncated" in result

    def test_read_file_never_persisted(self):
        """read_file has threshold=inf, should never be persisted."""
        env = MagicMock()
        content = "x" * 200_000
        result = maybe_persist_tool_result(
            content=content,
            tool_name="read_file",
            tool_use_id="tc_rf",
            env=env,
            threshold=float("inf"),
        )
        assert result == content
        env.execute.assert_not_called()

    def test_uses_registry_threshold_when_not_provided(self):
        """When threshold=None, looks up from registry."""
        env = MagicMock()
        env.execute.return_value = {"output": "", "returncode": 0}
        content = "x" * 60_000

        mock_registry = MagicMock()
        mock_registry.get_max_result_size.return_value = 30_000

        with patch("tools.registry.registry", mock_registry):
            result = maybe_persist_tool_result(
                content=content,
                tool_name="terminal",
                tool_use_id="tc_reg",
                env=env,
                threshold=None,
            )
        # Should have persisted since 60K > 30K
        assert PERSISTED_OUTPUT_TAG in result or "Truncated" in result

    def test_unicode_content_survives(self):
        env = MagicMock()
        env.execute.return_value = {"output": "", "returncode": 0}
        content = "日本語テスト " * 10_000  # ~60K chars of unicode
        result = maybe_persist_tool_result(
            content=content,
            tool_name="terminal",
            tool_use_id="tc_uni",
            env=env,
            threshold=30_000,
        )
        assert PERSISTED_OUTPUT_TAG in result
        # Preview should contain unicode
        assert "日本語テスト" in result

    def test_empty_content_returns_unchanged(self):
        result = maybe_persist_tool_result(
            content="",
            tool_name="terminal",
            tool_use_id="tc_empty",
            env=None,
            threshold=30_000,
        )
        assert result == ""

    def test_whitespace_only_below_threshold(self):
        content = " " * 100
        result = maybe_persist_tool_result(
            content=content,
            tool_name="terminal",
            tool_use_id="tc_ws",
            env=None,
            threshold=30_000,
        )
        assert result == content

    def test_file_path_uses_tool_use_id(self):
        env = MagicMock()
        env.execute.return_value = {"output": "", "returncode": 0}
        content = "x" * 60_000
        result = maybe_persist_tool_result(
            content=content,
            tool_name="terminal",
            tool_use_id="unique_id_abc",
            env=env,
            threshold=30_000,
        )
        assert "unique_id_abc.txt" in result

    def test_preview_included_in_persisted_output(self):
        env = MagicMock()
        env.execute.return_value = {"output": "", "returncode": 0}
        # Create content with a distinctive start
        content = "DISTINCTIVE_START_MARKER" + "x" * 60_000
        result = maybe_persist_tool_result(
            content=content,
            tool_name="terminal",
            tool_use_id="tc_prev",
            env=env,
            threshold=30_000,
        )
        assert "DISTINCTIVE_START_MARKER" in result

    def test_env_temp_dir_changes_persisted_path(self):
        env = MagicMock()
        env.execute.return_value = {"output": "", "returncode": 0}
        env.get_temp_dir.return_value = "/data/data/com.termux/files/usr/tmp"
        content = "x" * 60_000
        result = maybe_persist_tool_result(
            content=content,
            tool_name="terminal",
            tool_use_id="tc_termux",
            env=env,
            threshold=30_000,
        )
        assert "/data/data/com.termux/files/usr/tmp/hermes-results/tc_termux.txt" in result
        cmd = env.execute.call_args[0][0]
        assert "mkdir -p /data/data/com.termux/files/usr/tmp/hermes-results" in cmd

    def test_threshold_zero_forces_persist(self):
        env = MagicMock()
        env.execute.return_value = {"output": "", "returncode": 0}
        content = "even short content"
        result = maybe_persist_tool_result(
            content=content,
            tool_name="terminal",
            tool_use_id="tc_zero",
            env=env,
            threshold=0,
        )
        # Any non-empty content with threshold=0 should be persisted
        assert PERSISTED_OUTPUT_TAG in result


# ── enforce_turn_budget ───────────────────────────────────────────────

class TestEnforceTurnBudget:
    def test_under_budget_no_changes(self):
        msgs = [
            {"role": "tool", "tool_call_id": "t1", "content": "small"},
            {"role": "tool", "tool_call_id": "t2", "content": "also small"},
        ]
        result = enforce_turn_budget(msgs, env=None, config=BudgetConfig(turn_budget=200_000))
        assert result[0]["content"] == "small"
        assert result[1]["content"] == "also small"

    def test_over_budget_largest_persisted_first(self):
        env = MagicMock()
        env.execute.return_value = {"output": "", "returncode": 0}
        msgs = [
            {"role": "tool", "tool_call_id": "t1", "content": "a" * 80_000},
            {"role": "tool", "tool_call_id": "t2", "content": "b" * 130_000},
        ]
        # Total 210K > 200K budget
        enforce_turn_budget(msgs, env=env, config=BudgetConfig(turn_budget=200_000))
        # The larger one (130K) should be persisted first
        assert PERSISTED_OUTPUT_TAG in msgs[1]["content"]

    def test_already_persisted_results_skipped(self):
        env = MagicMock()
        env.execute.return_value = {"output": "", "returncode": 0}
        msgs = [
            {"role": "tool", "tool_call_id": "t1",
             "content": f"{PERSISTED_OUTPUT_TAG}\nalready persisted\n{PERSISTED_OUTPUT_CLOSING_TAG}"},
            {"role": "tool", "tool_call_id": "t2", "content": "x" * 250_000},
        ]
        enforce_turn_budget(msgs, env=env, config=BudgetConfig(turn_budget=200_000))
        # t1 should be untouched (already persisted)
        assert msgs[0]["content"].startswith(PERSISTED_OUTPUT_TAG)
        # t2 should be persisted
        assert PERSISTED_OUTPUT_TAG in msgs[1]["content"]

    def test_medium_result_regression(self):
        """6 results of 42K chars each (252K total) — each under 100K default
        threshold but aggregate exceeds 200K budget. L3 should persist."""
        env = MagicMock()
        env.execute.return_value = {"output": "", "returncode": 0}
        msgs = [
            {"role": "tool", "tool_call_id": f"t{i}", "content": "x" * 42_000}
            for i in range(6)
        ]
        enforce_turn_budget(msgs, env=env, config=BudgetConfig(turn_budget=200_000))
        # At least some results should be persisted to get under 200K
        persisted_count = sum(
            1 for m in msgs if PERSISTED_OUTPUT_TAG in m["content"]
        )
        assert persisted_count >= 2  # Need to shed at least ~52K

    def test_no_env_falls_back_to_truncation(self):
        msgs = [
            {"role": "tool", "tool_call_id": "t1", "content": "x" * 250_000},
        ]
        enforce_turn_budget(msgs, env=None, config=BudgetConfig(turn_budget=200_000))
        # Should be truncated (no sandbox available)
        assert "Truncated" in msgs[0]["content"] or PERSISTED_OUTPUT_TAG in msgs[0]["content"]

    def test_returns_same_list(self):
        msgs = [{"role": "tool", "tool_call_id": "t1", "content": "ok"}]
        result = enforce_turn_budget(msgs, env=None, config=BudgetConfig(turn_budget=200_000))
        assert result is msgs

    def test_empty_messages(self):
        result = enforce_turn_budget([], env=None, config=BudgetConfig(turn_budget=200_000))
        assert result == []


# ── Per-tool threshold integration ────────────────────────────────────

class TestPerToolThresholds:
    """Verify registry wiring for per-tool thresholds."""

    def test_registry_has_get_max_result_size(self):
        from tools.registry import registry
        assert hasattr(registry, "get_max_result_size")

    def test_default_threshold(self):
        from tools.registry import registry
        # Unknown tool should return the default
        val = registry.get_max_result_size("nonexistent_tool_xyz")
        assert val == DEFAULT_RESULT_SIZE_CHARS

    def test_terminal_threshold(self):
        from tools.registry import registry
        # Trigger import of terminal_tool to register the tool
        try:
            import tools.terminal_tool  # noqa: F401
            val = registry.get_max_result_size("terminal")
            assert val == 100_000
        except ImportError:
            pytest.skip("terminal_tool not importable in test env")

    def test_read_file_result_size_cap(self):
        from tools.registry import registry
        try:
            import tools.file_tools  # noqa: F401
            val = registry.get_max_result_size("read_file")
            assert val == 100_000
        except ImportError:
            pytest.skip("file_tools not importable in test env")

    def test_read_file_registry_cap_is_100k(self):
        """Regression test: read_file must have a 100_000 char registry cap (Layer 2 safety net)."""
        from tools.registry import registry
        try:
            import tools.file_tools  # noqa: F401
            val = registry.get_max_result_size("read_file")
            assert val == 100_000, (
                f"read_file registry cap must be 100_000, got {val!r}. "
                "float('inf') is not allowed — it disables the Layer 2 result-size guard."
            )
        except ImportError:
            pytest.skip("file_tools not importable in test env")

    def test_search_files_threshold(self):
        from tools.registry import registry
        try:
            import tools.file_tools  # noqa: F401
            val = registry.get_max_result_size("search_files")
            assert val == 100_000
        except ImportError:
            pytest.skip("file_tools not importable in test env")

    def test_maybe_persist_tool_result_auto_ingest_when_env_none(self, tmp_path):
        from tools.tool_result_storage import maybe_persist_tool_result
        from hermes_constants import get_hermes_home
        import sqlite3, json

        large_json_payload = json.dumps([
            {"id": f"item_{i}", "key": "IAMDS-595", "timeSpentSeconds": 3600, "comment": f"Worklog {i}"}
            for i in range(100)
        ])
        assert len(large_json_payload) > 2000

        result = maybe_persist_tool_result(
            content=large_json_payload,
            tool_name="jira_get_worklog",
            tool_use_id="call_no_env",
            env=None,
            threshold=1000,
        )

        assert "[ingested 100 rows → mcp_records" in result and "tool_use_id='call_no_env'" in result
        assert "Note on jira_get_worklog" in result
        assert "Tempo sync bot" in result
        assert "TempoMCP" in result

        # The per-test HERMES_HOME (conftest) — never the developer's real state.db.
        conn = sqlite3.connect(str(get_hermes_home() / "state.db"))
        count = conn.execute("SELECT COUNT(*) FROM mcp_records WHERE tool_use_id = 'call_no_env'").fetchone()[0]
        assert count == 100

    def test_ingest_hint_no_worklog_warning_for_other_tools(self):
        from tools.tool_result_storage import _build_ingest_hint

        hint = _build_ingest_hint("jira_search", 10)
        assert "[ingested 10 rows → mcp_records" in hint
        assert "Note on jira_get_worklog" not in hint

    def test_ingest_hint_names_sql_without_prohibitions(self):
        from tools.tool_result_storage import _build_ingest_hint

        hint = _build_ingest_hint("jira_search", 10)
        assert "`sql`" in hint
        # AIS-289: schema, example query and file-read rule live in the static
        # system prompt now — the per-result hint is one stable line.
        assert "SELECT" not in hint and len(hint) < 200
        assert "DO NOT" not in hint
        assert "ALWAYS" not in hint
        assert "CRITICAL" not in hint

    def test_ingest_hint_is_honest_when_sql_tool_is_absent(self):
        from tools.tool_result_storage import _build_ingest_hint

        hint = _build_ingest_hint("jira_search", 10, sql_available=False)
        assert "[ingested 10 rows → mcp_records" in hint
        assert "not in this session" in hint
        assert "SELECT" not in hint

    def test_maybe_persist_threads_sql_available_into_hint(self):
        import json

        payload = json.dumps([
            {"id": f"item_{i}", "key": "IAMDS-1", "timeSpentSeconds": 60, "comment": f"w {i}"}
            for i in range(5)
        ])
        result = maybe_persist_tool_result(
            content=payload,
            tool_name="jira_search",
            tool_use_id="call_sql_absent",
            env=None,
            sql_available=False,
        )
        assert "not in this session" in result
        assert "SELECT" not in result

    def test_ingest_hint_worklog_warning_for_jira_get_worklog_variants(self):
        from tools.tool_result_storage import _build_ingest_hint

        for name in ["jira_get_worklog", "mcp_AtlassianMCP_jira_get_worklog", "getWorklog"]:
            hint = _build_ingest_hint(name, 5)
            assert "Note on jira_get_worklog" in hint
            assert "Tempo sync bot" in hint
            assert "TempoMCP" in hint


def test_ingested_results_are_queried_not_read_back(tmp_path):
    from tools.tool_result_storage import _build_persisted_message

    with_sql = _build_persisted_message("preview", True, 900000, "/tmp/x.txt", ingest_count=58,
                                        tool_name="mcp_AtlassianMCP_jira_search", sql_available=True)
    assert "Do NOT read the saved file back" in with_sql
    assert "Use the read_file tool" not in with_sql
    without_ingest = _build_persisted_message("preview", True, 900000, "/tmp/x.txt", ingest_count=0,
                                              tool_name="terminal", sql_available=True)
    assert "Use the read_file tool" in without_ingest


class TestIngestedThreshold:
    """Once rows are ingested into mcp_records, a 10K threshold applies (not 100K)."""

    @staticmethod
    def _payload(n):
        import json
        return json.dumps([
            {"id": f"it_{i}", "key": "PROJ-1", "timeSpentSeconds": 3600, "comment": "x" * 400}
            for i in range(n)
        ])

    def test_ingested_payload_is_persisted_above_10k(self):
        content = self._payload(120)  # ~55K — under the 100K default, over the 10K ingested cap
        assert 10_000 < len(content) < DEFAULT_RESULT_SIZE_CHARS
        result = maybe_persist_tool_result(
            content=content, tool_name="mcp_TimeMCP_getWorklogs", tool_use_id="tc_ing_big", env=None,
        )
        assert result != content and "[ingested 120 rows" in result
        assert len(result) < len(content)

    def test_non_ingestible_payload_keeps_the_default_threshold(self):
        content = "plain text " * 5_000  # ~55K, nothing to ingest
        result = maybe_persist_tool_result(
            content=content, tool_name="mcp_TimeMCP_getWorklogs", tool_use_id="tc_plain", env=None,
        )
        assert result == content

    def test_small_ingested_payload_stays_inline_with_hint(self):
        content = self._payload(10)
        assert len(content) < 10_000
        result = maybe_persist_tool_result(
            content=content, tool_name="mcp_TimeMCP_getWorklogs", tool_use_id="tc_ing_small", env=None,
        )
        assert "[ingested 10 rows" in result
        # AIS-289: the inline payload is shaped (sorted keys, compact) but complete
        import json as _json
        shaped = _json.loads(result.split("\n\n[ingested")[0])
        assert len(shaped) == 10 and shaped[0]["key"] == "PROJ-1"


class TestShapingOrder:
    """AIS-289: ingest sees the full payload, the model sees the shaped one."""

    def test_full_rows_ingested_but_inline_list_is_capped(self):
        import json
        import sqlite3
        from hermes_cli.config import get_hermes_home

        payload = json.dumps({
            "@odata.context": "x",
            "value": [{"id": f"r{i}", "key": "PROJ-2", "timeSpentSeconds": 60, "comment": "c"} for i in range(40)],
        })
        result = maybe_persist_tool_result(
            content=payload, tool_name="mcp_TimeMCP_getWorklogs", tool_use_id="tc_shape_40", env=None,
        )
        body = json.loads(result.split("\n\n[ingested")[0])
        assert "@odata.context" not in body
        assert len(body["value"]) == 25 and body["_shaped"]["total"] == 40
        assert "tool_use_id='tc_shape_40'" in body["_shaped"]["full_rows"]
        conn = sqlite3.connect(str(get_hermes_home() / "state.db"))
        assert conn.execute("SELECT COUNT(*) FROM mcp_records WHERE tool_use_id='tc_shape_40'").fetchone()[0] == 40

    def test_shaping_is_deterministic_across_calls(self):
        import json

        payload = json.dumps({"value": [{"b": 1, "a": None, "c": ""}, {"a": 2, "b": 1}]})
        one = maybe_persist_tool_result(content=payload, tool_name="mcp_X_list", tool_use_id="tc_det", env=None)
        two = maybe_persist_tool_result(content=payload, tool_name="mcp_X_list", tool_use_id="tc_det", env=None)
        assert one == two
        assert one.split("\n\n[ingested")[0] == '{"value":[{"b":1},{"a":2,"b":1}]}'

    def test_non_mcp_tool_result_is_not_shaped(self):
        content = '{"@odata.context": "x", "value": []}'
        assert maybe_persist_tool_result(content=content, tool_name="terminal", tool_use_id="tc_term", env=None) == content
