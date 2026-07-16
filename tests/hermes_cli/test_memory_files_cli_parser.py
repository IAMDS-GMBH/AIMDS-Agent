from __future__ import annotations

import argparse

from hermes_cli.subcommands.memory import build_memory_parser


def _handler(_args):
    return None


def test_memory_parser_list_files_defaults():
    parser = argparse.ArgumentParser(prog="hermes")
    sub = parser.add_subparsers(dest="command")
    build_memory_parser(sub, cmd_memory=_handler)
    ns = parser.parse_args(["memory", "list-files"])
    assert ns.command == "memory"
    assert ns.memory_command == "list-files"
    assert ns.scope is None
    assert ns.memory_type is None
    assert ns.limit == 40


def test_memory_parser_reconcile_files():
    parser = argparse.ArgumentParser(prog="hermes")
    sub = parser.add_subparsers(dest="command")
    build_memory_parser(sub, cmd_memory=_handler)
    ns = parser.parse_args(["memory", "reconcile-files"])
    assert ns.memory_command == "reconcile-files"


def test_memory_parser_open_slug():
    parser = argparse.ArgumentParser(prog="hermes")
    sub = parser.add_subparsers(dest="command")
    build_memory_parser(sub, cmd_memory=_handler)
    ns = parser.parse_args(["memory", "open", "profile-language"])
    assert ns.memory_command == "open"
    assert ns.slug == "profile-language"
