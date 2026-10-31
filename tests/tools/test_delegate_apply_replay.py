"""Replay (AIS-796): a batch of 31 similar creates becomes one child run.

Before: the main chat makes 31 create calls itself — 31 tool results in its
context — and gets one delegation hint at the seventh. After: the user
approved the list, the chat makes ONE delegate_task(agent="apply") call; the
child carries out the 31 creates behind its tool rule (a stray send is
denied and reported as skipped), the chat receives one compact result and
state.db holds one agent_runs row. The fixture is synthetic (project DEMO).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.delegation_nudge import DelegationNudge
from agent.tool_guardrails import ToolCallGuardrailController
from hermes_state import SessionDB
from tools.delegate_tool import delegate_task

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "delegation" / "batch_31_creates.json"
SEND = "mcp_MSOffice365MCP_m365_send_email"


@pytest.fixture(scope="module")
def batch():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_is_synthetic(batch):
    assert batch["project"] == "DEMO" and len(batch["items"]) == 31
    assert all(item["subject"].startswith("Sample task ") for item in batch["items"])


def test_before_the_main_chat_makes_31_calls_and_gets_one_hint(batch):
    nudge = DelegationNudge(platform="tui")
    hints = [nudge.observe(batch["tool"], '{"id": 1}') for _ in batch["items"]]
    assert len(hints) == 31  # 31 tool results in the main context
    assert [i for i, hint in enumerate(hints) if hint] == [6]
    assert "'apply'" in hints[6]


def _parent(db):
    parent = MagicMock()
    parent.base_url = "https://litellm.example.test/v1"
    parent.api_key = "***"
    parent.provider = "aimds-suite-prod"
    parent.api_mode = "chat_completions"
    parent.model = "AIMDS-Suite-Auto"
    parent.platform = "tui"
    parent.session_id = "chat-demo"
    parent._session_db = db
    parent._delegate_depth = 0
    parent._active_children = []
    parent._active_children_lock = threading.Lock()
    parent._print_fn = None
    parent.tool_progress_callback = None
    parent.thinking_callback = None
    parent.providers_allowed = parent.providers_ignored = parent.providers_order = parent.provider_sort = None
    parent.enabled_toolsets = ["file", "terminal", "clarify", "memory", "todo", "mcp-AIMDSSuiteMCP"]
    return parent


def _child(batch):
    child = MagicMock()
    child._tool_guardrails = ToolCallGuardrailController()
    child.session_id = "child-demo"
    child.model = "AIMDS-Suite-Auto"
    child.session_prompt_tokens = 9_000
    child.session_completion_tokens = 1_200
    child.session_estimated_cost_usd = 0.0
    names = ("write_file", "patch", "todo", "cronjob", batch["tool"], SEND)
    child.tools = [{"type": "function", "function": {"name": n}} for n in names]
    child.valid_tool_names = set(names)

    def run_conversation(*_args, **_kwargs):
        """The child's turn, every call through its real guardrail gate."""
        calls = [(batch["tool"], {"project": batch["project"], **item}) for item in batch["items"]]
        calls.insert(10, (SEND, {"to": "team@example.test", "subject": "Sample status"}))
        messages, rows, counts = [], [], {"ok": 0, "skipped": 0}
        for index, (name, args) in enumerate(calls):
            call_id = f"tc_{index}"
            messages.append({"role": "assistant", "tool_calls": [
                {"id": call_id, "function": {"name": name, "arguments": json.dumps(args)}},
            ]})
            decision = child._tool_guardrails.before_call(name, args)
            if decision.allows_execution:
                key = f"DEMO-{101 + len(rows)}"
                content = json.dumps({"id": 1000 + index, "key": key})
                rows.append(f"| {args['subject']} | ok | {key} | |")
                counts["ok"] += 1
            else:
                content = json.dumps({"error": decision.message})
                rows.append("| Sample status mail | skipped | | needs the user's confirmation |")
                counts["skipped"] += 1
            messages.append({"role": "tool", "tool_call_id": call_id, "content": content})
        summary = (
            "| item | status | reference | note |\n|---|---|---|---|\n" + "\n".join(rows)
            + f"\n\nok: {counts['ok']}, failed: 0, skipped: {counts['skipped']}"
        )
        messages.append({"role": "assistant", "content": summary})
        return {"final_response": summary, "completed": True, "interrupted": False, "api_calls": 12,
                "messages": messages}

    child.run_conversation.side_effect = run_conversation
    return child


def test_after_one_apply_run_carries_the_approved_batch(batch, tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("chat-demo", source="tui")
    parent = _parent(db)
    child = _child(batch)
    goal = f"Create the {len(batch['items'])} approved work packages in project {batch['project']}"
    context = "\n".join(f"- {item['subject']} ({item['type']})" for item in batch["items"])

    with patch("run_agent.AIAgent", return_value=child) as agent_cls:
        raw = delegate_task(goal=goal, context=context, agent="apply", parent_agent=parent)
        build = agent_cls.call_args.kwargs

    result = json.loads(raw)
    # One delegate_task result instead of 31 tool results in the main context.
    assert len(result["results"]) == 1
    entry = result["results"][0]
    assert entry["status"] == "completed"
    assert "ok: 31, failed: 0, skipped: 1" in entry["summary"]
    assert "DEMO-131" in entry["summary"] and "needs the user's confirmation" in entry["summary"]
    assert len(raw) < 6_000 + 2_000  # the definition's result contract

    # The child: MCP only, no clarify, no Hermes write tools, the send denied.
    assert build["enabled_toolsets"] == ["mcp-AIMDSSuiteMCP"]
    assert build["clarify_callback"] is None
    assert {t["function"]["name"] for t in child.tools} == {batch["tool"], SEND}
    assert len(entry["tool_trace"]) == 32
    assert [t["status"] for t in entry["tool_trace"]].count("error") == 1

    # One agent_runs row: numbers and a goal hash, no goal text.
    runs = db.get_agent_runs(session_id="chat-demo")
    assert len(runs) == 1
    run = runs[0]
    assert run["definition"] == "apply" and run["status"] == "completed"
    assert run["tool_calls"] == 32 and run["tool_errors"] == 1 and run["iterations"] == 12
    assert run["child_session_id"] == "child-demo" and run["result_chars"] == len(entry["summary"])
    assert len(run["goal_hash"]) == 16 and run["goal_hash"] not in goal
    assert all(goal not in str(value) for value in run.values())
    db.close()
