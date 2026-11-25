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
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from ..auth.principal import Principal
from ..enforcement.budget import BudgetExceededError, BudgetLedger
from ..errors import Conflict, Forbidden, NotFound
from ..events.bus import EventBus, EventType
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


class ApprovalService:
    def __init__(
        self,
        *,
        store: ApprovalStore,
        bus: EventBus,
        notifier: Notifier,
        registry: UpstreamRegistry,
        budget: BudgetLedger,
        policy: LoadedPolicy,
        engine: PolicyEngine,
        instruments: Instruments,
    ) -> None:
        self._store = store
        self._bus = bus
        self._notifier = notifier
        self._registry = registry
        self._budget = budget
        self._policy = policy
        self._engine = engine
        self._instruments = instruments

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
        # Take the stream position *before* re-reading the row: a decision that lands in
        # between is then either visible in the row or delivered as an event.
        position = await self._bus.last_id(approval.tenant_id)
        current = await self._store.get(approval.id) or approval
        started = time.monotonic()
        deadline = started + timeout_seconds
        with tracer.start_as_current_span(
            "gateway.approval.wait", attributes={Attr.APPROVAL_ID: str(approval.id)}
        ):
            while not current.status.is_terminal and (remaining := deadline - time.monotonic()) > 0:
                block_ms = max(1, int(min(remaining, _WAIT_SLICE_SECONDS) * 1000))
                for event in await self._bus.read(approval.tenant_id, position, block_ms=block_ms):
                    position = event.id or position
                    if event.type in TERMINAL_EVENT_TYPES and event.data.get("approval_id") == str(
                        approval.id
                    ):
                        current = await self._store.get(approval.id) or current
                if not current.status.is_terminal and on_tick is not None:
                    await on_tick(time.monotonic() - started)
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
        audit = await self._store.audit_trail(approval_id)
        return ApprovalView(**approval.model_dump(), audit=audit)

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

        actor = Actor(decider.subject, decider.role)
        target = (
            ApprovalStatus.APPROVED
            if request.decision is Decision.APPROVE
            else ApprovalStatus.REJECTED
        )
        updated = await self._store.transition(
            approval_id,
            expected=ApprovalStatus.PENDING_APPROVAL,
            target=target,
            actor=actor,
            record_decision=True,
            decision_reason=request.reason,
            unexpired_only=True,
        )
        if updated is None:
            await self._raise_decision_conflict(approval_id)
        assert updated is not None  # noqa: S101 - _raise_decision_conflict always raises
        if target is ApprovalStatus.REJECTED:
            await self._announce("approval.rejected", updated)
        else:
            await self._announce("approval.approved", updated)
            updated = await self.execute(updated)
        return await self.get_for(decider, updated.id)

    async def execute(self, approval: Approval) -> Approval:
        """Run an APPROVED call exactly once (compare-and-set claim), recording the outcome."""
        claimed = await self._store.transition(
            approval.id,
            expected=ApprovalStatus.APPROVED,
            target=ApprovalStatus.EXECUTING,
            actor=SYSTEM_ACTOR,
        )
        if claimed is None:  # another replica owns it
            return await self._store.get(approval.id) or approval
        document = self._policy.document
        spec = document.tools.get(approval.tool_name)
        entry = (await self._registry.catalog()).get(approval.tool_name)
        failure: str | None = None
        if spec is None or entry is None:
            failure = "the tool's upstream server is unavailable"
        else:
            try:
                await self._budget.charge_tool_call(
                    tenant_id=approval.tenant_id,
                    session_id=approval.session_id,
                    limits=self._engine.session_budget(approval.requester_role),
                    cost_usd=spec.cost_usd_per_call,
                    ttl_seconds=document.budgets.ledger_ttl_seconds,
                )
            except BudgetExceededError as exc:
                failure = f"session budget exhausted ({exc.limit})"
        if failure is None and entry is not None:
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
                failure = str(exc)
            else:
                executed = await self._store.transition(
                    approval.id,
                    expected=ApprovalStatus.EXECUTING,
                    target=ApprovalStatus.EXECUTED,
                    actor=SYSTEM_ACTOR,
                    result=result.model_dump(by_alias=True, mode="json", exclude_none=True),
                    executed=True,
                    detail={"is_error": result.is_error},
                )
                assert executed is not None  # noqa: S101 - we hold the EXECUTING claim
                await self._announce("approval.executed", executed, is_error=result.is_error)
                return executed
        failed = await self._store.transition(
            approval.id,
            expected=ApprovalStatus.EXECUTING,
            target=ApprovalStatus.FAILED,
            actor=SYSTEM_ACTOR,
            error=failure,
        )
        assert failed is not None  # noqa: S101 - we hold the EXECUTING claim
        await self._announce("approval.failed", failed, error=failure)
        return failed

    async def expire_due(self) -> list[Approval]:
        expired = await self._store.expire_due()
        for approval in expired:
            await self._announce("approval.expired", approval)
        return expired

    # --- helpers -----------------------------------------------------------------------
    async def _raise_decision_conflict(self, approval_id: UUID) -> None:
        current = await self._store.get(approval_id)
        if current is not None and current.status is ApprovalStatus.PENDING_APPROVAL:
            if current.expires_at <= datetime.now(UTC):
                await self.expire_due()
            raise Conflict("approval expired before the decision", code="approval_expired")
        status = current.status.value if current is not None else "gone"
        raise Conflict(f"approval is already {status}", code="already_decided")

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
