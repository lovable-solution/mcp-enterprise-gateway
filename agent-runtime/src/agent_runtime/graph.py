"""LangGraph orchestration: run the agent, pause durably for human approval, resume.

::

    START --> agent --(answer | halted | failed)--> END
                ^  |
     (all       |  | (calls held for approval)
     decided)   |  v
             await_approval --(still pending: interrupt again)--+
                    ^                                             |
                    +---------------------------------------------+

* ``agent`` runs the PydanticAI agent over the thread's message history. A call the
  gateway holds for approval ends the run with ``DeferredToolRequests``; the thread's
  state (history plus the held calls) is checkpointed.
* ``await_approval`` calls ``interrupt()``: the thread stops, possibly for hours, and the
  process may exit. Resuming (``Command(resume=...)``) re-enters the node, which asks the
  **gateway** for each approval's outcome. The resume payload is never trusted for
  decisions, so a client cannot fabricate an approval or a result.
* Decided calls become ``DeferredToolResults`` for the next ``agent`` run: an executed
  call returns the upstream result the gateway recorded, and a rejected, expired or failed
  one returns a tool failure the model must account for.

State holds only JSON-compatible values (messages are PydanticAI's JSON form), so any
checkpointer, and its serializer allow-list, can store it.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, NotRequired, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt
from mcp_types import CallToolResult
from opentelemetry import trace
from pydantic_ai import (
    Agent,
    DeferredToolRequests,
    DeferredToolResults,
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolFailed,
    ToolReturn,
    ToolReturnPart,
    UsageLimits,
)

from .agent import AgentType
from .gateway import GatewaySession, result_text
from .identity import TokenProvider
from .toolset import AgentDeps, EventSink, GovernanceHaltError, RuntimeEvent, discard
from .usage import UsageReportingError

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("agent_runtime")

Status = Literal["running", "awaiting_approval", "completed", "halted", "failed"]


class PendingCall(TypedDict):
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    approval_id: str
    expires_at: str | None
    message: str


class Outcome(TypedDict):
    """A decided call's result, as handed to the next agent run."""

    kind: Literal["return", "failed"]
    value: NotRequired[Any]
    message: NotRequired[str]


class ThreadState(TypedDict, total=False):
    prompt: str
    messages: list[dict[str, Any]]
    pending: list[PendingCall]
    resolved: dict[str, Outcome]
    status: Status
    output: str | None
    error: str | None
    halt: dict[str, Any] | None
    budget: dict[str, Any] | None
    agent_runs: int


@dataclass(frozen=True, slots=True)
class GraphDeps:
    agent: AgentType
    tokens: TokenProvider
    gateway_url: str
    gateway_timeout_seconds: float = 60.0
    max_model_requests: int = 30
    emit: EventSink = discard
    gateway_transport: Any = None
    """Optional ``httpx2`` transport (tests)."""


def find_exception[ExcT: BaseException](exc: BaseException, kind: type[ExcT]) -> ExcT | None:
    """``exc`` or the first exception of type ``kind`` inside (nested) exception groups;
    parallel tool calls run in a task group, which wraps what they raise."""
    if isinstance(exc, kind):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for inner in exc.exceptions:
            found = find_exception(inner, kind)
            if found is not None:
                return found
    return None


def dump_messages(messages: Sequence[ModelMessage]) -> list[dict[str, Any]]:
    dumped: list[dict[str, Any]] = ModelMessagesTypeAdapter.dump_python(list(messages), mode="json")
    return dumped


def load_messages(data: list[dict[str, Any]] | None) -> list[ModelMessage]:
    return ModelMessagesTypeAdapter.validate_python(data or [])


def consistent(messages: Sequence[ModelMessage]) -> list[ModelMessage]:
    """History up to the last point a run can continue from.

    A run that stopped while executing tools leaves an empty or partial request after the
    model turn that called them; every call needs a result before the model can be asked
    again, so that turn is dropped and the model is asked again from the request before it.
    """
    history = list(messages)
    while history and isinstance(history[-1], ModelRequest) and not history[-1].parts:
        history.pop()
    if not history:
        return history
    last = history[-1]
    if isinstance(last, ModelResponse):
        if last.tool_calls:
            history.pop()
        return history
    turn = history[-2] if len(history) >= 2 else None  # noqa: PLR2004 - the turn before `last`
    if isinstance(turn, ModelResponse) and turn.tool_calls:
        called = {call.tool_call_id for call in turn.tool_calls}
        answered = {
            part.tool_call_id
            for part in last.parts
            if isinstance(part, ToolReturnPart | RetryPromptPart)
        }
        if not called <= answered:
            del history[-2:]
    return history


