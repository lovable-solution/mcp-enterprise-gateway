"""``agent-runtime``: run governed agent threads from the command line.

    agent-runtime run "Raise Alder Freight's credit limit to $650k: Q4 volumes" --wait
    agent-runtime status t-3f2a9c1e5b7d4a60
    agent-runtime resume t-3f2a9c1e5b7d4a60 --wait     # after an operator decided
    agent-runtime retry  t-3f2a9c1e5b7d4a60            # after a breaker reset

Exit codes: 0 completed, 2 waiting for approval, 3 halted by the gateway, 1 failed.
Settings come from ``AGENT_*`` variables (and ./.env); see ``agent_runtime.settings``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from . import __version__
from .agent import build_agent, claude_model
from .checkpoint import open_checkpointer
from .identity import IdentityError, token_provider
from .runner import AgentRuntime, ThreadStateError, ThreadView, UnknownThreadError
from .settings import AgentSettings
from .toolset import RuntimeEvent

EXIT_CODES = {"completed": 0, "awaiting_approval": 2, "halted": 3, "failed": 1, "running": 1}

_MARKERS = {
    "tool_call": "->",
    "tool_result": "<-",
    "progress": "..",
    "usage": " $",
    "approval": "!!",
    "status": "**",
}


async def print_event(event: RuntimeEvent) -> None:
    if event.kind == "text":
        return  # the final answer is printed with the thread summary
    detail = ""
    if event.kind == "tool_call":
        detail = " " + json.dumps(event.data.get("arguments", {}), separators=(",", ":"))[:200]
    print(f"  {_MARKERS.get(event.kind, '  ')} {event.message}{detail}", flush=True)


def _summary(view: ThreadView, session: dict[str, Any] | None) -> None:
    print(f"\nthread {view.thread_id}: {view.status} (agent runs: {view.agent_runs})")
    if view.output:
        print(f"\n{view.output}\n")
    for call in view.pending:
        print(f"  waiting: {call['tool_name']} -> approval {call['approval_id']}")
        print(f"           {json.dumps(call['arguments'], separators=(',', ':'))}")
    if view.halt:
        print(f"  halted: {view.halt['code']}: {view.halt['message']}")
    if view.error:
        print(f"  error: {view.error}")
    budget = (session or {}).get("budget") or view.budget
    if budget:
        print(
            f"  session spend: ${budget.get('cost_usd')} of ${budget.get('max_cost_usd')}"
            f" (llm ${budget.get('llm_cost_usd')}, tools ${budget.get('tool_cost_usd')}),"
            f" {budget.get('tool_calls')} tool calls, {budget.get('total_tokens')} tokens"
        )
    if view.status == "awaiting_approval":
        print(f"\n  resume with: agent-runtime resume {view.thread_id} --wait")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-runtime", description=(__doc__ or "").split("\n")[0]
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--json", action="store_true", help="print the thread as JSON")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="start a new thread")
    run.add_argument("prompt")
    run.add_argument("--thread", help="thread (= gateway session) id; default: new")
    for name, help_text in (
        ("resume", "continue a thread once its held calls are decided"),
        ("status", "show a thread"),
        ("retry", "re-run a halted or failed thread"),
    ):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("thread")
    for sub in (run, commands.choices["resume"]):
        sub.add_argument("--wait", action="store_true", help="wait for decisions and resume")
        sub.add_argument("--timeout", type=float, default=1800.0, help="seconds to --wait")
    return parser


async def _main(args: argparse.Namespace, settings: AgentSettings) -> int:
    from platform_telemetry import configure_telemetry  # noqa: PLC0415 - after settings load

    telemetry = configure_telemetry("agent-runtime", __version__, environment=settings.environment)
    try:
        model, model_settings = claude_model(settings)
        agent = build_agent(model, model_settings, trace_content=settings.trace_content)
        # Entering the agent closes the model provider's HTTP client on the way out.
        async with agent, open_checkpointer(settings) as checkpointer:
            runtime = AgentRuntime(
                agent=agent,
                checkpointer=checkpointer,
                tokens=token_provider(settings),
                gateway_url=settings.gateway_url,
                gateway_timeout_seconds=settings.gateway_timeout_seconds,
                max_model_requests=settings.max_model_requests,
                approval_poll_seconds=settings.approval_poll_seconds,
                emit=print_event,
            )
            view = await _dispatch(runtime, args)
            if args.json:
                print(json.dumps(_as_dict(view), default=str))
            else:
                _summary(view, await runtime.session_status(view.thread_id))
            return EXIT_CODES.get(view.status, 1)
    finally:
        telemetry.shutdown()


async def _dispatch(runtime: AgentRuntime, args: argparse.Namespace) -> ThreadView:
    match args.command:
        case "run":
            view = await runtime.start(args.prompt, thread_id=args.thread)
        case "resume":
            view = await runtime.resume(args.thread)
        case "retry":
            return await runtime.retry(args.thread)
        case _:
            return await runtime.view(args.thread)
    if getattr(args, "wait", False) and view.status == "awaiting_approval":
        print(f"  .. waiting up to {args.timeout:.0f}s for a reviewer", flush=True)
        view = await runtime.wait(view.thread_id, timeout_seconds=args.timeout)
    return view


def _as_dict(view: ThreadView) -> dict[str, Any]:
    return {name: getattr(view, name) for name in view.__slots__}


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = AgentSettings()
    except ValidationError as exc:
        print(f"configuration error:\n{exc}", file=sys.stderr)
        return 1
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(levelname)s %(name)s: %(message)s"
    )
    if settings.log_level != "debug":  # one line per HTTP request drowns the run's own output
        for noisy in ("httpx2", "httpx", "mcp"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        return asyncio.run(_main(args, settings))
    except (UnknownThreadError, ThreadStateError, IdentityError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted: the thread keeps its last checkpoint", file=sys.stderr)
        return 130
