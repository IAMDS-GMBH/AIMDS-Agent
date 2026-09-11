"""Shared stubs for driving ``cron.scheduler.run_job`` end-to-end without
credentials, network or the real ``~/.hermes`` (AIS-305 tests)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


class FakeAgent:
    """Stand-in for ``run_agent.AIAgent`` that records its constructor kwargs."""

    instances: list = []
    response: str = "done\n\nFINDING: nothing new\nNEXT: carry on"
    tool_messages: int = 0

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.enabled_toolsets = kwargs.get("enabled_toolsets")
        self.disabled_toolsets = kwargs.get("disabled_toolsets")
        self.max_iterations = kwargs.get("max_iterations")
        self.tools = []
        self.valid_tool_names = set()
        self.session_id = kwargs.get("session_id")
        self.prompt_received: Optional[str] = None
        self.session_api_calls = 1
        FakeAgent.instances.append(self)

    def run_conversation(self, user_message=None, *args, **kwargs):
        self.prompt_received = user_message
        messages = [{"role": "tool", "content": "x"} for _ in range(FakeAgent.tool_messages)]
        return {
            "final_response": FakeAgent.response,
            "messages": messages,
            "prompt_tokens": 1234,
            "completion_tokens": 56,
            "cache_read_tokens": 100,
        }

    def get_activity_summary(self):
        return {"seconds_since_activity": 0.0, "api_calls": 1, "max_iterations": self.max_iterations or 0}

    def close(self):
        pass


def install_run_job_stubs(monkeypatch, hermes_home: Path, *, config: Optional[Dict[str, Any]] = None,
                          vault: Optional[Path] = None, keep_prompt_builder: bool = False) -> None:
    """Patch run_job's environment: HERMES_HOME, config.yaml, provider resolver, AIAgent."""
    import cron.scheduler as sched

    hermes_home.mkdir(parents=True, exist_ok=True)
    (hermes_home / "config.yaml").write_text(yaml.safe_dump(config or {}), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    # cron.jobs resolves its paths at import time — redirect them explicitly so
    # nothing a test does can touch the developer's real ~/.hermes/cron.
    from cron import jobs as jobs_mod

    (hermes_home / "cron").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(jobs_mod, "CRON_DIR", hermes_home / "cron")
    monkeypatch.setattr(jobs_mod, "JOBS_FILE", hermes_home / "cron" / "jobs.json")
    monkeypatch.setattr(jobs_mod, "OUTPUT_DIR", hermes_home / "cron" / "output")
    monkeypatch.setattr(jobs_mod, "CRON_CACHE_DIR", hermes_home / "cron" / "cache", raising=False)
    vault = vault or (hermes_home / "vault")
    vault.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TERMINAL_CWD", str(vault))
    monkeypatch.setenv("HERMES_CRON_TIMEOUT", "0")

    FakeAgent.instances.clear()
    fake_mod = type(sys)("run_agent")
    fake_mod.AIAgent = FakeAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_mod)

    from hermes_cli import runtime_provider as _rtp

    monkeypatch.setattr(
        _rtp,
        "resolve_runtime_provider",
        lambda **_kw: {
            "provider": "test",
            "api_key": "k",
            "base_url": "http://test.local",
            "api_mode": "chat_completions",
        },
    )
    if not keep_prompt_builder:
        monkeypatch.setattr(sched, "_build_job_prompt", lambda job, prerun_script=None, **kw: "hi")
    monkeypatch.setattr(sched, "_resolve_origin", lambda job: None)
    monkeypatch.setattr(sched, "_resolve_delivery_target", lambda job: None)

    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *_a, **_kw: True)

    try:
        from tools import mcp_tool

        monkeypatch.setattr(mcp_tool, "discover_mcp_tools", lambda: [])
    except Exception:
        pass
