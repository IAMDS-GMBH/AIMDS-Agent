"""tools/mcp_result_shaper.py (AIS-289): MCP JSON results are shaped before
they reach the model — noise keys and empty values dropped, item lists and
long strings capped, keys sorted — and the shaping is deterministic."""

import json

import pytest

from tools.mcp_result_shaper import (
    SHAPED_KEY,
    ShapeConfig,
    shape_tool_result,
)

CFG = ShapeConfig()  # defaults, no config.yaml lookup

GRAPH_LISTING = {
    "@odata.context": "https://graph.microsoft.com/v1.0/$metadata#users('x')/drive/root/children",
    "@odata.count": 3,
    "value": [
        {
            "@odata.etag": "\"{ABC},1\"",
            "id": "01ABC",
            "name": "Umzugsplan.docx",
            "eTag": "\"{ABC},1\"",
            "size": 12345,
            "webUrl": "https://iamds-my.sharepoint.com/personal/x/Documents/Umzugsplan.docx",
            "parentReference": {"driveId": "b!1", "path": "/drive/root:", "siteId": None},
            "file": {"mimeType": "application/vnd.openxmlformats", "hashes": {"quickXorHash": "AAA="}},
            "shared": {},
            "description": "",
        },
        {"id": "02DEF", "name": "Notes.md", "size": 10, "folder": None},
        {"id": "03GHI", "name": "Archive", "folder": {"childCount": 4}},
    ],
}


def _shape(payload, tool="mcp_MSOffice365MCP_m365_list_drive_files", **kw):
    kw.setdefault("config", CFG)
    return shape_tool_result(json.dumps({"result": payload}), tool, "toolu_1", **kw)


class TestNoiseRemoval:
    def test_odata_keys_and_empty_values_are_dropped(self):
        out = json.loads(_shape(GRAPH_LISTING))["result"]
        assert not any(k.startswith("@odata") for k in out)
        first = out["value"][0]
        assert "@odata.etag" not in first and "eTag" in first  # only exact/glob patterns go
        assert "shared" not in first and "description" not in first
        assert "siteId" not in first["parentReference"]
        assert out["value"][1] == {"id": "02DEF", "name": "Notes.md", "size": 10}

    def test_configured_drop_keys(self):
        cfg = ShapeConfig(drop_keys=("@odata.*", "eTag", "parentReference"))
        out = json.loads(_shape(GRAPH_LISTING, config=cfg))["result"]
        assert "eTag" not in out["value"][0] and "parentReference" not in out["value"][0]


class TestCaps:
    def test_item_list_is_capped_with_shaped_block_pointing_at_rows(self):
        payload = {"value": [{"id": i, "name": f"f{i}"} for i in range(40)]}
        out = json.loads(_shape(payload, config=ShapeConfig(max_items=25), rows_ingested=True))["result"]
        assert len(out["value"]) == 25
        assert out[SHAPED_KEY]["total"] == 40 and out[SHAPED_KEY]["shown"] == 25
        assert out[SHAPED_KEY]["list"] == "value"
        assert "tool_use_id='toolu_1'" in out[SHAPED_KEY]["full_rows"]

    def test_no_rows_hint_when_nothing_was_ingested(self):
        payload = [{"id": i} for i in range(30)]
        out = json.loads(_shape(payload, config=ShapeConfig(max_items=5)))["result"]
        assert len(out["items"]) == 5 and "full_rows" not in out[SHAPED_KEY]

    def test_short_list_gets_no_shaped_block(self):
        out = json.loads(_shape(GRAPH_LISTING))["result"]
        assert SHAPED_KEY not in out

    def test_long_strings_inside_lists_are_truncated_with_marker(self):
        payload = {"value": [{"body": "x" * 5000, "short": "ok"}]}
        out = json.loads(_shape(payload, config=ShapeConfig(max_string_chars=100)))["result"]
        row = out["value"][0]
        assert row["body"].startswith("x" * 100) and row["body"].endswith("[+4900 chars]")
        assert row["short"] == "ok"

    def test_single_object_long_field_stays_complete(self):
        # An email body / document text is not a row preview.
        payload = {"id": "m1", "subject": "Plan", "body": {"content": "y" * 5000}}
        out = json.loads(_shape(payload, tool="mcp_MSOffice365MCP_m365_get_email",
                                config=ShapeConfig(max_string_chars=100)))["result"]
        assert out["body"]["content"] == "y" * 5000

    def test_depth_is_flattened(self):
        deep = {"a": {"b": {"c": {"d": {"e": "leaf"}}}}}
        out = json.loads(_shape(deep, config=ShapeConfig(max_depth=3)))["result"]
        assert out["a"]["b"] == "{…}"


