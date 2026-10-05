"""OpenProject ``pm_*`` tools under either registered name (AIS-483).

The bundled ``OpenProjectMCP`` and the Suite's go-mcp-openproject (behind the
``AIMDSSuiteMCP`` gateway) expose the same ``pm_*`` contract under different
registered names:

* local: ``mcp_op_pm_list_time_entries`` (``tool_prefix: op``) or
  ``mcp_OpenProjectMCP_pm_list_time_entries``
* Suite: ``mcp_AIMDSSuiteMCP_mcp_openproject-pm_list_time_entries``

Every OpenProject consumer (brief, hints, tool search, shaping, worktime,
ticket routing, prompt guidance) matches the ``pm_<tool>`` suffix through this
module instead of a server name, and prefers the Suite when both are
registered. Pure name logic plus an optional registry lookup — no I/O.
"""

from __future__ import annotations

import re
import sys
from typing import Iterable, Optional

#: The local catalog server; its config key may differ (second instance).
LOCAL_SERVER = "OpenProjectMCP"
SUITE_SERVER = "AIMDSSuiteMCP"
SUITE = "suite"
LOCAL = "local"

_PM_SUFFIX_RE = re.compile(r"(?:^|[-_])(pm_[a-z_]+)$")
# The gateway names the Suite's tools ``mcp_<service>-<tool>``.
# Raw gateway name ``mcp_openproject-pm_x``; registered (sanitised) names are
# ``mcp_AIMDSSuiteMCP_mcp_openproject_pm_x`` — the hyphen becomes "_".
_SUITE_NAME_RE = re.compile(r"(?:^mcp_AIMDSSuiteMCP_.*|openproject-)pm_[a-z_]+$", re.IGNORECASE)


def pm_suffix(name: str) -> str:
    """``mcp_op_pm_get_work_package`` / ``…openproject-pm_get_work_package`` → ``pm_get_work_package``."""
    match = _PM_SUFFIX_RE.search(str(name or ""))
    return match.group(1) if match else ""


def _owner(name: str) -> str:
    """Registering server per the MCP registry, without importing it cold."""
    module = sys.modules.get("tools.mcp_tool")
    if module is None:
        return ""
    try:
        return str(module.get_mcp_server_for_tool(name) or "")
    except Exception:
        return ""


def openproject_kind(name: str) -> str:
    """``"suite"`` / ``"local"`` for an OpenProject ``pm_*`` tool name, else ``""``.

    Works on registered names and on raw MCP names (``mcp_openproject-pm_x``
    is the Suite's raw name). A ``pm_*`` tool of an unrelated server is not
    OpenProject.
    """
    text = str(name or "")
    if not pm_suffix(text):
        return ""
    if _SUITE_NAME_RE.search(text):
        return SUITE
    low = text.lower()
    if "openproject" in _owner(text).lower() or low.startswith("mcp_op_pm_") or "openproject" in low:
        return LOCAL
    return ""


def is_openproject_tool(name: str) -> bool:
    return bool(openproject_kind(name))


def server_for(name: str) -> str:
    """MCP server (status key) behind an OpenProject tool name."""
    kind = openproject_kind(name)
    if not kind:
        return ""
    return _owner(name) or (SUITE_SERVER if kind == SUITE else LOCAL_SERVER)


def is_hidden(name: str) -> bool:
    """The AIS-479 per-domain decision currently hides this tool."""
    kind = openproject_kind(name)
    if not kind:
        return False
    try:
        from agent.openproject_suite import tool_hidden

        return bool(tool_hidden(server_for(name), str(name)))
    except Exception:
        return False


def openproject_tools(names: Iterable[str], suffix: str, *, skip_hidden: bool = False) -> list:
    """Every registered name for ``suffix`` (``pm_list_time_entries``), Suite first, then by name."""
    wanted = str(suffix or "").lower()
    hits = [
        n for n in (str(x) for x in (names or ()) if isinstance(x, str))
        if pm_suffix(n).lower() == wanted and openproject_kind(n)
    ]
    if skip_hidden:
        hits = [n for n in hits if not is_hidden(n)]
    return sorted(hits, key=lambda n: (openproject_kind(n) != SUITE, n))


def find_openproject_tool(names: Iterable[str], suffix: str, *, skip_hidden: bool = False) -> Optional[str]:
    """The preferred registered name for ``suffix``: the Suite's when both exist."""
    hits = openproject_tools(names, suffix, skip_hidden=skip_hidden)
    return hits[0] if hits else None
