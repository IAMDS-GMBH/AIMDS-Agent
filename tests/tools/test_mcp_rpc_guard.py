"""AIS-744: HTTP MCP calls run concurrently; callers can cap a call's timeout."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from tools import mcp_tool


def _server(config):
    return SimpleNamespace(_config=config, _rpc_lock=asyncio.Lock())


def test_stdio_servers_keep_the_lock():
    server = _server({"command": "npx"})
    assert mcp_tool._rpc_guard(server) is server._rpc_lock


def test_servers_without_config_keep_the_lock():
    server = SimpleNamespace(_rpc_lock=asyncio.Lock())
    assert mcp_tool._rpc_guard(server) is server._rpc_lock


def test_http_servers_are_not_serialized():
    server = _server({"url": "https://suite.example/mcp/"})

    async def _scenario():
        slow_entered = asyncio.Event()
        release = asyncio.Event()

        async def _slow():
            async with mcp_tool._rpc_guard(server):
                slow_entered.set()
                await release.wait()

        async def _fast():
            await slow_entered.wait()
            async with mcp_tool._rpc_guard(server):
                return "fast"

        slow = asyncio.create_task(_slow())
        result = await asyncio.wait_for(_fast(), timeout=1)
        release.set()
        await slow
        return result

    assert asyncio.run(_scenario()) == "fast"


def test_stdio_servers_serialize_calls():
    async def _scenario():
        server = _server({"command": "npx"})
        order = []

        async def _call(name, hold):
            async with mcp_tool._rpc_guard(server):
                order.append(f"{name}-in")
                await asyncio.sleep(hold)
                order.append(f"{name}-out")

        await asyncio.gather(_call("a", 0.05), _call("b", 0))
        return order

    assert asyncio.run(_scenario()) == ["a-in", "a-out", "b-in", "b-out"]


def test_call_timeout_ceiling_caps_the_tool_timeout():
    assert mcp_tool._effective_tool_timeout(180) == 180
    with mcp_tool.mcp_call_timeout(60):
        assert mcp_tool._effective_tool_timeout(180) == 60
        assert mcp_tool._effective_tool_timeout(30) == 30
    assert mcp_tool._effective_tool_timeout(180) == 180


def test_tool_handler_passes_the_capped_timeout():
    seen = {}

    def _fake_loop(coro_or_factory, timeout=30):
        seen["timeout"] = timeout
        coro = coro_or_factory() if callable(coro_or_factory) else coro_or_factory
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    session = MagicMock()
    session.call_tool = AsyncMock(
        return_value=SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            isError=False,
            structuredContent=None,
        )
    )
    server = SimpleNamespace(session=session, _config={"url": "https://x/mcp"}, _rpc_lock=None)
    with patch.dict(mcp_tool._servers, {"srv": server}), patch(
        "tools.mcp_tool._run_on_mcp_loop", side_effect=_fake_loop
    ):
        handler = mcp_tool._make_tool_handler("srv", "memory_session", 180.0)
        with mcp_tool.mcp_call_timeout(60):
            assert json.loads(handler({})) == {"result": "ok"}
    assert seen["timeout"] == 60