class TestDeterminism:
    def test_same_payload_same_bytes_and_sorted_keys(self):
        a = _shape(GRAPH_LISTING)
        b = _shape(GRAPH_LISTING)
        assert a == b
        assert a.index('"id"') < a.index('"name"') < a.index('"size"')
        assert " " not in a.split('"name":"Umzugsplan.docx"')[0].replace("Umzugsplan", "")

    def test_result_string_wrapping_json_is_unwrapped(self):
        # FastMCP text block: {"result": "<json string>"} → object once.
        content = json.dumps({"result": json.dumps(GRAPH_LISTING)})
        out = json.loads(shape_tool_result(content, "mcp_S_t", "id", config=CFG))
        assert isinstance(out["result"], dict) and "value" in out["result"]


class TestPassThrough:
    @pytest.mark.parametrize("content", ["plain text", json.dumps({"result": "just prose"}), "", "{not json"])
    def test_non_json_or_prose_is_unchanged(self, content):
        assert shape_tool_result(content, "mcp_S_t", "id", config=CFG) == content

    @pytest.mark.parametrize("tool", [
        "mcp_AIMDSSuiteMCP_mcp_memory_memory_context",
        "mcp_AIMDSSuiteMCP_mcp_memory_skill",
        "mcp_AIMDSSuiteMCP_mcp_websearch_web_fetch",
        "mcp_AtlassianMCP_list_resources",
    ])
    def test_non_data_mcp_tools_are_unchanged(self, tool):
        content = json.dumps({"result": {"rules": [{"text": "r" * 3000}], "@odata.x": 1}})
        assert shape_tool_result(content, tool, "id", config=CFG) == content

    def test_non_mcp_tool_is_unchanged(self):
        content = json.dumps({"@odata.context": "x", "value": []})
        assert shape_tool_result(content, "terminal", "id", config=CFG) == content

    def test_error_payload_is_unchanged(self):
        content = json.dumps({"error": "Graph 404", "@odata.context": "x"})
        assert shape_tool_result(content, "mcp_S_t", "id", config=CFG) == content

    def test_disabled_config_is_unchanged(self):
        content = json.dumps({"result": GRAPH_LISTING})
        assert shape_tool_result(content, "mcp_S_t", "id", config=ShapeConfig(enabled=False)) == content

    def test_untrusted_wrapper_is_tolerated(self):
        wrapped = (
            '<untrusted_tool_result source="mcp_S_t">\n'
            + json.dumps({"result": GRAPH_LISTING})
            + "\n</untrusted_tool_result>"
        )
        out = shape_tool_result(wrapped, "mcp_S_t", "id", config=CFG)
        assert "@odata" not in out and '"name":"Umzugsplan.docx"' in out


class TestOverrides:
    def test_per_tool_beats_per_server(self):
        cfg = ShapeConfig(
            max_items=25,
            per_server={"MSOffice365MCP": {"max_items": 3}},
            per_tool={"m365_list_drive_files": {"max_items": 2}},
        )
        payload = {"value": [{"id": i} for i in range(10)]}
        drive = json.loads(_shape(payload, config=cfg))["result"]
        assert len(drive["value"]) == 2
        other = json.loads(_shape(payload, tool="mcp_MSOffice365MCP_m365_list_emails", config=cfg))["result"]
        assert len(other["value"]) == 3

    def test_load_shape_config_reads_mcp_results_section(self, monkeypatch):
        import tools.mcp_result_shaper as shaper

        monkeypatch.setattr(
            "hermes_cli.config.load_config",
            lambda: {"mcp_results": {"max_items": 7, "drop_keys": ["foo"], "per_tool": {"x": {"enabled": False}}}},
        )
        cfg = shaper.load_shape_config()
        assert cfg.max_items == 7 and cfg.drop_keys == ("foo",)
        assert cfg.for_tool("mcp_S_x").enabled is False
