#!/usr/bin/env python3
"""Regenerate the vendored go-mcp-openproject tool contract (AIS-479).

The bundled OpenProject MCP server (optional-mcps/OpenProjectMCP) mirrors the
AIMDS Suite's ``go-mcp-openproject`` tool contract;
``tests/optional_mcps/test_openproject_contract.py`` checks it against the
snapshot this script writes. Run it whenever the Suite server changes:

    python scripts/sync_openproject_contract.py [SUITE_REPO] [--ref origin/main]

The Suite working tree is never touched: ``git archive`` exports
``container/go-mcp-openproject`` at the ref into a temp dir, a temporary
``_test.go`` dumps ``NewService(nil, nil, nil, nil, "", "").ToolList()`` as
JSON (exactly what ``tools/list`` returns) and the result is written with the
resolved commit. Needs ``git`` and ``go`` on PATH.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = REPO_ROOT / "optional-mcps" / "OpenProjectMCP" / "contract" / "go-mcp-openproject.tools.json"
SERVER_DIR = "container/go-mcp-openproject"
SOURCE = "IAMDS-GMBH/AIMDS-Suite container/go-mcp-openproject"

DUMP_TEST = """package tools

import (
\t"encoding/json"
\t"os"
\t"testing"
)

func TestDumpToolList(t *testing.T) {
\tb, err := json.MarshalIndent(NewService(nil, nil, nil, nil, "", "").ToolList(), "", "  ")
\tif err != nil {
\t\tt.Fatal(err)
\t}
\tif err := os.WriteFile(os.Getenv("DUMP_TOOL_LIST"), b, 0o644); err != nil {
\t\tt.Fatal(err)
\t}
}
"""


def _run(cmd: list, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, **kwargs)


def resolve_commit(suite: Path, ref: str) -> str:
    return _run(["git", "-C", str(suite), "rev-parse", "--short=8", f"{ref}^{{commit}}"], text=True).stdout.strip()


def export_server(suite: Path, commit: str, dest: Path) -> Path:
    """``git archive`` the server directory at ``commit`` into ``dest``."""
    archive = _run(["git", "-C", str(suite), "archive", "--format=tar", commit, SERVER_DIR]).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        try:
            tar.extractall(dest, filter="data")
        except TypeError:  # Python < 3.12
            tar.extractall(dest)
    server = dest / SERVER_DIR
    if not (server / "internal" / "tools").is_dir():
        raise SystemExit(f"{SERVER_DIR}/internal/tools not found at {commit}")
    return server


def dump_tool_list(server: Path) -> list:
    (server / "internal" / "tools" / "zz_dump_tool_list_test.go").write_text(DUMP_TEST, encoding="utf-8")
    out = server / "tools.json"
    env = dict(os.environ, DUMP_TOOL_LIST=str(out))
    proc = subprocess.run(
        ["go", "test", "./internal/tools", "-run", "TestDumpToolList", "-count=1"],
        cwd=server, env=env, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"go test failed:\n{proc.stdout}\n{proc.stderr}")
    return json.loads(out.read_text(encoding="utf-8"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("suite", nargs="?", default=str(REPO_ROOT.parent / "AIMDS-Suite"),
                        help="path to an AIMDS-Suite checkout (default: ../AIMDS-Suite)")
    parser.add_argument("--ref", default="origin/main", help="git ref to read (default: origin/main)")
    parser.add_argument("--output", default=str(SNAPSHOT), help="snapshot file to write")
    args = parser.parse_args(argv)

    for binary in ("git", "go"):
        if shutil.which(binary) is None:
            raise SystemExit(f"{binary} is required on PATH")
    suite = Path(args.suite).resolve()
    commit = resolve_commit(suite, args.ref)
    with tempfile.TemporaryDirectory(prefix="op-contract-") as tmp:
        tools = dump_tool_list(export_server(suite, commit, Path(tmp)))

    snapshot = {"source": SOURCE, "commit": commit, "tools": tools}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(tools)} tools from {args.ref} ({commit}) to {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
