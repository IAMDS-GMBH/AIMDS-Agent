"""TypeScript types for the contracts (AIS-797), without a generator dependency.

Covers the JSON Schema subset the contracts use: objects with properties,
``$defs`` + local ``$ref`` (``#`` is the root), ``enum``/``const``, arrays,
nullable type lists and free-form objects. Keywords that only constrain
values (``maxLength``, ``anyOf`` of ``required`` …) do not change the type and
are left to the schema. The output follows ``apps/shared`` style: two-space
indent, single quotes, no semicolons, members sorted.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from agent.contracts import NAMES, load_schema, version

TARGET = "apps/shared/src/contracts.ts"
_IDENT = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def _literal(value: Any) -> str:
    if isinstance(value, str):
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    return json.dumps(value)


def _doc(text: str, indent: str) -> List[str]:
    text = " ".join(str(text or "").split())
    return [f"{indent}/** {text} */"] if text else []


class _Renderer:
    def __init__(self, root_title: str):
        self.root_title = root_title

    def type_of(self, node: Dict[str, Any], indent: str = "") -> str:
        ref = node.get("$ref")
        if ref is not None:
            if ref == "#":
                return self.root_title
            if ref.startswith("#/$defs/"):
                return ref[len("#/$defs/"):]
            raise ValueError(f"unsupported $ref: {ref}")
        if "const" in node:
            return _literal(node["const"])
        if "enum" in node:
            return " | ".join(_literal(v) for v in node["enum"])
        kind = node.get("type")
        if isinstance(kind, list):
            return " | ".join(self.type_of({**node, "type": k}, indent) for k in kind)
        if kind == "string":
            return "string"
        if kind in ("integer", "number"):
            return "number"
        if kind == "boolean":
            return "boolean"
        if kind == "null":
            return "null"
        if kind == "array":
            inner = self.type_of(node.get("items") or {}, indent)
            return f"({inner})[]" if " | " in inner else f"{inner}[]"
        if kind == "object":
            if node.get("properties"):
                return "{\n" + "\n".join(self.members(node, indent + "  ")) + f"\n{indent}}}"
            extra = node.get("additionalProperties")
            value = self.type_of(extra, indent) if isinstance(extra, dict) else "unknown"
            return f"Record<string, {value}>"
        return "unknown"

    def members(self, node: Dict[str, Any], indent: str) -> List[str]:
        required = set(node.get("required") or ())
        lines: List[str] = []
        for name in sorted(node["properties"]):
            prop = node["properties"][name]
            key = name if _IDENT.match(name) else _literal(name)
            optional = "" if name in required else "?"
            lines.extend(_doc(prop.get("description", ""), indent))
            lines.append(f"{indent}{key}{optional}: {self.type_of(prop, indent)}")
        return lines

    def declaration(self, name: str, node: Dict[str, Any]) -> List[str]:
        lines = _doc(node.get("description", ""), "")
        if node.get("type") == "object" and node.get("properties"):
            lines.append(f"export interface {name} {{")
            lines.extend(self.members(node, "  "))
            lines.append("}")
        else:
            lines.append(f"export type {name} = {self.type_of(node)}")
        return lines


def render() -> str:
    """The full ``contracts.ts`` text."""
    out: List[str] = [
        "// Generated from agent/contracts/*.schema.json by scripts/gen_contract_types.py.",
        "// Do not edit by hand: change the schema and run the script.",
        "",
        "export const CONTRACT_VERSIONS = {",
    ]
    out.extend(f"  {name}: {version(name)}," for name in NAMES)
    out[-1] = out[-1].rstrip(",")
    out.extend(["} as const", ""])
    seen: Dict[str, str] = {}
    for contract in NAMES:
        schema = load_schema(contract)
        renderer = _Renderer(schema["title"])
        out.append(f"// ── {contract} ──")
        out.append("")
        for name, node in [*schema.get("$defs", {}).items(), (schema["title"], schema)]:
            if name in seen:
                raise ValueError(f"type {name} defined by {seen[name]} and {contract}")
            seen[name] = contract
            out.extend(renderer.declaration(name, node))
            out.append("")
    return "\n".join(out).rstrip() + "\n"
