import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


from tools.mcp_tool import MCPServerTask, _format_connect_error, _resolve_stdio_command, _MCP_AVAILABLE

# Ensure the mcp module symbols exist for patching even when the SDK isn't installed
if not _MCP_AVAILABLE:
    import tools.mcp_tool as _mcp_mod
    if not hasattr(_mcp_mod, "StdioServerParameters"):
        _mcp_mod.StdioServerParameters = MagicMock
    if not hasattr(_mcp_mod, "stdio_client"):
        _mcp_mod.stdio_client = MagicMock
    if not hasattr(_mcp_mod, "ClientSession"):
        _mcp_mod.ClientSession = MagicMock


def test_resolve_stdio_command_falls_back_to_hermes_node_bin(tmp_path):
    node_bin = tmp_path / "node" / "bin"
    node_bin.mkdir(parents=True)
    npx_path = node_bin / "npx"
    npx_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    npx_path.chmod(0o755)

    with patch("tools.mcp_tool.shutil.which", return_value=None), \
         patch.dict("os.environ", {"HERMES_HOME": str(tmp_path)}, clear=False):
        command, env = _resolve_stdio_command("npx", {"PATH": "/usr/bin"})

    assert command == str(npx_path)
    assert env["PATH"].split(os.pathsep)[0] == str(node_bin)


def test_resolve_stdio_command_falls_back_to_usr_local_bin():
    """When ``npx`` isn't on the filtered PATH and isn't under ``$HERMES_HOME/node/bin``
    or ``~/.local/bin``, the resolver should still locate it at ``/usr/local/bin/npx``.

    This is the canonical install location for Node on Linux from-source builds,
    the upstream ``node:bookworm-slim`` image (which the Hermes Docker image
    copies ``node + npm + corepack`` from since #4977), and macOS Homebrew on
    Intel. Without this candidate, MCP servers run with an ``env.PATH`` that
    omits ``/usr/local/bin`` (common when users hand-author PATH for sandboxing)
    fail with ENOENT at ``execvp``.
    """
    target = os.path.join(os.sep, "usr", "local", "bin", "npx")

    # Pretend ONLY the /usr/local/bin/npx candidate exists and is executable —
    # the other candidates ($HERMES_HOME/node/bin/npx and ~/.local/bin/npx)
    # should fail isfile() and the resolver must fall through to /usr/local/bin.
    def _fake_isfile(path):
        return path == target

    def _fake_access(path, _mode):
        return path == target

    with patch("tools.mcp_tool.shutil.which", return_value=None), \
         patch("tools.mcp_tool.os.path.isfile", side_effect=_fake_isfile), \
         patch("tools.mcp_tool.os.access", side_effect=_fake_access):
        command, env = _resolve_stdio_command("npx", {"PATH": "/opt/data/bin:/usr/bin:/bin"})

    assert command == target
    # /usr/local/bin must be prepended so npx's shebang (`/usr/bin/env node`)
    # can find node in the same directory.
    assert env["PATH"].split(os.pathsep)[0] == os.path.dirname(target)


