"""AIS-427: a hung memory backend must not hold the turn for 180 s."""

import time

import pytest

from agent.memory_facade import MemoryFacade


def test_memory_reads_are_bounded(monkeypatch):
    facade = object.__new__(MemoryFacade)
    monkeypatch.setattr(MemoryFacade, "_call", lambda self, tool, args: time.sleep(2) or "late")
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        facade._call_read("memory_search", {"query": "x"}, timeout=0.2)
    assert time.monotonic() - started < 1.0


def test_fast_memory_reads_return_their_result(monkeypatch):
    facade = object.__new__(MemoryFacade)
    monkeypatch.setattr(MemoryFacade, "_call", lambda self, tool, args: {"ok": tool})
    assert facade._call_read("memory_context", {}, timeout=2) == {"ok": "memory_context"}


def test_memory_reads_cap_the_mcp_call_itself(monkeypatch):
    """AIS-744: the abandoned call must end too, not run on to 180 s."""
    from tools import mcp_tool

    seen = {}

    def _call(self, tool, args):
        seen["ceiling"] = mcp_tool._call_timeout_ceiling.get()
        return "ok"

    facade = object.__new__(MemoryFacade)
    monkeypatch.setattr(MemoryFacade, "_call", _call)
    assert facade._call_read("memory_session", {}, timeout=60) == "ok"
    assert seen["ceiling"] == 60
    assert mcp_tool._call_timeout_ceiling.get() is None
