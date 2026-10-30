"""Versioned data contracts shared by the backend and the desktop (AIS-797).

Each ``<name>.schema.json`` here is a JSON Schema (draft 2020-12) with a
``urn:aimds-agent:contract:<name>:<version>`` id and a ``v`` const field in
the payload. The schemas are the single source of truth: the TypeScript
types in ``apps/shared/src/contracts.ts`` are generated from them
(``scripts/gen_contract_types.py``; a test fails when the checked-in file is
stale), and tests validate what the backend emits against them.

A breaking change bumps the version in ``$id`` and ``v``; adding an optional
field does not.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

CONTRACTS_DIR = Path(__file__).resolve().parent

#: Contract names in generation order.
NAMES = (
    "ledger_entry",
    "workspace_feed",
    "clarify_questions",
    "presence",
    "agent_envelope",
    "feedback_signature",
)


def schema_path(name: str) -> Path:
    if name not in NAMES:
        raise KeyError(f"unknown contract: {name}")
    return CONTRACTS_DIR / f"{name}.schema.json"


@lru_cache(maxsize=None)
def _load(name: str) -> str:
    return schema_path(name).read_text(encoding="utf-8")


def load_schema(name: str) -> Dict[str, Any]:
    """The parsed schema (a fresh copy, callers may mutate it)."""
    return json.loads(_load(name))


def version(name: str) -> int:
    return int(load_schema(name)["$id"].rsplit(":", 1)[1])


def validate(name: str, payload: Any, *, definition: str = "") -> None:
    """Raise ``jsonschema.ValidationError`` when *payload* breaks the contract.

    *definition* validates against one ``$defs`` entry (e.g. ``LedgerPage``)
    instead of the root. jsonschema is imported lazily: the runtime never
    validates, only tests and tooling do.
    """
    import jsonschema
    from referencing import Registry, Resource

    schema = load_schema(name)
    # Registered under its $id so "#" inside a definition still means the root.
    registry = Registry().with_resource(schema["$id"], Resource.from_contents(schema))
    target = schema["$id"] + (f"#/$defs/{definition}" if definition else "")
    jsonschema.Draft202012Validator({"$ref": target}, registry=registry).validate(payload)
