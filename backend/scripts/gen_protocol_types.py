#!/usr/bin/env python
"""Emit the TypeScript view of the wire protocol from the msgspec structs.

The client speaks 53 message types. Hand-writing them in TypeScript and keeping
them in sync with `chainlit/protocol/` by review is the largest avoidable cost
in the rebuild -- a drift shows up as a runtime shape mismatch in the browser,
not as a build failure.

Run `python scripts/gen_protocol_types.py` to regenerate, or with `--check` to
fail when the committed file is stale. CI runs the latter.

The emitter walks msgspec's own JSON Schema, so the tags, the discriminator and
the field optionality are whatever msgspec actually encodes -- not a second
description of it that can disagree.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import msgspec

from chainlit.protocol.client import ClientMsg
from chainlit.protocol.server import ServerMsg

OUTPUT = (
    Path(__file__).resolve().parents[2]
    / "libs"
    / "react-client"
    / "src"
    / "protocol"
    / "messages.ts"
)

HEADER = """// Generated from backend/chainlit/protocol by scripts/gen_protocol_types.py.
// Do not edit: run `python scripts/gen_protocol_types.py` in backend/ instead.
//
// `ServerMsg` and `ClientMsg` are discriminated on `t`, so a switch over it
// narrows to one branch and a missing case is a compile error.
"""


def ts_name(component: str) -> str:
    """Turn a msgspec component name into a legal TypeScript identifier.

    msgspec disambiguates a name used in both unions by qualifying it with the
    module path -- `chainlit.protocol.client.WindowMessage`. Today that happens
    to exactly one struct, because `window.message` is the single tag that
    travels in both directions. Fold the direction into the name instead.
    """
    if "." not in component:
        return component
    *path, leaf = component.split(".")
    direction = "Client" if "client" in path else "Server" if "server" in path else ""
    return f"{direction}{leaf}"


def ts_type(schema: Dict[str, Any]) -> str:
    """Render one JSON Schema node as a TypeScript type."""
    if "$ref" in schema:
        return ts_name(schema["$ref"].rsplit("/", 1)[-1])

    if "anyOf" in schema:
        parts = [ts_type(option) for option in schema["anyOf"]]
        # msgspec spells an optional field as anyOf[..., {"type": "null"}].
        return " | ".join(dict.fromkeys(parts))

    if "enum" in schema:
        return " | ".join(msgspec.json.encode(v).decode() for v in schema["enum"])

    if "const" in schema:
        return msgspec.json.encode(schema["const"]).decode()

    kind: str = schema.get("type", "")
    if kind == "array":
        items = schema.get("items")
        if not items:
            return "unknown[]"
        rendered = ts_type(items)
        # `A | B[]` parses as `A | (B[])`, so a list of a union has to be
        # parenthesised or the generated type silently means something else.
        # Element is a union of eleven branches, which is how Thread.elements
        # came out as "ten element types, or an array of the eleventh".
        return f"({rendered})[]" if " | " in rendered else f"{rendered}[]"
    if kind == "object":
        values = schema.get("additionalProperties")
        return f"Record<string, {ts_type(values) if values else 'unknown'}>"

    return {
        "string": "string",
        "integer": "number",
        "number": "number",
        "boolean": "boolean",
        "null": "null",
    }.get(kind, "unknown")


def emit_interface(name: str, schema: Dict[str, Any]) -> str:
    """Render one struct as a TypeScript interface."""
    required = set(schema.get("required", ()))
    lines = [f"export interface {name} {{"]

    for field, field_schema in schema.get("properties", {}).items():
        optional = "" if field in required else "?"
        rendered = ts_type(field_schema)
        if description := field_schema.get("description"):
            lines.append(f"  /** {description.strip().splitlines()[0]} */")
        lines.append(f"  {field}{optional}: {rendered};")

    if len(lines) == 1:
        # msgspec emits no `properties` for a struct with no fields.
        lines.append("  // no fields")
    lines.append("}")
    return "\n".join(lines)


def emit_union(name: str, schema: Dict[str, Any]) -> str:
    branches = [ts_type(option) for option in schema["anyOf"]]
    joined = "\n  | ".join(branches)
    tag_map = schema.get("discriminator", {}).get("mapping", {})
    tags = "\n  | ".join(msgspec.json.encode(tag).decode() for tag in tag_map)
    return (
        f"export type {name} =\n  | {joined};\n\n"
        f"export type {name}Tag =\n  | {tags};\n\n"
        f"/** Exhaustive handler table: omitting a message is a compile error. */\n"
        f"export type {name}Handlers = {{\n"
        f"  [K in {name}['t']]: (message: Extract<{name}, {{ t: K }}>) => void;\n"
        f"}};"
    )


def render() -> str:
    schemas, components = msgspec.json.schema_components(
        [ServerMsg, ClientMsg], ref_template="#/$defs/{name}"
    )

    blocks: List[str] = [HEADER]
    for component_name in sorted(components):
        blocks.append(
            emit_interface(ts_name(component_name), components[component_name])
        )
    blocks.append(emit_union("ServerMsg", schemas[0]))
    blocks.append(emit_union("ClientMsg", schemas[1]))

    return "\n\n".join(blocks) + "\n"


def prettify(source: str) -> str:
    """Run the repo's prettier over the output so the committed file is stable.

    Without this the check mode fails the moment a developer's editor formats
    the generated file on save.
    """
    try:
        result = subprocess.run(
            ["npx", "--no-install", "prettier", "--parser", "typescript"],
            input=source,
            capture_output=True,
            text=True,
            cwd=OUTPUT.parents[4],
            timeout=120,
        )
    except OSError, subprocess.TimeoutExpired:
        return source
    return result.stdout if result.returncode == 0 and result.stdout else source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero when the committed file is out of date",
    )
    args = parser.parse_args()

    generated = prettify(render())

    if args.check:
        current = OUTPUT.read_text() if OUTPUT.exists() else ""
        if current != generated:
            print(
                f"{OUTPUT.relative_to(OUTPUT.parents[4])} is out of date.\n"
                f"Run `python scripts/gen_protocol_types.py` in backend/ and commit "
                f"the result.",
                file=sys.stderr,
            )
            return 1
        print(f"{OUTPUT.name} is up to date")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(generated)
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
