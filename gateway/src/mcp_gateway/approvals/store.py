"""PostgreSQL repository for approvals.

Every state change is a single conditional ``UPDATE ... WHERE status = <expected>``
(compare-and-set) plus its audit row, in one transaction: a decision can never be applied
twice, and two replicas can never both execute the same approved call. Timestamps come
from the database clock.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

import asyncpg
from asyncpg.pool import PoolConnectionProxy

from .models import Approval, ApprovalStatus, AuditEntry

_RETURNING: Final = "RETURNING *"


@dataclass(frozen=True, slots=True)
class NewApproval:
    id: UUID
    tenant_id: str
    session_id: str
    requester_subject: str
    requester_role: str
    tool_name: str
    arguments: dict[str, Any]
    arguments_digest: str
    risk: str
    approver_roles: Sequence[str]
    policy_name: str
    policy_digest: str
    policy_rule: str
    trace_id: str | None
    timeout_seconds: int


@dataclass(frozen=True, slots=True)
class Actor:
    subject: str
    role: str


SYSTEM_ACTOR: Final = Actor(subject="mcp-gateway", role="system")


class ApprovalStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create_or_get_pending(self, new: NewApproval) -> tuple[Approval, bool]:
        """Insert a pending approval, or return the identical call already pending in the
        same session (``created`` is False then)."""
        for _ in range(3):
            async with self._pool.acquire() as connection, connection.transaction():
                row = await connection.fetchrow(
                    """
                    INSERT INTO approval_requests (
                        id, tenant_id, session_id, requester_subject, requester_role,
                        tool_name, arguments, arguments_digest, risk, approver_roles, status,
                        policy_name, policy_digest, policy_rule, trace_id, expires_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'PENDING_APPROVAL',
                            $11, $12, $13, $14, now() + make_interval(secs => $15))
                    ON CONFLICT (tenant_id, session_id, tool_name, arguments_digest)
                        WHERE status = 'PENDING_APPROVAL' DO NOTHING
                    RETURNING *
                    """,
                    new.id,
                    new.tenant_id,
                    new.session_id,
                    new.requester_subject,
                    new.requester_role,
                    new.tool_name,
                    new.arguments,
                    new.arguments_digest,
                    new.risk,
                    list(new.approver_roles),
                    new.policy_name,
                    new.policy_digest,
                    new.policy_rule,
                    new.trace_id,
                    float(new.timeout_seconds),
                )
                if row is not None:
                    await _audit(
                        connection,
                        new.id,
                        from_status=None,
                        to_status=ApprovalStatus.PENDING_APPROVAL,
                        actor=Actor(new.requester_subject, new.requester_role),
                        detail={"policy_rule": new.policy_rule, "policy_digest": new.policy_digest},
                    )
                    return _approval(row), True
                existing = await connection.fetchrow(
                    """
                    SELECT * FROM approval_requests
                    WHERE tenant_id = $1 AND session_id = $2 AND tool_name = $3
                      AND arguments_digest = $4 AND status = 'PENDING_APPROVAL'
                    """,
                    new.tenant_id,
                    new.session_id,
                    new.tool_name,
                    new.arguments_digest,
                )
                if existing is not None:
                    return _approval(existing), False
            # The conflicting row was decided between INSERT and SELECT: try again.
        msg = "could not create the approval request"
        raise RuntimeError(msg)

    async def get(self, approval_id: UUID) -> Approval | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM approval_requests WHERE id = $1", approval_id
        )
        return None if row is None else _approval(row)

    async def recent(
        self, tenant_id: str, *, status: ApprovalStatus | None, limit: int
    ) -> list[Approval]:
        rows = await self._pool.fetch(
            """
            SELECT * FROM approval_requests
            WHERE tenant_id = $1 AND ($2::text IS NULL OR status = $2)
            ORDER BY created_at DESC
            LIMIT $3
            """,
            tenant_id,
            None if status is None else status.value,
            limit,
        )
        return [_approval(row) for row in rows]

    async def transition(
        self,
        approval_id: UUID,
        *,
        expected: ApprovalStatus,
        target: ApprovalStatus,
        actor: Actor,
        decision_reason: str | None = None,
        record_decision: bool = False,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        executed: bool = False,
        unexpired_only: bool = False,
        detail: dict[str, Any] | None = None,
    ) -> Approval | None:
        """Compare-and-set ``expected -> target``; None if the row was not in ``expected``
        (or, with ``unexpired_only``, had already passed its expiry)."""
        params: list[Any] = [approval_id, expected.value, target.value]
        assignments = ["status = $3", "status_changed_at = now()"]

        def bind(column: str, value: Any) -> None:
            params.append(value)
            assignments.append(f"{column} = ${len(params)}")

        if record_decision:
            assignments.append("decided_at = now()")
            bind("decided_by_subject", actor.subject)
            bind("decided_by_role", actor.role)
            bind("decision_reason", decision_reason)
        if result is not None:
            bind("result", result)
        if error is not None:
            bind("error", error)
        if executed:
            assignments.append("executed_at = now()")
        condition = "id = $1 AND status = $2" + (
            " AND expires_at > now()" if unexpired_only else ""
        )
        # Column names come from the fixed set above; every value is a bind parameter.
        query = (
            f"UPDATE approval_requests SET {', '.join(assignments)} WHERE {condition} {_RETURNING}"  # noqa: S608
        )
        async with self._pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(query, *params)
            if row is None:
                return None
            await _audit(
                connection,
                approval_id,
                from_status=expected,
                to_status=target,
                actor=actor,
                detail=detail or {},
            )
        return _approval(row)

    async def expire_due(self) -> list[Approval]:
        """Expire every pending approval past its deadline (any replica may run this)."""
        async with self._pool.acquire() as connection, connection.transaction():
            rows = await connection.fetch(
                """
                UPDATE approval_requests SET status = 'EXPIRED', status_changed_at = now()
                WHERE status = 'PENDING_APPROVAL' AND expires_at <= now()
                RETURNING *
                """
            )
            for row in rows:
                await _audit(
                    connection,
                    row["id"],
                    from_status=ApprovalStatus.PENDING_APPROVAL,
                    to_status=ApprovalStatus.EXPIRED,
                    actor=SYSTEM_ACTOR,
                    detail={"reason": "no decision before expires_at"},
                )
        return [_approval(row) for row in rows]

    async def stalled(
        self, status: ApprovalStatus, *, older_than_seconds: float, limit: int = 100
    ) -> list[Approval]:
        """Rows that have sat in ``status`` (APPROVED or EXECUTING) for too long."""
        rows = await self._pool.fetch(
            """
            SELECT * FROM approval_requests
            WHERE status = $1 AND status_changed_at < now() - make_interval(secs => $2)
            ORDER BY status_changed_at
            LIMIT $3
            """,
            status.value,
            older_than_seconds,
            limit,
        )
        return [_approval(row) for row in rows]

    async def audit_trail(self, approval_id: UUID) -> list[AuditEntry]:
        rows = await self._pool.fetch(
            """
            SELECT at, from_status, to_status, actor_subject, actor_role, detail
            FROM approval_audit WHERE approval_id = $1 ORDER BY id
            """,
            approval_id,
        )
        return [AuditEntry.model_validate(dict(row)) for row in rows]


async def _audit(
    connection: PoolConnectionProxy[asyncpg.Record],
    approval_id: UUID,
    *,
    from_status: ApprovalStatus | None,
    to_status: ApprovalStatus,
    actor: Actor,
    detail: dict[str, Any],
) -> None:
    await connection.execute(
        """
        INSERT INTO approval_audit
            (approval_id, from_status, to_status, actor_subject, actor_role, detail)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        approval_id,
        None if from_status is None else from_status.value,
        to_status.value,
        actor.subject,
        actor.role,
        detail,
    )


def _approval(row: asyncpg.Record) -> Approval:
    return Approval.model_validate(dict(row))