def deferred_results(resolved: dict[str, Outcome] | None) -> DeferredToolResults | None:
    if not resolved:
        return None
    calls: dict[str, Any] = {
        call_id: ToolReturn(outcome.get("value"))
        if outcome["kind"] == "return"
        else ToolFailed(outcome.get("message", "the call failed"))
        for call_id, outcome in resolved.items()
    }
    return DeferredToolResults(calls=calls)


def approval_outcome(view: dict[str, Any]) -> Outcome | None:
    """What the model is told about a decided approval; None while it is undecided."""
    status = view.get("status")
    reviewer = view.get("decided_by_subject") or "a reviewer"
    if status == "EXECUTED":
        result = CallToolResult.model_validate(view.get("result") or {"content": []})
        if result.is_error:
            text = result_text(result) or "the tool reported an error"
            return {
                "kind": "failed",
                "message": f"Approved by {reviewer}, but the tool failed: {text}",
            }
        value = result.structured_content
        return {
            "kind": "return",
            "value": {
                "approved_by": reviewer,
                "approval_id": view.get("id"),
                "result": value if value is not None else result_text(result),
            },
        }
    if status == "REJECTED":
        reason = view.get("decision_reason") or "no reason given"
        return {
            "kind": "failed",
            "message": f"{reviewer} rejected this call ({reason}). It was not executed.",
        }
    if status == "EXPIRED":
        return {
            "kind": "failed",
            "message": "No reviewer decided before the approval expired. It was not executed.",
        }
    if status == "FAILED":
        return {
            "kind": "failed",
            "message": f"The call was approved but could not be executed: {view.get('error')}",
        }
    return None  # PENDING_APPROVAL, or APPROVED / EXECUTING (the gateway is running it)


def pending_calls(requests: DeferredToolRequests) -> list[PendingCall]:
    calls: list[PendingCall] = []
    for call in requests.calls:
        metadata = requests.metadata.get(call.tool_call_id, {})
        approval_id = metadata.get("approval_id")
        if not isinstance(approval_id, str):
            msg = f"deferred call {call.tool_name} carries no approval id"
            raise RuntimeError(msg)
        calls.append(
            PendingCall(
                tool_call_id=call.tool_call_id,
                tool_name=call.tool_name,
                arguments=call.args_as_dict(),
                approval_id=approval_id,
                expires_at=metadata.get("expires_at"),
                message=str(metadata.get("message", "")),
            )
        )
    return calls


def _thread_id(config: RunnableConfig) -> str:
    thread_id = (config.get("configurable") or {}).get("thread_id")
    if not isinstance(thread_id, str):
        msg = "the graph must be invoked with config={'configurable': {'thread_id': ...}}"
        raise ValueError(msg)
    return thread_id


def build_graph(
    deps: GraphDeps, checkpointer: BaseCheckpointSaver[Any] | None
) -> CompiledStateGraph[ThreadState, None, ThreadState, ThreadState]:
    def session(thread_id: str, *, mcp: bool) -> GatewaySession:
        return GatewaySession(
            deps.gateway_url,
            deps.tokens.token_for(thread_id),
            session_id=thread_id,
            timeout_seconds=deps.gateway_timeout_seconds,
            transport=deps.gateway_transport,
            mcp=mcp,
        )

    async def agent_node(state: ThreadState, config: RunnableConfig) -> ThreadState:
        thread_id = _thread_id(config)
        history = load_messages(state.get("messages"))
        runs = state.get("agent_runs", 0) + 1
        with tracer.start_as_current_span(
            "agent_runtime.agent_run",
            attributes={"agent_runtime.thread_id": thread_id, "agent_runtime.run": runs},
        ):
            async with session(thread_id, mcp=True) as gateway:
                agent_deps = AgentDeps(gateway=gateway, emit=deps.emit)
                return await _run_agent(deps, state, history, agent_deps, runs)

    async def await_approval_node(state: ThreadState, config: RunnableConfig) -> ThreadState:
        thread_id = _thread_id(config)
        pending = state.get("pending") or []
        interrupt(
            {
                "reason": "awaiting_approval",
                "approvals": [
                    {
                        "approval_id": call["approval_id"],
                        "tool_name": call["tool_name"],
                        "arguments": call["arguments"],
                        "expires_at": call["expires_at"],
                    }
                    for call in pending
                ],
            }
        )
        # Resumed: the gateway is the only source of truth for decisions.
        resolved = dict(state.get("resolved") or {})
        still_pending: list[PendingCall] = []
        with tracer.start_as_current_span(
            "agent_runtime.check_approvals", attributes={"agent_runtime.thread_id": thread_id}
        ):
            async with session(thread_id, mcp=False) as gateway:
                for call in pending:
                    outcome = approval_outcome(await gateway.approval(call["approval_id"]))
                    if outcome is None:
                        still_pending.append(call)
                    else:
                        resolved[call["tool_call_id"]] = outcome
                        await deps.emit(
                            RuntimeEvent(
                                "approval",
                                f"{call['tool_name']}: decided ({outcome['kind']})",
                                {"approval_id": call["approval_id"]},
                            )
                        )
        if still_pending:
            return {"pending": still_pending, "resolved": resolved}
        return {"pending": [], "resolved": resolved, "status": "running"}

    def after_agent(state: ThreadState) -> str:
        return "await_approval" if state.get("status") == "awaiting_approval" else END

    def after_approval(state: ThreadState) -> str:
        return "await_approval" if state.get("pending") else "agent"

    builder: StateGraph[ThreadState, None, ThreadState, ThreadState] = StateGraph(ThreadState)
    builder.add_node("agent", agent_node)
    builder.add_node("await_approval", await_approval_node)
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", after_agent, ["await_approval", END])
    builder.add_conditional_edges("await_approval", after_approval, ["await_approval", "agent"])
    return builder.compile(checkpointer=checkpointer)


