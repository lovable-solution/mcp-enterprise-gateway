"""The enforcement pipeline every ``tools/call`` goes through, in this order:

1. **Loop detection / circuit breaker** (Redis): before RBAC, so an agent hammering any
   tool, even one it may not call, is halted. A tripped session is refused everything.
2. **RBAC** (policy engine, default deny): a denied or unknown tool gets the *same*
   JSON-RPC error, so agents cannot probe which tools exist.
3. **Arguments** are validated against the tool's (closed) input schema.
4. **Budget pre-check**: a session over any cap is refused before anything is queued.
5. **Human approval** for tools with ``requires_approval``: suspend, persist, notify, wait.
6. **Charge and forward**: the budget charge is atomic with its cap check; then the call
   is forwarded upstream with a signed identity assertion.

Blocked calls return a ``CallToolResult`` with ``is_error=True``, a message the model can
act on, and a machine-readable ``_meta["mcp-enterprise-gateway/governance"]`` entry
(``code``: PENDING_APPROVAL, REJECTED, EXPIRED, APPROVAL_FAILED, CIRCUIT_OPEN,
BUDGET_EXCEEDED, INVALID_ARGUMENTS, UPSTREAM_UNAVAILABLE, OUTCOME_UNKNOWN) that agent
runtimes act on. A call the upstream provably never ran is refunded to the session budget.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final

from mcp import MCPError
from mcp_types import INVALID_PARAMS, CallToolResult, TextContent, Tool
from opentelemetry import trace

from .approvals.models import Approval, ApprovalStatus
from .approvals.service import ApprovalService
from .auth.principal import Principal
from .enforcement.breaker import BreakerState, BreakerVerdict, CircuitBreaker
from .enforcement.budget import BudgetExceededError, BudgetLedger, BudgetSnapshot
from .enforcement.fingerprint import call_fingerprint
from .enforcement.sessions import SessionDirectory
from .enforcement.spend import SpendEvents, budget_data
from .events.notifier import Notifier
from .policy.engine import PolicyEngine, ToolDecision
from .policy.loader import LoadedPolicy
from .telemetry import Attr, Instruments, Outcome, tracer
from .upstream.identity import CallerIdentity
from .upstream.registry import UpstreamError, UpstreamRegistry

GOVERNANCE_META_KEY: Final = "mcp-enterprise-gateway/governance"

Progress = Callable[[float, str], Awaitable[None]]


def governance_result(code: str, message: str, **details: Any) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=message)],
        is_error=True,
        meta={GOVERNANCE_META_KEY: {"code": code, **details}},
    )


def _with_governance(result: CallToolResult, **details: Any) -> CallToolResult:
    meta = dict(result.meta or {})
    meta[GOVERNANCE_META_KEY] = details
    return result.model_copy(update={"meta": meta})


class ToolCallPipeline:
    def __init__(
        self,
        *,
        policy: LoadedPolicy,
        engine: PolicyEngine,
        registry: UpstreamRegistry,
        breaker: CircuitBreaker,
        budget: BudgetLedger,
        sessions: SessionDirectory,
        approvals: ApprovalService,
        notifier: Notifier,
        spend: SpendEvents,
        instruments: Instruments,
    ) -> None:
        self._policy = policy
        self._engine = engine
        self._registry = registry
        self._breaker = breaker
        self._budget = budget
        self._sessions = sessions
        self._approvals = approvals
        self._notifier = notifier
        self._spend = spend
        self._instruments = instruments

    async def list_tools(self, principal: Principal) -> list[Tool]:
        catalog = await self._registry.catalog()
        return [
            catalog[name].tool
            for name in self._engine.visible_tools(principal.role)
            if name in catalog
        ]

    async def call_tool(
        self,
        principal: Principal,
        name: str,
        arguments: Mapping[str, Any],
        *,
        progress: Progress | None = None,
    ) -> CallToolResult:
        started = time.perf_counter()
        span = trace.get_current_span()  # the SDK's ``tools/call <name>`` server span
        span.set_attributes(
            {
                Attr.TENANT: principal.tenant_id,
                Attr.SESSION: principal.session_id or "",
                Attr.SUBJECT: principal.subject,
                Attr.ROLE: principal.role,
                Attr.POLICY_DIGEST: self._policy.digest,
            }
        )
        outcome = Outcome.ERROR
        try:
            result, outcome = await self._enforce(principal, name, arguments, progress)
            return result
        except MCPError:
            outcome = Outcome.DENIED  # the only protocol error raised here is the RBAC denial
            raise
        finally:
            labels = {
                Attr.TOOL: name if name in self._policy.document.tools else "(unregistered)",
                Attr.OUTCOME: outcome.value,
                Attr.ROLE: principal.role,
            }
            span.set_attribute(Attr.OUTCOME, outcome.value)
            self._instruments.tool_calls.add(1, labels)
            self._instruments.tool_call_duration.record(time.perf_counter() - started, labels)

    async def _enforce(  # noqa: PLR0911 - one early return per enforcement step
        self,
        principal: Principal,
        name: str,
        arguments: Mapping[str, Any],
        progress: Progress | None,
    ) -> tuple[CallToolResult, Outcome]:
        document = self._policy.document
        assert principal.session_id is not None  # noqa: S101 - enforced by the MCP endpoint
        tenant, session = principal.tenant_id, principal.session_id
        ttl = document.budgets.ledger_ttl_seconds
        await self._sessions.touch(principal, ttl)

        # 1. Loop detection and the session's circuit breaker.
        fingerprint = call_fingerprint(name, arguments)
        with tracer.start_as_current_span("gateway.circuit_breaker.check") as span:
            verdict = await self._breaker.check(
                tenant_id=tenant,
                session_id=session,
                tool=name,
                fingerprint=fingerprint,
                policy=document.circuit_breaker,
                index_ttl_seconds=ttl,
            )
            span.set_attributes(
                {
                    Attr.BREAKER_STATE: verdict.state.value,
                    Attr.IDENTICAL_CALLS: verdict.identical_calls,
                }
            )
        if verdict.state is BreakerState.TRIPPED:
            self._instruments.breaker_trips.add(1, {Attr.ROLE: principal.role})
            await self._notifier.emit(
                "circuit_breaker.tripped",
                tenant_id=tenant,
                session_id=session,
                data={
                    "tool": name,
                    "subject": principal.subject,
                    "role": principal.role,
                    "identical_calls": verdict.identical_calls,
                    "window_seconds": document.circuit_breaker.loop_detection.window_seconds,
                    "retry_after_seconds": verdict.retry_after_seconds,
                    "manual_reset_required": document.circuit_breaker.require_manual_reset,
                },
            )
        if verdict.blocked:
            return _circuit_open(verdict), Outcome.CIRCUIT_OPEN

        # 2. RBAC.
        with tracer.start_as_current_span("gateway.policy.check") as span:
            decision = self._engine.check_tool(principal.role, name)
            span.set_attributes(
                {
                    Attr.DECISION: "allow" if decision.allowed else "deny",
                    Attr.RULE: decision.rule or "",
                    Attr.DENY_REASON: decision.reason.value if decision.reason else "",
                }
            )
        trace.get_current_span().set_attribute(
            Attr.DECISION, "allow" if decision.allowed else "deny"
        )
        if not decision.allowed:
            await self._deny(principal, name, decision)

        # 3. Upstream availability and arguments.
        entry = (await self._registry.catalog()).get(name)
        if entry is None:
            return (
                governance_result(
                    "UPSTREAM_UNAVAILABLE",
                    f"The server providing {name} is unavailable right now. Try again later.",
                ),
                Outcome.UPSTREAM_UNAVAILABLE,
            )
        problems = entry.argument_errors(arguments)
        if problems:
            return (
                governance_result(
                    "INVALID_ARGUMENTS",
                    "Invalid arguments: " + "; ".join(problems[:5]),
                    errors=problems,
                ),
                Outcome.INVALID_ARGUMENTS,
            )

        # 4. Budget pre-check (the binding check is atomic with the charge in step 6).
        limits = self._engine.session_budget(principal.role)
        snapshot = await self._budget.snapshot(tenant, session, limits)
        if snapshot.exhausted_limit is not None:
            return await self._budget_exceeded(principal, snapshot.exhausted_limit, snapshot)

        # 5. Human approval.
        if decision.requires_approval:
            return await self._approval_gate(
                principal=principal,
                name=name,
                arguments=arguments,
                fingerprint=fingerprint,
                decision=decision,
                progress=progress,
            )

        # 6. Charge and forward.
        spec = document.tools[name]
        try:
            snapshot = await self._budget.charge_tool_call(
                tenant_id=tenant,
                session_id=session,
                limits=limits,
                cost_usd=spec.cost_usd_per_call,
                ttl_seconds=ttl,
            )
        except BudgetExceededError as exc:
            return await self._budget_exceeded(principal, exc.limit, exc.snapshot)
        self._spend.record_tool_cost(tenant_id=tenant, tool=name, cost_usd=spec.cost_usd_per_call)
        try:
            with tracer.start_as_current_span(
                "gateway.upstream.call", attributes={Attr.UPSTREAM: entry.server}
            ):
                result = await self._registry.call(entry, CallerIdentity.of(principal), arguments)
        except UpstreamError as exc:
            if exc.executed == "no":  # never ran: give the session its call back
                await self._budget.refund_tool_call(
                    tenant_id=tenant, session_id=session, cost_usd=spec.cost_usd_per_call
                )
                return (
                    governance_result(
                        "UPSTREAM_UNAVAILABLE",
                        f"The server providing {name} is unreachable; the call did not run. "
                        "Try again later.",
                    ),
                    Outcome.UPSTREAM_UNAVAILABLE,
                )
            return (
                governance_result(
                    "OUTCOME_UNKNOWN",
                    f"The server providing {name} failed while handling the call. It may or "
                    "may not have taken effect: check before repeating it.",
                ),
                Outcome.UPSTREAM_UNAVAILABLE,
            )
        await self._spend.after_spend(
            tenant_id=tenant,
            session_id=session,
            subject=principal.subject,
            role=principal.role,
            snapshot=snapshot,
            tool=name,
        )
        return result, (Outcome.TOOL_ERROR if result.is_error else Outcome.SUCCEEDED)

    async def _deny(self, principal: Principal, name: str, decision: ToolDecision) -> None:
        reason = decision.reason.value if decision.reason else "denied"
        self._instruments.policy_denials.add(
            1,
            {
                Attr.DENY_REASON: reason,
                Attr.ROLE: principal.role,
                Attr.TOOL: name if name in self._policy.document.tools else "(unregistered)",
            },
        )
        await self._notifier.emit(
            "tool_call.denied",
            tenant_id=principal.tenant_id,
            session_id=principal.session_id,
            data={
                "tool": name,
                "reason": reason,
                "subject": principal.subject,
                "role": principal.role,
            },
        )
        raise MCPError(INVALID_PARAMS, f"Unknown tool: {name}")

    async def _approval_gate(
        self,
        *,
        principal: Principal,
        name: str,
        arguments: Mapping[str, Any],
        fingerprint: str,
        decision: ToolDecision,
        progress: Progress | None,
    ) -> tuple[CallToolResult, Outcome]:
        span_context = trace.get_current_span().get_span_context()
        approval = await self._approvals.request(
            principal=principal,
            tool=name,
            arguments=arguments,
            digest=fingerprint,
            decision=decision,
            trace_id=f"{span_context.trace_id:032x}" if span_context.is_valid else None,
        )
        trace.get_current_span().set_attribute(Attr.APPROVAL_ID, str(approval.id))

        async def tick(elapsed: float) -> None:
            if progress is not None:
                await progress(elapsed, f"waiting for human approval {approval.id}")

        outcome = await self._approvals.wait(
            approval,
            timeout_seconds=self._policy.document.approvals.inline_wait_seconds,
            on_tick=tick,
        )
        return _approval_result(outcome)

    async def _budget_exceeded(
        self, principal: Principal, limit: str, snapshot: BudgetSnapshot
    ) -> tuple[CallToolResult, Outcome]:
        self._instruments.budget_rejections.add(
            1, {Attr.BUDGET_LIMIT: limit, Attr.ROLE: principal.role}
        )
        assert principal.session_id is not None  # noqa: S101
        await self._spend.exhausted(
            tenant_id=principal.tenant_id,
            session_id=principal.session_id,
            subject=principal.subject,
            limit=limit,
            snapshot=snapshot,
        )
        return (
            governance_result(
                "BUDGET_EXCEEDED",
                f"Blocked: this session has reached its {limit} budget limit. No further tool "
                "calls are allowed in this session.",
                limit=limit,
                budget=budget_data(snapshot),
            ),
            Outcome.BUDGET_EXCEEDED,
        )


def _circuit_open(verdict: BreakerVerdict) -> CallToolResult:
    trip = verdict.trip
    if verdict.retry_after_seconds is None:
        when = "until an operator resets this session"
    else:
        when = f"for {verdict.retry_after_seconds} seconds"
    return governance_result(
        "CIRCUIT_OPEN",
        "Blocked: this session repeated the same tool call too many times (loop detected). "
        f"All tool calls in this session are paused {when}. Change approach instead of retrying.",
        tripped_by_tool=trip.tool if trip else None,
        retry_after_seconds=verdict.retry_after_seconds,
        identical_calls_allowed=trip.identical_calls_allowed if trip else None,
        window_seconds=trip.window_seconds if trip else None,
    )


def _approval_result(approval: Approval) -> tuple[CallToolResult, Outcome]:
    details: dict[str, Any] = {
        "approval_id": str(approval.id),
        "status": approval.status.value,
        "expires_at": approval.expires_at.isoformat(),
    }
    match approval.status:
        case ApprovalStatus.EXECUTED if approval.result is not None:
            result = CallToolResult.model_validate(approval.result)
            decided = {"decided_by": approval.decided_by_subject}
            return _with_governance(result, code="APPROVED", **details, **decided), Outcome.APPROVED
        case ApprovalStatus.REJECTED:
            reason = f": {approval.decision_reason}" if approval.decision_reason else ""
            return (
                governance_result(
                    "REJECTED",
                    f"A human reviewer rejected this call{reason}. It was not executed.",
                    reason=approval.decision_reason,
                    **details,
                ),
                Outcome.REJECTED,
            )
        case ApprovalStatus.EXPIRED:
            return (
                governance_result(
                    "EXPIRED",
                    "No reviewer decided in time; the call expired and was not executed.",
                    **details,
                ),
                Outcome.EXPIRED,
            )
        case ApprovalStatus.FAILED:
            return (
                governance_result(
                    "APPROVAL_FAILED",
                    f"The call was approved but could not be executed: {approval.error}",
                    error=approval.error,
                    **details,
                ),
                Outcome.APPROVAL_FAILED,
            )
        case ApprovalStatus.APPROVED | ApprovalStatus.EXECUTING:
            return (
                governance_result(
                    "PENDING_APPROVAL",
                    f"This call was approved and is being executed (approval id {approval.id}). "
                    "Do not retry it: its result becomes available under that approval id.",
                    **details,
                ),
                Outcome.PENDING_APPROVAL,
            )
        case _:
            return (
                governance_result(
                    "PENDING_APPROVAL",
                    f"This call is held for human approval (approval id {approval.id}). It has not "
                    "run yet. Do not retry it: the outcome becomes available once a reviewer "
                    "decides.",
                    **details,
                ),
                Outcome.PENDING_APPROVAL,
            )
