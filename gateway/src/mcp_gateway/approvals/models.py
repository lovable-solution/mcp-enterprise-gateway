"""Approval records and API models.

State machine (also enforced by a database trigger)::

    PENDING_APPROVAL --approve--> APPROVED --claim--> EXECUTING --> EXECUTED | FAILED
           |                         \\--(budget/upstream unavailable)--> FAILED
           |--reject--> REJECTED
           \\--timeout-> EXPIRED            (never executed)
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Final, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ApprovalStatus(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL


_TERMINAL: Final = frozenset(
    {
        ApprovalStatus.REJECTED,
        ApprovalStatus.EXPIRED,
        ApprovalStatus.EXECUTED,
        ApprovalStatus.FAILED,
    }
)

#: Events that end an approval (waiters stop on any of them).
TERMINAL_EVENT_TYPES: Final = frozenset(
    {"approval.executed", "approval.failed", "approval.rejected", "approval.expired"}
)


class Approval(BaseModel):
    """One suspended tool call: the exact payload awaiting (or past) a human decision."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: str
    session_id: str
    requester_subject: str
    requester_role: str
    tool_name: str
    arguments: dict[str, Any]
    arguments_digest: str
    risk: str
    approver_roles: tuple[str, ...]
    status: ApprovalStatus
    policy_name: str
    policy_digest: str
    policy_rule: str
    trace_id: str | None
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None = None
    decided_by_subject: str | None = None
    decided_by_role: str | None = None
    decision_reason: str | None = None
    executed_at: datetime | None = None
    result: dict[str, Any] | None = None
    """The upstream ``CallToolResult`` (wire format) once EXECUTED."""
    error: str | None = None
    status_changed_at: datetime | None = None


class AuditEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    at: datetime
    from_status: ApprovalStatus | None
    to_status: ApprovalStatus
    actor_subject: str | None
    actor_role: str | None
    detail: dict[str, Any]


class ApprovalView(Approval):
    """Approval with its audit trail, as returned by the API."""

    audit: list[AuditEntry] = Field(default_factory=list)


class ApprovalList(BaseModel):
    approvals: list[Approval]


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Decision
    reason: str | None = Field(default=None, max_length=1000)
    """Recorded in the audit trail; required to reject."""

    @model_validator(mode="after")
    def _rejections_need_a_reason(self) -> Self:
        if self.decision is Decision.REJECT and not (self.reason and self.reason.strip()):
            msg = "a rejection needs a reason"
            raise ValueError(msg)
        return self
