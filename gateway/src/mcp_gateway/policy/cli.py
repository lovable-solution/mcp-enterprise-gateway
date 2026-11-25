"""``mcp-gateway-policy``: validate, inspect and export the gateway policy.

mcp-gateway-policy validate config/policies.yaml
mcp-gateway-policy matrix   config/policies.yaml
mcp-gateway-policy schema   --output config/policies.schema.json [--check]
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .engine import PolicyEngine
from .loader import LoadedPolicy, PolicyLoadError, load_policy
from .models import ControlAction
from .schema import render_policy_json_schema


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result: int = args.handler(args)
    except PolicyLoadError as exc:
        print(exc, file=sys.stderr)
        return 1
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mcp-gateway-policy", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="validate a policy file (exit 1 if invalid)")
    validate.add_argument("path", type=Path)
    validate.set_defaults(handler=_validate)

    matrix = commands.add_parser("matrix", help="print the effective role x tool access matrix")
    matrix.add_argument("path", type=Path)
    matrix.set_defaults(handler=_matrix)

    schema = commands.add_parser("schema", help="emit the JSON Schema for policies.yaml")
    schema.add_argument("--output", "-o", type=Path, help="write to a file instead of stdout")
    schema.add_argument(
        "--check", action="store_true", help="exit 1 if --output is missing or out of date"
    )
    schema.set_defaults(handler=_schema)
    return parser


def _validate(args: argparse.Namespace) -> int:
    loaded = load_policy(args.path)
    doc = loaded.document
    gated = sum(spec.requires_approval for spec in doc.tools.values())
    print(
        f"OK  {loaded.source}: policy {doc.metadata.name!r} ({loaded.digest[:19]}), "
        f"{len(doc.roles)} roles, {len(doc.tools)} tools ({gated} behind approval)"
    )
    return 0


def _matrix(args: argparse.Namespace) -> int:
    loaded = load_policy(args.path)
    print(_render_matrix(loaded))
    return 0


def _schema(args: argparse.Namespace) -> int:
    rendered = render_policy_json_schema()
    if args.check:
        if args.output is None:
            print("--check requires --output", file=sys.stderr)
            return 2
        current = args.output.read_text(encoding="utf-8") if args.output.exists() else None
        if current != rendered:
            print(f"{args.output} is out of date; run: make schema", file=sys.stderr)
            return 1
        print(f"OK  {args.output} is up to date")
        return 0
    if args.output is None:
        sys.stdout.write(rendered)
    else:
        args.output.write_text(rendered, encoding="utf-8")
        print(f"wrote {args.output}")
    return 0


def _render_matrix(loaded: LoadedPolicy) -> str:
    """Role x tool table (ALLOW, HITL = allowed after approval, '-' = denied) followed by
    role x control-plane action table."""
    doc = loaded.document
    engine = PolicyEngine(doc)
    roles = list(doc.roles)

    def cell(role: str, tool: str) -> str:
        decision = engine.check_tool(role, tool)
        if not decision.allowed:
            return "-"
        return "HITL" if decision.requires_approval else "ALLOW"

    tool_rows = [
        [tool, spec.access.value, spec.risk.value, *(cell(r, tool) for r in roles)]
        for tool, spec in doc.tools.items()
    ]
    action_rows = [
        [action.value, *("yes" if engine.allows_action(r, action) else "-" for r in roles)]
        for action in ControlAction
    ]
    return "\n".join(
        [
            f"policy {doc.metadata.name!r}  {loaded.digest[:19]}",
            "",
            _table(["tool", "access", "risk", *roles], tool_rows),
            "",
            _table(["control-plane action", *roles], action_rows),
            "",
            "ALLOW = allowed   HITL = allowed after human approval   - = denied",
        ]
    )


def _table(header: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]
    lines = [
        "  ".join(value.ljust(width) for value, width in zip(row, widths, strict=True))
        for row in [header, *rows]
    ]
    lines.insert(1, "  ".join("-" * width for width in widths))
    return "\n".join(line.rstrip() for line in lines)


if __name__ == "__main__":
    raise SystemExit(main())
