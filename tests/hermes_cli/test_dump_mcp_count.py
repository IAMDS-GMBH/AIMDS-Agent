"""AIS-332: ``hermes dump`` must not report ``mcp_servers: 0`` for an install
whose config.yaml has servers configured just because ``load_config()``
handed back an empty dict."""

from hermes_cli import dump


def test_counts_canonical_block():
    assert dump._count_mcp_servers({"mcp_servers": {"A": {}, "B": {}}}) == 2


def test_counts_legacy_block():
    assert dump._count_mcp_servers({"mcp": {"servers": {"A": {}}}}) == 1


def test_falls_back_to_raw_config_yaml(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "mcp_servers:\n  GithubMCP: {}\n  MSOffice365MCP: {}\n  AIMDSSuiteMCP: {}\n",
        encoding="utf-8",
    )
    assert dump._count_mcp_servers({}, tmp_path) == 3


def test_empty_block_in_parsed_config_is_not_overridden(tmp_path):
    (tmp_path / "config.yaml").write_text("mcp_servers:\n  X: {}\n", encoding="utf-8")
    assert dump._count_mcp_servers({"mcp_servers": {}}, tmp_path) == 0


def test_missing_or_broken_yaml_counts_zero(tmp_path):
    assert dump._count_mcp_servers({}, tmp_path) == 0
    (tmp_path / "config.yaml").write_text(": not yaml: [", encoding="utf-8")
    assert dump._count_mcp_servers({}, tmp_path) == 0
