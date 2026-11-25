"""Live: Claude (``claude-opus-5-5``) through the real gateway. Costs money; opt in with
``make test-live``. Asserts behaviour, not wording, and prints what the run cost."""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import httpx2
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr

from agent_runtime.agent import build_agent, claude_model
from agent_runtime.runner import AgentRuntime
from agent_runtime.settings import AgentSettings
from agent_runtime.toolset import RuntimeEvent

from .conftest import CrmOps, Gateway, _dotenv, minter

pytestmark = [pytest.mark.live, pytest.mark.integration, pytest.mark.anyio]


def live_settings() -> AgentSettings:
    key = _dotenv().get("ANTHROPIC_API_KEY") or ""
    if not key:
        pytest.fail("ANTHROPIC_API_KEY is not set in .env")
    return AgentSettings(
        _env_file=None,
        anthropic_api_key=SecretStr(key),
        dev_jwt_secret=SecretStr("unused-" + "x" * 40),
        checkpointer="memory",
    )


async def test_claude_requests_a_change_waits_for_approval_and_reports_it(
    gateway: Gateway, operator: httpx2.AsyncClient, crm_ops: CrmOps
) -> None:
    events: list[RuntimeEvent] = []

    async def record(event: RuntimeEvent) -> None:
        events.append(event)
        print(f"  [{event.kind}] {event.message}")  # noqa: T201 - live runs are watched

    model, model_settings = claude_model(live_settings())
    agent = build_agent(model, model_settings)
    async with agent:  # closes the Anthropic HTTP client afterwards
        await _run(agent, events, record, gateway, operator, crm_ops)


async def _run(
    agent: Any,
    events: list[RuntimeEvent],
    record: Any,
    gateway: Gateway,
    operator: httpx2.AsyncClient,
    crm_ops: CrmOps,
) -> None:
    runtime = AgentRuntime(
        agent=agent,
        checkpointer=InMemorySaver(),
        tokens=minter("admin", subject="crm-copilot-live"),
        gateway_url=gateway.url,
        gateway_timeout_seconds=60.0,
        emit=record,
    )
    started = time.monotonic()
    paused = await runtime.start(
        "Cedar Analytics has signed a two-year renewal. Check its receivables and credit "
        "utilisation, then request raising its credit limit to $90,000."
    )
    assert paused.status == "awaiting_approval", paused
    [held] = paused.pending
    assert held["tool_name"] == "crm-ops__update_credit_limit"
    assert held["arguments"]["account_id"] == "ACC-1003"
    assert held["arguments"]["new_limit_usd"] == 90_000
    assert crm_ops.store.credit_limit("acme", "ACC-1003") != 90_000  # held, not executed

    decided = await operator.post(
        f"/v1/approvals/{held['approval_id']}/decide",
        json={"decision": "approve", "reason": "renewal verified"},
    )
    assert decided.json()["status"] == "EXECUTED"
    done = await runtime.resume(paused.thread_id)
    elapsed = time.monotonic() - started

    assert done.status == "completed", done
    assert crm_ops.store.credit_limit("acme", "ACC-1003") == 90_000
    assert done.output is not None
    assert "90,000" in done.output or "90000" in done.output or "$90k" in done.output.lower()

    session: dict[str, Any] | None = await runtime.session_status(paused.thread_id)
    assert session is not None
    budget = session["budget"]
    models = {e.data["report"]["model"] for e in events if e.kind == "usage"}
    print(  # noqa: T201
        f"\nlive run: {elapsed:.1f}s, models {sorted(models)}, "
        f"{budget['input_tokens']} input + {budget['output_tokens']} output tokens "
        f"(cache read {budget['cache_read_tokens']}, write {budget['cache_write_tokens']}), "
        f"LLM ${budget['llm_cost_usd']}, tools ${budget['tool_cost_usd']}\n\n{done.output}"
    )
    assert Decimal(budget["llm_cost_usd"]) > 0
    assert models  # every response was reported
