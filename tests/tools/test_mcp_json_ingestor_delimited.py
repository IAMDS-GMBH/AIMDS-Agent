"""Delimited plain-text MCP results must become rows, not one blob.

`mcp_records` exists so agents can aggregate tool output with SQL instead of
writing throwaway parsing scripts — SOUL forbids the latter outright. But
several MCP servers answer in human-readable text rather than JSON. TempoMCP
returns worklogs as `TempoWorklogId: … | IssueKey: … | Hours: …`, one per
line. `json.loads` failed on that, so a 96k-character payload landed as a
single opaque row, SQL could not touch it, and the agent fell back to Python
— concluding, not unreasonably, "too big for SQL parsing".
"""

from __future__ import annotations

import pytest

from tools.mcp_json_ingestor import _extract_items


TEMPO = (
    "TempoWorklogId: 43011 | IssueKey: IAMDS-595 | Date: 2026-01-02 | Hours: 1.00\n"
    "TempoWorklogId: 43012 | IssueKey: AIS-83 | Date: 2026-01-03 | Hours: 2.50\n"
    "TempoWorklogId: 43013 | IssueKey: AIS-83 | Date: 2026-01-04 | Hours: 0.50"
)


class TestDelimitedTextBecomesRows:
    def test_each_line_becomes_its_own_record(self):
        rows = _extract_items({"result": TEMPO})

        assert len(rows) == 3
        assert rows[0]["IssueKey"] == "IAMDS-595"
        assert rows[2]["Hours"] == "0.50"

    def test_values_keep_their_content(self):
        rows = _extract_items({"result": "Id: 1 | Description: Arbeit an Vorgang | Hours: 1.00"})

        assert rows[0]["Description"] == "Arbeit an Vorgang"

    def test_json_results_are_unaffected(self):
        rows = _extract_items({"result": '[{"id": 1}, {"id": 2}]'})

        assert [r["id"] for r in rows] == [1, 2]

    def test_known_collection_keys_still_win(self):
        rows = _extract_items({"worklogs": [{"id": 7}]})

        assert rows == [{"id": 7}]


class TestProseIsNotShredded:
    def test_free_text_falls_back_to_a_single_record(self):
        payload = {"result": "Das ist Fliesstext ohne Struktur.\nZweite Zeile, auch ohne."}

        rows = _extract_items(payload)

        assert rows == [payload]

    def test_a_stack_trace_is_not_parsed_into_rows(self):
        payload = {
            "result": (
                "Traceback (most recent call last):\n"
                '  File "x.py", line 1, in <module>\n'
                "ValueError: boom"
            )
        }

        assert _extract_items(payload) == [payload]

    def test_a_single_structured_line_is_not_enough_to_shred_a_block(self):
        payload = {
            "result": (
                "Hier ist ein Bericht ueber die Lage.\n"
                "Id: 1 | Hours: 2\n"
                "Weiterer Fliesstext folgt hier.\n"
                "Und noch eine Zeile Prosa."
            )
        }

        # Only 1 of 4 lines parses — below the acceptance ratio.
        assert _extract_items(payload) == [payload]

    @pytest.mark.parametrize("text", ["", "   \n  \n"])
    def test_empty_payloads_are_safe(self, text):
        payload = {"result": text}

        assert _extract_items(payload) == [payload]
