"""AIS-797: the versioned data contracts and their generated TypeScript types."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

from agent import contracts  # noqa: E402
from agent.contracts.typescript import TARGET, render  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("name", contracts.NAMES)
def test_schema_is_valid_and_versioned(name):
    schema = contracts.load_schema(name)
    jsonschema.Draft202012Validator.check_schema(schema)
    assert schema["$id"] == f"urn:aimds-agent:contract:{name}:{contracts.version(name)}"
    assert schema["properties"]["v"] == {"const": contracts.version(name), "description": "Contract version."}
    assert "v" in schema["required"] and schema["additionalProperties"] is False


def test_generated_typescript_is_up_to_date():
    current = (REPO_ROOT / TARGET).read_text(encoding="utf-8")
    assert current == render(), "run: python scripts/gen_contract_types.py"


def test_generator_check_mode():
    proc = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "gen_contract_types.py"), "--check"],
                          capture_output=True, text=True, cwd=REPO_ROOT)
    assert proc.returncode == 0, proc.stderr


def test_shared_package_exports_the_contracts():
    index = (REPO_ROOT / "apps" / "shared" / "src" / "index.ts").read_text(encoding="utf-8")
    assert "export * from './contracts'" in index
    gateway = (REPO_ROOT / "apps" / "shared" / "src" / "json-rpc-gateway.ts").read_text(encoding="utf-8")
    assert "'session.ledger'" in gateway


def test_typescript_shapes():
    text = render()
    assert "export interface LedgerEntry {" in text and "  status_from?: string\n" in text
    assert "export type LedgerKind = 'ticket' | 'mail' | 'chat' | 'file' | 'note' | 'web' | 'artifact'" in text
    assert "  entries: LedgerEntry[]\n" in text  # "#" resolves to the root type
    assert "  prev_status?: string | null\n" in text
    assert "  extra?: Record<string, unknown>\n" in text
    assert "export const CONTRACT_VERSIONS = {" in text


VALID = {
    "clarify_questions": {
        "v": 1,
        "questions": [{"question": "Which environment first?", "choices": ["Staging", "Production"], "topic": "Rollout"}],
        "answers": [{"state": "answered", "response": "Staging"}],
    },
    "presence": {
        "v": 1, "device": "device-demo", "posture": "developer", "capabilities": ["openproject", "memory"],
        "version": "0.8.0", "runner": "local", "ts": "2026-10-30T09:00:00Z", "expires": "2026-10-30T09:05:00Z",
    },
    "agent_envelope": {
        "v": 1, "id": "env-1", "from": "device-demo", "to": "suite", "kind": "handoff",
        "refs": [{"source": "openproject", "key": "DEMO-1"}, {"source": "web", "url": "https://example.org/x"}],
        "intent": "Prepare the release notes for DEMO-1.", "expires": "2026-10-30T10:00:00Z", "runner": "suite",
    },
    "feedback_signature": {
        "v": 1, "server": "AIMDSSuiteMCP", "tool": "mcp_openproject-pm_update_work_package",
        "signal": "timeout", "count": 3, "versions": ["0.7.14"],
    },
    "workspace_feed": {
        "v": 1, "generated_at": "2026-10-30T09:00:00+01:00", "cached": False, "age_s": 0,
        "window": {"kind": "workspace", "today": "2026-10-30", "preview_day": "2026-11-02",
                   "start": "2026-10-30T00:00:00+01:00", "end": "2026-11-02T23:59:59+01:00"},
        "items": [{"id": "openproject:ticket:DEMO-1", "source": "openproject", "kind": "ticket", "key": "DEMO-1",
                   "title": "Prepare the staging host", "status": "In Progress", "mine": True,
                   "prev_status": "Backlog", "status_changed_at": "2026-10-30T08:00:00+01:00",
                   "first_seen_at": "2026-10-29T08:00:00+01:00"}],
        "sources": [{"name": "openproject", "label": "OpenProject", "status": "active", "detail": "1",
                     "count": 1, "partial": False, "unavailable": False}],
    },
}


@pytest.mark.parametrize("name", sorted(VALID))
def test_valid_payloads_pass(name):
    contracts.validate(name, VALID[name])


@pytest.mark.parametrize("name, patch", [
    ("clarify_questions", {"questions": [{"question": "Pick one", "choices": ["a", "b", "c", "d", "e"]}]}),
    ("clarify_questions", {"questions": []}),
    ("presence", {"runner": "cloud"}),
    ("agent_envelope", {"intent": "x" * 201}),
    # Refs carry identifiers, never content.
    ("agent_envelope", {"refs": [{"source": "openproject", "key": "DEMO-1", "body": "ticket text"}]}),
    ("agent_envelope", {"refs": [{"source": "openproject"}]}),
    ("feedback_signature", {"message": "the tool said …"}),
    ("feedback_signature", {"signal": "slow"}),
    ("workspace_feed", {"v": 2}),
])
def test_invalid_payloads_fail(name, patch):
    with pytest.raises(jsonschema.ValidationError):
        contracts.validate(name, {**VALID[name], **patch})


def test_ledger_ref_rejects_content_fields():
    entry = {"v": 1, "seq": 1, "ts": 1.0, "kind": "mail", "verb": "send", "source": "m365", "area": "m365",
             "tool": "send_mail", "ref": {"title": "Status update"}, "lineage_root": "s", "session_id": "s"}
    contracts.validate("ledger_entry", entry)
    with pytest.raises(jsonschema.ValidationError):
        contracts.validate("ledger_entry", {**entry, "ref": {"title": "x", "body": "Mail text"}})
