"""Human-in-the-loop approval service.

Flow for a tool with ``requires_approval: true``:

1. ``request`` persists the call (``PENDING_APPROVAL``) and emits ``approval.requested``
   (event stream for the console, signed webhook). An identical call already pending in
   the same session is reused, never duplicated.
2. The agent's ``tools/call`` is held for up to ``approvals.inline_wait_seconds``
   (``wait``). If a decision lands in time, the agent receives the real result inline;
   otherwise it receives ``PENDING_APPROVAL`` with the approval id, checkpoints, and
   resumes later from ``GET /v1/approvals/{id}``.
3. ``decide`` (``POST /v1/approvals/{id}/decide``) applies the operator's decision after
   checking the approver role, the four-eyes rule and tenant isolation.
4. On approval the gateway itself executes exactly the stored payload, once (``execute``):
   the request is immutable in the database, so arguments cannot change after review.

An approved call never ends in limbo. Execution runs as a background task, so an
operator who disconnects cannot cancel it half-way, and every failure after the claim
lands in ``FAILED``. ``recover_stalled`` resolves what a crashed replica leaves behind:
an ``APPROVED`` call never started, so it is executed; an ``EXECUTING`` call was
interrupted with an unknown outcome, so it is marked ``FAILED`` for manual reconciliation
(a non-idempotent write must never be blindly retried).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from ..auth.principal import Principal
from ..enforcement.budget import BudgetExceededError, BudgetLedger
from ..enforcement.spend import SpendEvents
from ..errors import Conflict, Forbidden, NotFound
from ..events.bus import EventType
from ..events.hub import EventHub, SubscriptionLaggedError
from ..events.notifier import Notifier
from ..policy.engine import PolicyEngine, ToolDecision
from ..policy.loader import LoadedPolicy
from ..policy.models import ControlAction
from ..telemetry import Attr, Instruments, tracer
from ..upstream.identity import CallerIdentity
from ..upstream.registry import UpstreamError, UpstreamRegistry
from .models import (
    TERMINAL_EVENT_TYPES,
    Approval,
    ApprovalStatus,
    ApprovalView,
    Decision,
    DecisionRequest,
)
from .store import SYSTEM_ACTOR, Actor, ApprovalStore, NewApproval

logger = logging.getLogger(__name__)

Tick = Callable[[float], Awaitable[None]]
_WAIT_SLICE_SECONDS = 2.0
_UNKNOWN_OUTCOME = "the outcome is unknown: verify the upstream state before retrying"


class ApprovalService:
    def __init__(
        self,
        *,
        store: ApprovalStore,
        hub: EventHub,
        notifier: Notifier,
        registry: UpstreamRegistry,
        budget: BudgetLedger,
        spend: SpendEvents,
        policy: LoadedPolicy,
        engine: PolicyEngine,
        instruments: Instruments,
    ) -> None:
        self._store = store
        self._hub = hub
        self._notifier = notifier
        self._registry = registry
        self._budget = budget
        self._spend = spend
        self._policy = policy
        self._engine = engine
        self._instruments = instruments
        self._executions: dict[UUID, asyncio.Task[Approval]] = {}

    # --- requester side --------------------------------------------------------------
    async def request(
        self,
        *,
        principal: Principal,
        tool: str,
        arguments: Mapping[str, Any],
        digest: str,
        decision: ToolDecision,
        trace_id: str | None,
    ) -> Approval:
        assert principal.session_id is not None  # noqa: S101 - agent principals only
        assert decision.approval_timeout_seconds is not None  # noqa: S101
        spec = self._policy.document.tools[tool]
        approval, created = await self._store.create_or_get_pending(
            NewApproval(
                id=uuid4(),
                tenant_id=principal.tenant_id,
                session_id=principal.session_id,
                requester_subject=principal.subject,
                requester_role=principal.role,
                tool_name=tool,
                arguments=dict(arguments),
                arguments_digest=digest,
                risk=spec.risk.value,
                approver_roles=decision.approver_roles,
                policy_name=self._policy.document.metadata.name,
                policy_digest=self._policy.digest,
                policy_rule=decision.rule or "",
                trace_id=trace_id,
                timeout_seconds=decision.approval_timeout_seconds,
            )
        )
        if created:
            await self._announce("approval.requested", approval)
        return approval

    async def wait(
        self, approval: Approval, *, timeout_seconds: float, on_tick: Tick | None
    ) -> Approval:
        """Block until the approval ends or ``timeout_seconds`` elapse; return its state."""
        if approval.status.is_terminal or timeout_seconds <= 0:
            return approval
        started = time.monotonic()
        deadline = started + timeout_seconds
        current = approval
        with tracer.start_as_current_span(
            "gateway.approval.wait", attributes={Attr.APPROVAL_ID: str(approval.id)}
        ):
            while not current.status.is_terminal and time.monotonic() < deadline:
                try:
                    current = await self._wait_once(current, started, deadline, on_tick)
                except SubscriptionLaggedError:
                    continue  # fell behind a busy stream: subscribe afresh
        return current

    async def _wait_once(
        self, approval: Approval, started: float, deadline: float, on_tick: Tick | None
    ) -> Approval:
        # Subscribe *before* re-reading the row: a decision that lands in between is then
        # either visible in the row or delivered as an event.
        async with self._hub.subscribe(approval.tenant_id) as subscription:
            current = await self._store.get(approval.id) or approval
            last_tick = time.monotonic()
            while not current.status.is_terminal and (remaining := deadline - time.monotonic()) > 0:
                event = await subscription.next(timeout=min(remaining, _WAIT_SLICE_SECONDS))
                if (
                    event is not None
                    and event.type in TERMINAL_EVENT_TYPES
                    and event.data.get("approval_id") == str(approval.id)
                ):
                    current = await self._store.get(approval.id) or current
                if on_tick is not None and time.monotonic() - last_tick >= _WAIT_SLICE_SECONDS:
                    last_tick = time.monotonic()
                    await on_tick(last_tick - started)
            return current

    # --- operator side ---------------------------------------------------------------
    async def get_for(self, principal: Principal, approval_id: UUID) -> ApprovalView:
        """Operators with approvals:read, or the requesting session itself (to resume)."""
        approval = await self._store.get(approval_id)
        if approval is None or approval.tenant_id != principal.tenant_id:
            raise NotFound("no such approval")
        is_requester = (
            approval.requester_subject == principal.subject
            and approval.session_id == principal.session_id
        )
        if not is_requester and not self._engine.allows_action(
            principal.role, ControlAction.APPROVALS_READ
        ):
            raise Forbidden(f"role {principal.role!r} may not read approvals")
        return await self._view(approval)

    async def recent(
        self, principal: Principal, *, status: ApprovalStatus | None, limit: int
    ) -> list[Approval]:
        return await self._store.recent(principal.tenant_id, status=status, limit=limit)

    async def decide(
        self, approval_id: UUID, decider: Principal, request: DecisionRequest
    ) -> ApprovalView:
        approval = await self._store.get(approval_id)
        if approval is None or approval.tenant_id != decider.tenant_id:
            raise NotFound("no such approval")
        if decider.role not in approval.approver_roles:
            raise Forbidden(
                f"role {decider.role!r} is not an approver for {approval.tool_name}",
                code="not_an_approver",
            )
        if (
            self._policy.document.approvals.require_distinct_approver
            and decider.subject == approval.requester_subject
        ):
            raise Forbidden("the requester of a call cannot decide on it", code="self_approval")
        if approval.status is not ApprovalStatus.PENDING_APPROVAL:
            raise Conflict(f"approval is already {approval.status.value}", code="already_decided")

        target = (
            ApprovalStatus.APPROVED
            if request.decision is Decision.APPROVE
            else ApprovalStatus.REJECTED
        )
        updated = await self._store.transition(
            approval_id,
            expected=ApprovalStatus.PENDING_APPROVAL,
            target=target,
            actor=Actor(decider.subject, decider.role),
            record_decision=True,
            decision_reason=request.reason,
            unexpired_only=True,
        )
        if updated is None:
            raise await self._decision_conflict(approval_id)
        if target is ApprovalStatus.REJECTED:
            await self._announce("approval.rejected", updated)
            return await self._view(updated)
        await self._announce("approval.approved", updated)
        # The decider may hold approvals:decide without approvals:read; it may always see
        # the outcome of its own decision.
        return await self._view(await self.execute(updated))

    # --- execution ---------------------------------------------------------------------
    async def execute(self, approval: Approval) -> Approval:
        """Run an APPROVED call exactly once and return its final state.

        The run is a background task awaited through a shield: cancelling the caller (an
        operator closing the browser) does not cancel the run."""
        task = self._executions.get(approval.id)
        if task is None or task.done():
            task = asyncio.create_task(self._execute(approval), name=f"approval:{approval.id}")
            self._executions[approval.id] = task
            task.add_done_callback(lambda done: self._forget(approval.id, done))
        return await asyncio.shield(task)

    async def aclose(self, timeout_seconds: float = 30.0) -> None:
        """Let in-flight executions finish (shutdown); recovery handles any that cannot."""
        if self._executions:
            await asyncio.wait(set(self._executions.values()), timeout=timeout_seconds)

    def _forget(self, approval_id: UUID, task: asyncio.Task[Approval]) -> None:
        if self._executions.get(approval_id) is task:
            del self._executions[approval_id]

    async def _execute(self, approval: Approval) -> Approval:
        claimed = await self._store.transition(
            approval.id,
            expected=ApprovalStatus.APPROVED,
            target=ApprovalStatus.EXECUTING,
            actor=SYSTEM_ACTOR,
        )
        if claimed is None:  # another replica (or the recovery sweeper) owns it
            return await self._store.get(approval.id) or approval
        try:
            return await self._run(claimed)
        except Exception as exc:
            logger.exception("execution of approval %s failed", approval.id)
            return await self._fail(
                claimed, f"execution error ({type(exc).__name__}); {_UNKNOWN_OUTCOME}"
            )

    async def _run(self, approval: Approval) -> Approval:
        document = self._policy.document
        spec = document.tools.get(approval.tool_name)
        entry = (await self._registry.catalog()).get(approval.tool_name)
        if spec is None or entry is None:
            return await self._fail(approval, "the tool's upstream server is unavailable")
        try:
            snapshot = await self._budget.charge_tool_call(
                tenant_id=approval.tenant_id,
                session_id=approval.session_id,
                limits=self._engine.session_budget(approval.requester_role),
                cost_usd=spec.cost_usd_per_call,
                ttl_seconds=document.budgets.ledger_ttl_seconds,
            )
        except BudgetExceededError as exc:
            await self._spend.exhausted(
                tenant_id=approval.tenant_id,
                session_id=approval.session_id,
                subject=approval.requester_subject,
                limit=exc.limit,
                snapshot=exc.snapshot,
            )
            return await self._fail(approval, f"session budget exhausted ({exc.limit})")
        self._spend.record_tool_cost(
            tenant_id=approval.tenant_id, tool=approval.tool_name, cost_usd=spec.cost_usd_per_call
        )
        identity = CallerIdentity(
            subject=approval.requester_subject,
            tenant_id=approval.tenant_id,
            session_id=approval.session_id,
            role=approval.requester_role,
            approval_id=str(approval.id),
        )
        try:
            with tracer.start_as_current_span(
                "gateway.approval.execute",
                attributes={Attr.APPROVAL_ID: str(approval.id), Attr.TOOL: approval.tool_name},
            ):
                result = await self._registry.call(entry, identity, approval.arguments)
        except UpstreamError as exc:
            if exc.executed == "no":
                await self._budget.refund_tool_call(
                    tenant_id=approval.tenant_id,
                    session_id=approval.session_id,
                    cost_usd=spec.cost_usd_per_call,
                )
                return await self._fail(approval, str(exc))
            return await self._fail(approval, f"{exc}; {_UNKNOWN_OUTCOME}")
        executed = await self._store.transition(
            approval.id,
            expected=ApprovalStatus.EXECUTING,
            target=ApprovalStatus.EXECUTED,
            actor=SYSTEM_ACTOR,
            result=result.model_dump(by_alias=True, mode="json", exclude_none=True),
            executed=True,
            detail={"is_error": result.is_error},
        )
        if executed is None:  # recovery gave up on it while the upstream call was running
            logger.error("approval %s completed after recovery marked it failed", approval.id)
            return await self._store.get(approval.id) or approval
        await self._announce("approval.executed", executed, is_error=result.is_error)
        await self._spend.after_spend(
            tenant_id=approval.tenant_id,
            session_id=approval.session_id,
            subject=approval.requester_subject,
            role=approval.requester_role,
            snapshot=snapshot,
            tool=approval.tool_name,
            approval_id=str(approval.id),
        )
        return executed

    async def _fail(self, approval: Approval, error: str) -> Approval:
        failed = await self._store.transition(
            approval.id,
            expected=ApprovalStatus.EXECUTING,
            target=ApprovalStatus.FAILED,
            actor=SYSTEM_ACTOR,
            error=error,
        )
        if failed is None:
            return await self._store.get(approval.id) or approval
        await self._announce("approval.failed", failed, error=error)
        return failed

    # --- background work ---------------------------------------------------------------
    async def expire_due(self) -> list[Approval]:
        expired = await self._store.expire_due()
        for approval in expired:
            await self._announce("approval.expired", approval)
        return expired

    async def recover_stalled(
        self, *, approved_after_seconds: float, executing_after_seconds: float
    ) -> None:
        """Resolve calls a crashed replica left in flight (any replica may run this)."""
        for approval in await self._store.stalled(
            ApprovalStatus.APPROVED, older_than_seconds=approved_after_seconds
        ):
            logger.warning("approval %s was approved but never started; executing it", approval.id)
            await self.execute(approval)
        for approval in await self._store.stalled(
            ApprovalStatus.EXECUTING, older_than_seconds=executing_after_seconds
        ):
            logger.warning("approval %s was interrupted while executing", approval.id)
            await self._fail(
                approval, f"execution was interrupted (gateway restart); {_UNKNOWN_OUTCOME}"
            )

    # --- helpers -----------------------------------------------------------------------
    async def _view(self, approval: Approval) -> ApprovalView:
        current = await self._store.get(approval.id) or approval
        audit = await self._store.audit_trail(approval.id)
        return ApprovalView(**current.model_dump(), audit=audit)

    async def _decision_conflict(self, approval_id: UUID) -> Conflict:
        current = await self._store.get(approval_id)
        if current is not None and current.status is ApprovalStatus.PENDING_APPROVAL:
            if current.expires_at <= datetime.now(UTC):
                await self.expire_due()
            return Conflict("approval expired before the decision", code="approval_expired")
        status = current.status.value if current is not None else "gone"
        return Conflict(f"approval is already {status}", code="already_decided")

    async def _announce(self, event_type: EventType, approval: Approval, **extra: Any) -> None:
        self._instruments.approvals.add(
            1, {"status": approval.status.value, Attr.TOOL: approval.tool_name}
        )
        await self._notifier.emit(
            event_type,
            tenant_id=approval.tenant_id,
            session_id=approval.session_id,
            data={**summary(approval), **extra},
        )


def summary(approval: Approval) -> dict[str, Any]:
    """Event payload: enough for an operator to review the call without another lookup."""
    return {
        "approval_id": str(approval.id),
        "status": approval.status.value,
        "tool": approval.tool_name,
        "arguments": approval.arguments,
        "risk": approval.risk,
        "requester": {"subject": approval.requester_subject, "role": approval.requester_role},
        "approver_roles": list(approval.approver_roles),
        "expires_at": approval.expires_at.isoformat(),
        "decided_by": approval.decided_by_subject,
        "decision_reason": approval.decision_reason,
        "trace_id": approval.trace_id,
    }
