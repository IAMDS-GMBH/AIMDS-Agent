from __future__ import annotations

import argparse

from hermes_cli.subcommands.memory import build_memory_parser


def _handler(_args):
    return None


def test_memory_parser_list_extraction_audit_defaults():
    parser = argparse.ArgumentParser(prog="hermes")
    sub = parser.add_subparsers(dest="command")
    build_memory_parser(sub, cmd_memory=_handler)

    ns = parser.parse_args(["memory", "list-extraction-audit"])
    assert ns.command == "memory"
    assert ns.memory_command == "list-extraction-audit"
    assert ns.status is None
    assert ns.reason is None
    assert ns.limit == 40


def test_memory_parser_list_extraction_audit_filters():
    parser = argparse.ArgumentParser(prog="hermes")
    sub = parser.add_subparsers(dest="command")
    build_memory_parser(sub, cmd_memory=_handler)

    ns = parser.parse_args(
        [
            "memory",
            "list-extraction-audit",
            "--status",
            "skip",
            "--reason",
            "skip_prefilter",
            "--limit",
            "10",
        ]
    )
    assert ns.memory_command == "list-extraction-audit"
    assert ns.status == "skip"
    assert ns.reason == "skip_prefilter"
    assert ns.limit == 10
