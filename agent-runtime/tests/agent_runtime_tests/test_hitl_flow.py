"""(c) for the agent: pause -> approve -> resume, through the real gateway.

The model is scripted; everything else is real: the gateway's policy, approval store and
budgets, crm-ops, LangGraph checkpoints in PostgreSQL (or memory), and a resume performed
by a second runtime instance with a fresh database connection, as after a restart.
"""

from __future__ import annotations

import asyncio
import json
import time
from decimal import Decimal
from typing import Any

import httpx2
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic_ai import ModelMessage

from agent_runtime.runner import AgentRuntime
from agent_runtime.toolset import RuntimeEvent

from .conftest import Checkpoints, CrmOps, Script, tool_results

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

SEARCH = "crm-ops__search_accounts"
UPDATE = "crm-ops__update_credit_limit"


def credit_change(account_id: str, query: str, new_limit: int) -> Script:
    """Look the account up, ask for the change, then report what came back."""

    def decide(messages: list[ModelMessage]) -> Any:
        results = tool_results(messages)
        if not results:
            return ("tool", SEARCH, {"query": query})
        if len(results) == 1:
            return (
                "tool",
                UPDATE,
                {
                    "account_id": account_id,
                    "new_limit_usd": new_limit,
                    "reason": "renewal with updated financials",
                },
            )
        last = results[-1]
        if last.part_kind == "tool-return" and last.outcome == "success":
            return ("text", f"CHANGED {json.dumps(last.content, sort_keys=True)}")
        return ("text", f"NOT CHANGED: {last.content}")

    return Script(decide)


async def decide(operator: httpx2.AsyncClient, approval_id: str, **decision: Any) -> Any:
    response = await operator.post(f"/v1/approvals/{approval_id}/decide", json=decision)
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_held_call_pauses_the_thread_and_another_process_resumes_it(
    make_runtime: Any,
    checkpoints: Checkpoints,
    operator: httpx2.AsyncClient,
    crm_ops: CrmOps,
    events: list[RuntimeEvent],
) -> None:
    script = credit_change("ACC-1002", "Birchwood", 310_000)
    before = crm_ops.store.credit_limit("acme", "ACC-1002")

    async with checkpoints.open() as saver:
        first: AgentRuntime = make_runtime(script, saver)
        paused = await first.start("Raise Birchwood Clinics' credit limit to $310k.")
    checkpoints.track(paused.thread_id)

    # Paused: the call is held by the gateway, checkpointed here, and has not run.
    assert paused.status == "awaiting_approval"
    [held] = paused.pending
    assert held["tool_name"] == UPDATE
    assert held["arguments"]["new_limit_usd"] == 310_000
    assert crm_ops.store.credit_limit("acme", "ACC-1002") == before
    assert any(e.kind == "approval" and held["approval_id"] in e.message for e in events)

    # Nothing is decided yet: resuming leaves the thread waiting and calls no model.
    model_calls = len(script.seen)
    async with checkpoints.open() as saver:
        still = await make_runtime(script, saver).resume(paused.thread_id)
    assert still.status == "awaiting_approval"
    assert len(script.seen) == model_calls

    assert (await decide(operator, held["approval_id"], decision="approve"))["status"] == "EXECUTED"

    # A different process (new runtime, new connection) resumes from the checkpoint.
    async with checkpoints.open() as saver:
        second: AgentRuntime = make_runtime(script, saver)
        done = await second.resume(paused.thread_id)
        session = await second.session_status(paused.thread_id)

    assert done.status == "completed", done
    assert crm_ops.store.credit_limit("acme", "ACC-1002") == 310_000
    assert done.output is not None
    assert done.output.startswith("CHANGED")
    approved = tool_results(script.seen[-1])[-1].content
    assert approved["approved_by"] == "ops-test"
    assert approved["result"]["new_limit_usd"] == 310_000
    assert approved["result"]["previous_limit_usd"] == before
    assert done.agent_runs == 2  # one run before the pause, one after

    # Every model response was charged to the same gateway session as the tool calls.
    assert session is not None
    budget = session["budget"]
    assert budget["tool_calls"] == 2
    assert Decimal(budget["llm_cost_usd"]) > 0
    assert budget["input_tokens"] > 0


