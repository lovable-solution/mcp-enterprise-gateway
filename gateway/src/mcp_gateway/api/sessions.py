"""Agent sessions: live budget and breaker state, usage reporting, breaker reset."""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Path
from pydantic import BaseModel, ConfigDict

from ..auth.principal import SESSION_ID_REGEX, Principal
from ..enforcement.budget import BudgetSnapshot, UsageReport, price_usage
from ..enforcement.sessions import SessionInfo
from ..errors import Forbidden, NotFound
from ..policy.models import ControlAction
from ..runtime import GatewayRuntime
from ..telemetry import Attr
from .deps import PrincipalDep, RuntimeDep, require

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])

SessionIdPath = Annotated[str, Path(pattern=SESSION_ID_REGEX)]


class BreakerStatus(BaseModel):
    open: bool
    retry_after_seconds: int | None = None
    trip: dict[str, Any] | None = None


class SessionStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    session: SessionInfo
    budget: BudgetSnapshot
    circuit_breaker: BreakerStatus


class SessionList(BaseModel):
    sessions: list[SessionStatus]


class UsageAccepted(BaseModel):
    cost_usd: Decimal
    priced_at_fallback: bool
    """True when the model is not in the price table (charged at the conservative rate)."""
    budget: BudgetSnapshot


async def _status(runtime: GatewayRuntime, tenant_id: str, info: SessionInfo) -> SessionStatus:
    budget = await runtime.budget.snapshot(
        tenant_id, info.session_id, runtime.engine.session_budget(info.role)
    )
    trip, retry_after = await runtime.breaker.status(tenant_id, info.session_id)
    return SessionStatus(
        session=info,
        budget=budget,
        circuit_breaker=BreakerStatus(
            open=trip is not None,
            retry_after_seconds=retry_after,
            trip=None if trip is None else asdict(trip),
        ),
    )


async def _session(runtime: GatewayRuntime, principal: Principal, session_id: str) -> SessionInfo:
    info = await runtime.sessions.get(principal.tenant_id, session_id)
    if info is None:
        raise NotFound("no such session")
    return info


@router.get("", summary="Recently active sessions of the caller's tenant")
async def list_sessions(
    runtime: RuntimeDep, principal: Annotated[Principal, require(ControlAction.SESSIONS_READ)]
) -> SessionList:
    ttl = runtime.policy.document.budgets.ledger_ttl_seconds
    infos = await runtime.sessions.recent(principal.tenant_id, ttl_seconds=ttl)
    return SessionList(
        sessions=[await _status(runtime, principal.tenant_id, info) for info in infos]
    )


@router.get("/{session_id}", summary="One session's budget and breaker state")
async def get_session(
    session_id: SessionIdPath, runtime: RuntimeDep, principal: PrincipalDep
) -> SessionStatus:
    own = principal.session_id == session_id
    if not own and not runtime.engine.allows_action(principal.role, ControlAction.SESSIONS_READ):
        raise Forbidden(f"role {principal.role!r} may not read other sessions")
    return await _status(
        runtime, principal.tenant_id, await _session(runtime, principal, session_id)
    )


@router.post("/{session_id}/usage", summary="Report model token usage for the caller's own session")
async def report_usage(
    session_id: SessionIdPath, report: UsageReport, runtime: RuntimeDep, principal: PrincipalDep
) -> UsageAccepted:
    """Called by the agent runtime after every model call. The gateway prices the usage,
    charges the session ledger, and refuses further tool calls once a cap is reached."""
    if principal.session_id != session_id:
        raise Forbidden(
            "usage can only be reported for the caller's own session", code="foreign_session"
        )
    policy = runtime.policy.document
    limits = runtime.engine.session_budget(principal.role)
    priced = price_usage(report, policy.pricing)
    await runtime.sessions.touch(principal, policy.budgets.ledger_ttl_seconds)
    snapshot = await runtime.budget.record_usage(
        tenant_id=principal.tenant_id,
        session_id=session_id,
        limits=limits,
        report=report,
        cost_usd=priced.cost_usd,
        ttl_seconds=policy.budgets.ledger_ttl_seconds,
    )
    labels = {Attr.MODEL: report.model, Attr.TENANT: principal.tenant_id}
    for token_type, count in (
        ("input", report.input_tokens),
        ("output", report.output_tokens),
        ("cache_write", report.cache_creation_input_tokens),
        ("cache_read", report.cache_read_input_tokens),
    ):
        if count:
            runtime.instruments.tokens.add(count, {**labels, Attr.TOKEN_TYPE: token_type})
    runtime.instruments.cost_usd.add(float(priced.cost_usd), {**labels, Attr.COST_SOURCE: "llm"})
    await runtime.pipeline.after_spend(principal, snapshot, model=report.model)
    return UsageAccepted(
        cost_usd=priced.cost_usd, priced_at_fallback=priced.priced_at_fallback, budget=snapshot
    )


@router.post("/{session_id}/circuit-breaker/reset", summary="Close a tripped circuit breaker")
async def reset_breaker(
    session_id: SessionIdPath,
    runtime: RuntimeDep,
    principal: Annotated[Principal, require(ControlAction.CIRCUIT_BREAKERS_RESET)],
) -> SessionStatus:
    info = await _session(runtime, principal, session_id)
    was_open = await runtime.breaker.reset(principal.tenant_id, session_id)
    if was_open:
        await runtime.notifier.emit(
            "circuit_breaker.reset",
            tenant_id=principal.tenant_id,
            session_id=session_id,
            data={"reset_by": principal.subject, "role": principal.role},
        )
    return await _status(runtime, principal.tenant_id, info)
