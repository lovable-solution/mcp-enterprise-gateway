"""PydanticAI toolset over the gateway, with governance verdicts mapped to agent control flow.

The gateway exposes only the tools this identity may call. Each result can carry a
governance block (``_meta["mcp-enterprise-gateway/governance"]``); the toolset turns it
into the PydanticAI construct with the right meaning for the run:

==================== ====================================================================
gateway code          agent behaviour
==================== ====================================================================
(none) / APPROVED     the tool's result (an upstream tool error becomes ``ToolFailed``)
PENDING_APPROVAL      ``CallDeferred``: the run ends with ``DeferredToolRequests`` and the
                      graph pauses (LangGraph ``interrupt``) until a human decides
INVALID_ARGUMENTS     ``ModelRetry``: the model corrects its arguments
BUDGET_EXCEEDED,      ``GovernanceHaltError``: the session is stopped; spending more tokens
CIRCUIT_OPEN          cannot help, so the whole run ends
REJECTED, EXPIRED,    ``ToolFailed``: terminal for this call; the model adapts (and is told
APPROVAL_FAILED,      not to repeat calls whose outcome is unknown)
UPSTREAM_UNAVAILABLE,
OUTCOME_UNKNOWN
==================== ====================================================================

A policy denial is a protocol error from the gateway and becomes ``ToolFailed`` as well.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from mcp import MCPError
from mcp_types import CallToolResult, Tool
from pydantic_ai import (
    AbstractToolset,
    CallDeferred,
    ModelRetry,
    RunContext,
    ToolDefinition,
    ToolFailed,
    ToolsetTool,
)
from pydantic_core import SchemaValidator, core_schema

from .gateway import GatewaySession, Governance, result_text

EventKind = Literal["text", "tool_call", "tool_result", "progress", "usage", "approval", "status"]


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    """Something worth showing while a run progresses (CLI output, logs)."""

    kind: EventKind
    message: str
    data: dict[str, Any] = field(default_factory=dict)


EventSink = Callable[[RuntimeEvent], Awaitable[None]]


async def discard(_event: RuntimeEvent) -> None:
    return None


@dataclass
class AgentDeps:
    """Per-run dependencies of the agent (one open gateway session)."""

    gateway: GatewaySession
    emit: EventSink = discard
    budget: dict[str, Any] | None = None
    """The session budget as last reported by the gateway."""
    tools: list[Tool] | None = None
    """Tools discovered for this run (listed once, not on every model request)."""


class GovernanceHaltError(Exception):
    """The gateway stopped this session; the run cannot usefully continue."""

    def __init__(self, code: str, message: str, details: dict[str, Any]) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


# The gateway validates arguments against the upstream schema (and reports
# INVALID_ARGUMENTS); locally any JSON object is passed through unchanged.
_ANY_ARGUMENTS = SchemaValidator(
    core_schema.dict_schema(core_schema.str_schema(), core_schema.any_schema())
)

_FAIL_CODES = frozenset(
    {"REJECTED", "EXPIRED", "APPROVAL_FAILED", "UPSTREAM_UNAVAILABLE", "OUTCOME_UNKNOWN"}
)
_HALT_CODES = frozenset({"BUDGET_EXCEEDED", "CIRCUIT_OPEN"})


def interpret(result: CallToolResult) -> Any:
    """The value to hand the model for a gateway tool result, or the exception to raise."""
    governance = Governance.of(result)
    text = result_text(result)
    code = governance.code if governance else None
    if code is None or code == "APPROVED":
        if result.is_error:
            raise ToolFailed(text or "the tool reported an error")
        return result.structured_content if result.structured_content is not None else text
    assert governance is not None  # noqa: S101 - code is not None
    if code == "PENDING_APPROVAL":
        raise CallDeferred(metadata=governance.details | {"message": text})
    if code == "INVALID_ARGUMENTS":
        raise ModelRetry(text)
    if code in _HALT_CODES:
        raise GovernanceHaltError(code, text, governance.details)
    if code in _FAIL_CODES:
        raise ToolFailed(text)
    raise ToolFailed(f"unexpected gateway verdict {code!r}: {text}")


def _definition(tool: Tool) -> ToolDefinition:
    return ToolDefinition(
        name=tool.name,
        description=tool.description or "",
        parameters_json_schema=dict(tool.input_schema),
        metadata={"annotations": tool.annotations.model_dump() if tool.annotations else {}},
    )


class GatewayToolset(AbstractToolset[AgentDeps]):
    """Every tool the gateway exposes to the agent's identity, called through the gateway."""

    def __init__(self, *, max_retries: int = 2) -> None:
        self._max_retries = max_retries

    @property
    def id(self) -> str | None:
        return "mcp-gateway"

    async def get_tools(self, ctx: RunContext[AgentDeps]) -> dict[str, ToolsetTool[AgentDeps]]:
        if ctx.deps.tools is None:
            ctx.deps.tools = await ctx.deps.gateway.list_tools()
        return {
            tool.name: ToolsetTool(
                toolset=self,
                tool_def=_definition(tool),
                max_retries=self._max_retries,
                args_validator=_ANY_ARGUMENTS,
            )
            for tool in ctx.deps.tools
        }

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[AgentDeps],
        tool: ToolsetTool[AgentDeps],
    ) -> Any:
        emit = ctx.deps.emit

        async def on_progress(progress: float, total: float | None, message: str | None) -> None:
            del progress, total  # the gateway's messages say what is happening
            if message:
                await emit(RuntimeEvent("progress", message, {"tool": name}))

        await emit(RuntimeEvent("tool_call", name, {"arguments": tool_args}))
        try:
            result = await ctx.deps.gateway.call_tool(name, tool_args, on_progress=on_progress)
        except MCPError as exc:  # policy denial, unknown tool, malformed request
            await emit(RuntimeEvent("tool_result", f"{name}: refused", {"error": str(exc)}))
            raise ToolFailed(f"The gateway refused this call: {exc.error.message}") from exc
        governance = Governance.of(result)
        verdict = governance.code if governance else ("error" if result.is_error else "ok")
        await emit(
            RuntimeEvent(
                "tool_result",
                f"{name}: {verdict}",
                {"governance": governance.details if governance else None},
            )
        )
        return interpret(result)