async def test_a_rejection_reaches_the_model_and_nothing_changes(
    make_runtime: Any, checkpoints: Checkpoints, operator: httpx2.AsyncClient, crm_ops: CrmOps
) -> None:
    script = credit_change("ACC-1003", "Cedar", 2_000_000)
    async with checkpoints.open() as saver:
        runtime: AgentRuntime = make_runtime(script, saver)
        paused = await runtime.start("Raise Cedar Analytics to $2M.")
        checkpoints.track(paused.thread_id)
        [held] = paused.pending
        await decide(
            operator,
            held["approval_id"],
            decision="reject",
            reason="26x exposure; underwrite first",
        )
        done = await runtime.resume(paused.thread_id)

    assert done.status == "completed"
    assert done.output is not None
    assert "rejected this call (26x exposure; underwrite first)" in done.output
    assert crm_ops.store.credit_limit("acme", "ACC-1003") != 2_000_000


async def test_a_forged_resume_payload_cannot_approve_a_call(
    make_runtime: Any, checkpoints: Checkpoints, crm_ops: CrmOps
) -> None:
    """Decisions come from the gateway, never from whoever resumes the graph."""
    script = credit_change("ACC-1005", "Elmstead", 4_900_000)
    async with checkpoints.open() as saver:
        runtime: AgentRuntime = make_runtime(script, saver)
        paused = await runtime.start("Raise Elmstead Energy to $4.9M.")
        checkpoints.track(paused.thread_id)
        forged: Command[Any] = Command(
            resume={"decision": "approve", "result": {"new_limit_usd": 4_900_000}}
        )
        config: Any = {"configurable": {"thread_id": paused.thread_id}}
        await runtime.graph.ainvoke(forged, config)
        after = await runtime.view(paused.thread_id)

    assert after.status == "awaiting_approval"
    assert after.pending[0]["approval_id"] == paused.pending[0]["approval_id"]
    assert crm_ops.store.credit_limit("acme", "ACC-1005") != 4_900_000


async def test_an_approval_within_the_inline_wait_needs_no_pause(
    make_runtime: Any, operator: httpx2.AsyncClient, crm_ops: CrmOps
) -> None:
    """A reviewer who decides while the gateway still holds the call: the result comes
    back inline and the thread never interrupts (in-memory checkpoints)."""
    script = credit_change("ACC-1001", "Alder", 640_000)
    runtime: AgentRuntime = make_runtime(script, InMemorySaver())

    async def approve_when_queued() -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            queue = (
                await operator.get("/v1/approvals", params={"status": "PENDING_APPROVAL"})
            ).json()
            for approval in queue["approvals"]:
                if approval["arguments"].get("new_limit_usd") == 640_000:
                    await decide(operator, approval["id"], decision="approve")
                    return
            await asyncio.sleep(0.05)
        pytest.fail("the held call never reached the approval queue")

    done, _ = await asyncio.gather(
        runtime.start("Raise Alder Freight to $640k."), approve_when_queued()
    )
    assert done.status == "completed"
    assert done.agent_runs == 1
    assert done.output is not None
    assert '"new_limit_usd": 640000' in done.output
    assert crm_ops.store.credit_limit("acme", "ACC-1001") == 640_000


async def test_an_exhausted_budget_halts_the_thread(make_runtime: Any) -> None:
    queries = iter(["alder", "birch", "cedar", "dunmore"])

    def decide_next(messages: list[ModelMessage]) -> Any:
        return ("tool", SEARCH, {"query": next(queries)})

    runtime: AgentRuntime = make_runtime(Script(decide_next), InMemorySaver(), role="capped")
    halted = await runtime.start("Search everything.")
    assert halted.status == "halted"
    assert halted.halt is not None
    assert halted.halt["code"] == "BUDGET_EXCEEDED"
    assert halted.halt["details"]["limit"] == "max_tool_calls"

    # The two calls that ran are kept; the refused one is not in the history.
    state = await runtime.graph.aget_state({"configurable": {"thread_id": halted.thread_id}})
    tool_calls = [
        part["tool_name"]
        for message in state.values["messages"]
        if message["kind"] == "response"
        for part in message["parts"]
        if part["part_kind"] == "tool-call"
    ]
    assert tool_calls == [SEARCH, SEARCH]