def test_resolve_stdio_command_falls_back_to_hermes_managed_uv_bin(tmp_path):
    """Bare ``uvx`` should resolve against Hermes' own managed uv install at
    ``$HERMES_HOME/bin`` (see hermes_cli/managed_uv.py: managed_uv_path()),
    mirroring the existing $HERMES_HOME/node/bin fallback for npx.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    uvx_path = bin_dir / "uvx"
    uvx_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    uvx_path.chmod(0o755)

    with patch("tools.mcp_tool.shutil.which", return_value=None), \
         patch.dict("os.environ", {"HERMES_HOME": str(tmp_path)}, clear=False):
        command, env = _resolve_stdio_command("uvx", {"PATH": "/usr/bin"})

    assert command == str(uvx_path)
    assert env["PATH"].split(os.pathsep)[0] == str(bin_dir)


def test_resolve_stdio_command_falls_back_to_local_bin_for_uvx():
    """When uvx isn't under $HERMES_HOME/bin, fall back to ~/.local/bin/uvx
    (the default install location of the official astral.sh installer).
    """
    target = os.path.join(os.path.expanduser("~"), ".local", "bin", "uvx")

    def _fake_isfile(path):
        return path == target

    def _fake_access(path, _mode):
        return path == target

    with patch("tools.mcp_tool.shutil.which", return_value=None), \
         patch("tools.mcp_tool.os.path.isfile", side_effect=_fake_isfile), \
         patch("tools.mcp_tool.os.access", side_effect=_fake_access):
        command, env = _resolve_stdio_command("uvx", {"PATH": "/usr/bin"})

    assert command == target
    assert env["PATH"].split(os.pathsep)[0] == os.path.dirname(target)


def test_resolve_stdio_command_falls_back_to_homebrew_bin_for_uvx():
    """When uvx isn't under $HERMES_HOME/bin or ~/.local/bin, fall back to
    /opt/homebrew/bin/uvx (Homebrew on Apple Silicon macOS) — a very common
    real-world uv install location that previously had no fallback at all,
    causing "[Errno 2] No such file or directory: 'uvx'" whenever the
    subprocess PATH was filtered (e.g. under a packaged GUI/gateway process).
    """
    target = os.path.join(os.sep, "opt", "homebrew", "bin", "uvx")

    def _fake_isfile(path):
        return path == target

    def _fake_access(path, _mode):
        return path == target

    with patch("tools.mcp_tool.shutil.which", return_value=None), \
         patch("tools.mcp_tool.os.path.isfile", side_effect=_fake_isfile), \
         patch("tools.mcp_tool.os.access", side_effect=_fake_access):
        command, env = _resolve_stdio_command("uvx", {"PATH": "/usr/bin"})

    assert command == target
    assert env["PATH"].split(os.pathsep)[0] == os.path.dirname(target)


def test_resolve_stdio_command_falls_back_to_usr_local_bin_for_uv():
    """"uv" (not just "uvx") should get the same fallback treatment, landing
    on /usr/local/bin/uv as the last-resort candidate.
    """
    target = os.path.join(os.sep, "usr", "local", "bin", "uv")

    def _fake_isfile(path):
        return path == target

    def _fake_access(path, _mode):
        return path == target

    with patch("tools.mcp_tool.shutil.which", return_value=None), \
         patch("tools.mcp_tool.os.path.isfile", side_effect=_fake_isfile), \
         patch("tools.mcp_tool.os.access", side_effect=_fake_access):
        command, env = _resolve_stdio_command("uv", {"PATH": "/opt/data/bin:/usr/bin"})

    assert command == target
    assert env["PATH"].split(os.pathsep)[0] == os.path.dirname(target)


def test_run_stdio_uses_resolved_uvx_command_and_prepended_path(tmp_path):
    """End-to-end: MCPServerTask.start() with a bare "uvx" command should
    launch the resolved $HERMES_HOME/bin/uvx executable, matching the
    existing npx end-to-end coverage above.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    uvx_path = bin_dir / "uvx"
    uvx_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    uvx_path.chmod(0o755)

    mock_session = MagicMock()
    mock_session.initialize = AsyncMock()
    mock_session.list_tools = AsyncMock(return_value=SimpleNamespace(tools=[]))

    mock_stdio_cm = MagicMock()
    mock_stdio_cm.__aenter__ = AsyncMock(return_value=(object(), object()))
    mock_stdio_cm.__aexit__ = AsyncMock(return_value=False)

    mock_session_cm = MagicMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_cm.__aexit__ = AsyncMock(return_value=False)

    async def _test():
        with patch("tools.mcp_tool.shutil.which", return_value=None), \
             patch.dict("os.environ", {"HERMES_HOME": str(tmp_path), "PATH": "/usr/bin", "HOME": str(tmp_path)}, clear=False), \
             patch("tools.mcp_tool.StdioServerParameters") as mock_params, \
             patch("tools.mcp_tool.stdio_client", return_value=mock_stdio_cm), \
             patch("tools.mcp_tool.ClientSession", return_value=mock_session_cm):
            server = MCPServerTask("srv")
            await server.start({"command": "uvx", "args": ["some-mcp-package"], "env": {"PATH": "/usr/bin"}})

            call_kwargs = mock_params.call_args.kwargs
            assert call_kwargs["command"] == str(uvx_path)
            assert call_kwargs["env"]["PATH"].split(os.pathsep)[0] == str(bin_dir)

            await server.shutdown()

    asyncio.run(_test())


def test_resolve_stdio_command_respects_explicit_empty_path():
    seen_paths = []

    def _fake_which(_cmd, path=None):
        seen_paths.append(path)
        return None

    with patch("tools.mcp_tool.shutil.which", side_effect=_fake_which):
        command, env = _resolve_stdio_command("python", {"PATH": ""})

    assert command == "python"
    assert env["PATH"] == ""
    assert seen_paths == [""]


def test_format_connect_error_unwraps_exception_group():
    error = ExceptionGroup(
        "unhandled errors in a TaskGroup",
        [FileNotFoundError(2, "No such file or directory", "node")],
    )

    message = _format_connect_error(error)

    assert "missing executable 'node'" in message


def test_run_stdio_uses_resolved_command_and_prepended_path(tmp_path):
    node_bin = tmp_path / "node" / "bin"
    node_bin.mkdir(parents=True)
    npx_path = node_bin / "npx"
    npx_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    npx_path.chmod(0o755)

    mock_session = MagicMock()
    mock_session.initialize = AsyncMock()
    mock_session.list_tools = AsyncMock(return_value=SimpleNamespace(tools=[]))

    mock_stdio_cm = MagicMock()
    mock_stdio_cm.__aenter__ = AsyncMock(return_value=(object(), object()))
    mock_stdio_cm.__aexit__ = AsyncMock(return_value=False)

    mock_session_cm = MagicMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_cm.__aexit__ = AsyncMock(return_value=False)

    async def _test():
        with patch("tools.mcp_tool.shutil.which", return_value=None), \
             patch.dict("os.environ", {"HERMES_HOME": str(tmp_path), "PATH": "/usr/bin", "HOME": str(tmp_path)}, clear=False), \
             patch("tools.mcp_tool.StdioServerParameters") as mock_params, \
             patch("tools.mcp_tool.stdio_client", return_value=mock_stdio_cm), \
             patch("tools.mcp_tool.ClientSession", return_value=mock_session_cm):
            server = MCPServerTask("srv")
            await server.start({"command": "npx", "args": ["-y", "pkg"], "env": {"PATH": "/usr/bin"}})

            call_kwargs = mock_params.call_args.kwargs
            assert call_kwargs["command"] == str(npx_path)
            assert call_kwargs["env"]["PATH"].split(os.pathsep)[0] == str(node_bin)

            await server.shutdown()

    asyncio.run(_test())