async def _run_agent(
    deps: GraphDeps,
    state: ThreadState,
    history: list[ModelMessage],
    agent_deps: AgentDeps,
    runs: int,
) -> ThreadState:
    prompt = None if history else state.get("prompt")
    async with deps.agent.iter(
        prompt,
        message_history=history,
        deferred_tool_results=deferred_results(state.get("resolved")),
        deps=agent_deps,
        usage_limits=UsageLimits(request_limit=deps.max_model_requests),
    ) as run:
        try:
            async for node in run:
                if Agent.is_model_request_node(node):
                    # Streamed requests: long answers never hit a request timeout.
                    async with node.stream(run.ctx) as stream:
                        async for _event in stream:
                            pass
        except Exception as exc:
            return await _stopped(deps, run.all_messages(), exc, agent_deps, runs)
        result = run.result
        messages = run.all_messages()
    if result is None:  # pragma: no cover - iteration completed without an end node
        failure = RuntimeError("the run ended without a result")
        return await _stopped(deps, messages, failure, agent_deps, runs)
    common: ThreadState = {
        "messages": dump_messages(messages),
        "resolved": {},
        "budget": agent_deps.budget,
        "agent_runs": runs,
        "error": None,
        "halt": None,
    }
    if isinstance(result.output, DeferredToolRequests):
        pending = pending_calls(result.output)
        for call in pending:
            await deps.emit(
                RuntimeEvent(
                    "approval",
                    f"{call['tool_name']} is held for human approval ({call['approval_id']})",
                    dict(call),
                )
            )
        return {**common, "status": "awaiting_approval", "pending": pending, "output": None}
    await deps.emit(RuntimeEvent("text", result.output))
    return {**common, "status": "completed", "pending": [], "output": result.output}


async def _stopped(
    deps: GraphDeps,
    messages: Sequence[ModelMessage],
    exc: Exception,
    agent_deps: AgentDeps,
    runs: int,
) -> ThreadState:
    """The run stopped early: keep its history up to a consistent point so a retry
    continues from there instead of redoing (and re-paying for) completed work."""
    common: ThreadState = {
        "messages": dump_messages(consistent(messages)),
        "pending": [],
        "budget": agent_deps.budget,
        "agent_runs": runs,
        "output": None,
    }
    halt = find_exception(exc, GovernanceHaltError)
    if halt is not None:
        await deps.emit(RuntimeEvent("status", f"halted by the gateway: {halt.code}"))
        return {
            **common,
            "status": "halted",
            "halt": {"code": halt.code, "message": str(halt), "details": halt.details},
            "error": None,
        }
    reporting = find_exception(exc, UsageReportingError)
    reason = (
        f"usage could not be reported to the gateway: {reporting}"
        if reporting is not None
        else f"{type(exc).__name__}: {exc}"
    )
    logger.error("agent run failed: %s", reason, exc_info=exc)
    await deps.emit(RuntimeEvent("status", f"failed: {reason}"))
    return {**common, "status": "failed", "error": reason, "halt": None}
