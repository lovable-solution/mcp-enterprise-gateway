"""Approval queue API (operator console) and approval lookup for resuming agents."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from ..approvals.models import ApprovalList, ApprovalStatus, ApprovalView, DecisionRequest
from ..auth.principal import Principal
from ..policy.models import ControlAction
from .deps import PrincipalDep, RuntimeDep, require

router = APIRouter(prefix="/v1/approvals", tags=["approvals"])


@router.get("", summary="List approval requests of the caller's tenant")
async def list_approvals(
    runtime: RuntimeDep,
    principal: Annotated[Principal, require(ControlAction.APPROVALS_READ)],
    status: ApprovalStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ApprovalList:
    approvals = await runtime.approvals.recent(principal, status=status, limit=limit)
    return ApprovalList(approvals=approvals)


@router.get("/{approval_id}", summary="One approval with its audit trail")
async def get_approval(
    approval_id: UUID, runtime: RuntimeDep, principal: PrincipalDep
) -> ApprovalView:
    """Operators with ``approvals:read``, or the requesting agent session (to resume)."""
    return await runtime.approvals.get_for(principal, approval_id)


@router.post("/{approval_id}/decide", summary="Approve or reject a pending call")
async def decide(
    approval_id: UUID,
    body: DecisionRequest,
    runtime: RuntimeDep,
    principal: Annotated[Principal, require(ControlAction.APPROVALS_DECIDE)],
) -> ApprovalView:
    """Approving executes the stored call immediately and returns its result.

    Refused with 403 unless the caller's role is an approver for the tool and the caller
    is not the requester (four-eyes); 409 if the request is no longer pending.
    """
    return await runtime.approvals.decide(approval_id, principal, body)
