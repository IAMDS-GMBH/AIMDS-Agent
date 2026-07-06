"""``hermes support`` subcommand parser."""

from __future__ import annotations

import argparse
from typing import Callable


def build_support_parser(subparsers, *, cmd_support: Callable) -> None:
    support_parser = subparsers.add_parser(
        "support",
        help="Support tooling (send diagnostic log dumps)",
        description="Create and upload a redacted support log bundle.",
    )
    support_sub = support_parser.add_subparsers(dest="support_action")

    send_logs = support_sub.add_parser(
        "send-logs",
        help="Send a redacted support log bundle to the configured support server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  hermes support send-logs\n"
            "  hermes support send-logs --reason boot_failure\n"
            "  hermes support send-logs --json\n"
            "  hermes support send-logs --url https://support.example/upload --api-key $TOKEN\n"
        ),
    )
    send_logs.add_argument("--reason", default="manual", help="Reason label included in upload metadata")
    send_logs.add_argument("--max-lines", type=int, default=1200, help="Max lines per log file to include")
    send_logs.add_argument("--timeout", type=int, default=45, help="HTTP timeout in seconds")
    send_logs.add_argument("--url", default="", help="Override support upload URL")
    send_logs.add_argument("--api-key", default="", help="Override support API key")
    send_logs.add_argument("--output", default=None, help="Keep bundle zip at this path instead of temp cleanup")
    send_logs.add_argument("--no-dump", dest="include_dump", action="store_false", help="Skip hermes dump text")
    send_logs.add_argument("--json", action="store_true", help="Emit JSON result")
    send_logs.set_defaults(include_dump=True)

    support_parser.set_defaults(func=cmd_support)

