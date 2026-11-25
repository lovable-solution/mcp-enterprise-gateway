"""High-level API over the graph: start a thread, resume it after a decision, inspect it.

A thread is one agent session: its id is the session id (``sid``) the gateway budgets,
rate-limits and audits. Every operation reloads the thread from the checkpointer, so any
process (a CLI, a worker, a different replica) can resume a thread another one started.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command

from .agent import AgentType
from .gateway import GatewaySession
from .graph import GraphDeps, PendingCall, ThreadState, approval_outcome, build_graph
from .identity import TokenProvider, check_session_id
from .toolset import EventSink, discard


class UnknownThreadError(LookupError):
    """No checkpoint exists for this thread id."""


class ThreadStateError(RuntimeError):
    """The thread is not in a state that allows the requested operation."""


@dataclass(frozen=True, slots=True)
class ThreadView:
    thread_id: str
    status: str
    output: str | None = None
    pending: list[PendingCall] = field(default_factory=list)
    error: str | None = None
    halt: dict[str, Any] | None = None
    budget: dict[str, Any] | None = None
    agent_runs: int = 0
    next_nodes: tuple[str, ...] = ()

    @property
    def finished(self) -> bool:
        return self.status in {"completed", "halted", "failed"}


class AgentRuntime:
    def __init__(
        self,
        *,
        agent: AgentType,
        checkpointer: BaseCheckpointSaver[Any],
        tokens: TokenProvider,
        gateway_url: str,
        gateway_timeout_seconds: float = 60.0,
        max_model_requests: int = 30,
        approval_poll_seconds: float = 2.0,
        emit: EventSink = discard,
        gateway_transport: Any = None,
    ) -> None:
        self._tokens = tokens
        self._gateway_url = gateway_url
        self._gateway_timeout = gateway_timeout_seconds
        self._poll = approval_poll_seconds
        self._transport = gateway_transport
        self.graph = build_graph(
            GraphDeps(
                agent=agent,
                tokens=tokens,
                gateway_url=gateway_url,
                gateway_timeout_seconds=gateway_timeout_seconds,
                max_model_requests=max_model_requests,
                emit=emit,
                gateway_transport=gateway_transport,
            ),
            checkpointer,
        )

    # --- operations --------------------------------------------------------------------
    async def start(self, prompt: str, *, thread_id: str | None = None) -> ThreadView:
        thread_id = check_session_id(thread_id or self._tokens.new_session_id())
        if (await self.graph.aget_state(_config(thread_id))).values:
            msg = f"thread {thread_id} already exists; resume it instead"
            raise ThreadStateError(msg)
        initial: ThreadState = {"prompt": prompt, "status": "running", "agent_runs": 0}
        await self.graph.ainvoke(initial, _config(thread_id), durability="sync")
        return await self.view(thread_id)

    async def resume(self, thread_id: str) -> ThreadView:
        """Continue a paused thread once at least one of its held calls is decided."""
        view = await self.view(thread_id)
        if view.status != "awaiting_approval" or not view.next_nodes:
            return view
        if not await self._any_decided(thread_id, view.pending):
            return view
        await self.graph.ainvoke(
            Command(resume={"resumed_at": time.time()}), _config(thread_id), durability="sync"
        )
        return await self.view(thread_id)

    async def wait(self, thread_id: str, *, timeout_seconds: float) -> ThreadView:
        """Resume as decisions arrive until the thread finishes or the timeout passes."""
        deadline = time.monotonic() + timeout_seconds
        view = await self.resume(thread_id)
        while view.status == "awaiting_approval" and time.monotonic() < deadline:
            await asyncio.sleep(self._poll)
            view = await self.resume(thread_id)
        return view

    async def retry(self, thread_id: str) -> ThreadView:
        """Run a halted or failed thread again from its last consistent point (after an
        operator reset the circuit breaker, or a transient failure cleared)."""
        view = await self.view(thread_id)
        if view.status not in {"halted", "failed"}:
            msg = f"thread {thread_id} is {view.status}; only halted or failed threads retry"
            raise ThreadStateError(msg)
        config = _config(thread_id)
        retry: ThreadState = {"status": "running", "error": None, "halt": None, "pending": []}
        await self.graph.aupdate_state(config, retry, as_node="await_approval")
        await self.graph.ainvoke(None, config, durability="sync")
        return await self.view(thread_id)

    async def view(self, thread_id: str) -> ThreadView:
        snapshot = await self.graph.aget_state(_config(thread_id))
        values = cast(ThreadState, snapshot.values or {})
        if not values:
            msg = f"no thread {thread_id}"
            raise UnknownThreadError(msg)
        return ThreadView(
            thread_id=thread_id,
            status=values.get("status", "running"),
            output=values.get("output"),
            pending=list(values.get("pending") or []),
            error=values.get("error"),
            halt=values.get("halt"),
            budget=values.get("budget"),
            agent_runs=values.get("agent_runs", 0),
            next_nodes=tuple(snapshot.next),
        )

    async def session_status(self, thread_id: str) -> dict[str, Any] | None:
        """The gateway's live view of the thread's session (budget and breaker)."""
        async with self._session(thread_id) as gateway:
            return await gateway.session_status()

    # --- helpers -------------------------------------------------------------------------
    def _session(self, thread_id: str) -> GatewaySession:
        return GatewaySession(
            self._gateway_url,
            self._tokens.token_for(thread_id),
            session_id=thread_id,
            timeout_seconds=self._gateway_timeout,
            transport=self._transport,
            mcp=False,  # control-plane calls only
        )

    async def _any_decided(self, thread_id: str, pending: list[PendingCall]) -> bool:
        async with self._session(thread_id) as gateway:
            for call in pending:
                if approval_outcome(await gateway.approval(call["approval_id"])) is not None:
                    return True
        return False


def _config(thread_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id}}
