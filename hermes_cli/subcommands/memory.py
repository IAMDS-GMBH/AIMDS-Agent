"""``hermes memory`` subcommand parser.

Extracted from ``hermes_cli/main.py:main()`` (god-file Phase 2 follow-up).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_memory_parser(subparsers, *, cmd_memory: Callable) -> None:
    """Attach the ``memory`` subcommand to ``subparsers``."""
    memory_parser = subparsers.add_parser(
        "memory",
        help="Configure external memory provider",
        description=(
            "Set up and manage external memory provider plugins.\n\n"
            "Available providers: honcho, openviking, mem0, hindsight,\n"
            "holographic, retaindb, byterover.\n\n"
            "Only one external provider can be active at a time.\n"
            "Built-in memory (MEMORY.md/USER.md) is always active."
        ),
    )
    memory_sub = memory_parser.add_subparsers(dest="memory_command")
    _setup_parser = memory_sub.add_parser(
        "setup", help="Interactive provider selection and configuration"
    )
    _setup_parser.add_argument(
        "provider",
        nargs="?",
        default=None,
        help="Provider to configure directly (e.g. honcho), skipping the picker",
    )
    memory_sub.add_parser("status", help="Show current memory provider config")
    memory_sub.add_parser("off", help="Disable external provider (built-in only)")
    _reset_parser = memory_sub.add_parser(
        "reset",
        help="Erase all built-in memory (MEMORY.md and USER.md)",
    )
    _reset_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompt",
    )
    _reset_parser.add_argument(
        "--target",
        choices=["all", "memory", "user"],
        default="all",
        help="Which store to reset: 'all' (default), 'memory', or 'user'",
    )
    # list-structured subcommand
    _ls_parser = memory_sub.add_parser(
        "list-structured",
        help="List structured mirror records (MCP_MIRROR_MEMORY.jsonl)",
    )
    _ls_parser.add_argument(
        "--scope",
        choices=["user", "project"],
        default=None,
        help="Filter by scope (user or project)",
    )
    _ls_parser.add_argument(
        "--type",
        dest="memory_type",
        default=None,
        help="Filter by memory type (e.g. preference, project, rule)",
    )
    _ls_parser.add_argument(
        "--limit",
        type=int,
        default=40,
        help="Maximum number of records to display (default: 40)",
    )

    # delete-structured subcommand
    _del_parser = memory_sub.add_parser(
        "delete-structured",
        help="Delete a structured mirror record by slug",
    )
    _del_parser.add_argument(
        "slug",
        help="Slug of the record to delete (from list-structured output)",
    )
    _del_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompt",
    )

    # list-extraction-audit subcommand
    _audit_parser = memory_sub.add_parser(
        "list-extraction-audit",
        help="List extraction audit events (MCP_MIRROR_AUDIT.jsonl)",
    )
    _audit_parser.add_argument(
        "--status",
        choices=["trigger", "skip", "save", "error"],
        default=None,
        help="Filter by event status",
    )
    _audit_parser.add_argument(
        "--reason",
        default=None,
        help="Filter by reason_code",
    )
    _audit_parser.add_argument(
        "--limit",
        type=int,
        default=40,
        help="Maximum number of events to display (default: 40)",
    )

    # list-context-audit subcommand
    _ctx_audit = memory_sub.add_parser(
        "list-context-audit",
        help="List memory_context decision audit events",
    )
    _ctx_audit.add_argument(
        "--status",
        choices=["trigger", "skip", "error"],
        default=None,
        help="Filter by event status",
    )
    _ctx_audit.add_argument(
        "--reason",
        default=None,
        help="Filter by reason_code",
    )
    _ctx_audit.add_argument(
        "--limit",
        type=int,
        default=40,
        help="Maximum number of events to display (default: 40)",
    )

    # list-files subcommand
    _list_files = memory_sub.add_parser(
        "list-files",
        help="List editable memory files under HermesMemory",
    )
    _list_files.add_argument(
        "--scope",
        choices=["user", "project"],
        default=None,
        help="Filter by scope",
    )
    _list_files.add_argument(
        "--type",
        dest="memory_type",
        default=None,
        help="Filter by memory type",
    )
    _list_files.add_argument(
        "--limit",
        type=int,
        default=40,
        help="Maximum number of rows to display (default: 40)",
    )

    _reconcile_files = memory_sub.add_parser(
        "reconcile-files",
        help="Reconcile editable filesystem memory back into structured mirror",
    )

    _open_file = memory_sub.add_parser(
        "open",
        help="Resolve and print the editable file path for a memory slug",
    )
    _open_file.add_argument("slug", help="Memory slug")

    memory_parser.set_defaults(func=cmd_memory)
