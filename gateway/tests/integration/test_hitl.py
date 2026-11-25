"""(c) Human-in-the-loop, end to end: pause -> approve -> resume (and every other exit)."""

from __future__ import annotations

import time
import uuid
from typing import Any
from uuid import UUID

import anyio
import asyncpg
import pytest

from mcp_gateway.events.webhooks import verify

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

UPDATE = "crm-ops__update_credit_limit"
WEBHOOK_KEY = b"webhook-" + b"w" * 40


def governance(result: Any) -> dict[str, Any]:
    meta: dict[str, Any] = (result.meta or {}).get("mcp-enterprise-gateway/governance", {})
    return meta


def new_sid() -> str:
    return f"s-{uuid.uuid4().hex[:12]}"


async def pending_approval(
    api: Any, operator: str, session_id: str, timeout: float = 10
) -> dict[str, Any]:
    """Poll the approval queue the way the operator console does."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = await api.get(
            "/v1/approvals",
            params={"status": "PENDING_APPROVAL"},
            headers={"Authorization": f"Bearer {operator}"},
        )
        assert response.status_code == 200
        for approval in response.json()["approvals"]:
            if approval["session_id"] == session_id:
                found: dict[str, Any] = approval
                return found
        await anyio.sleep(0.05)
    pytest.fail(f"no pending approval appeared for session {session_id}")


def webhook(receiver: Any, event_type: str, approval_id: str, timeout: float = 10) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for delivery in receiver.of_type(event_type):
            if delivery.event["data"].get("approval_id") == approval_id:
                return delivery
        time.sleep(0.05)
    pytest.fail(f"no {event_type} webhook for {approval_id}")


async def test_pause_approve_resume_inline(
    mcp_client: Any,
    mint: Any,
    api: Any,
    auth: Any,
    crm_ops: Any,
    gateway_db: Any,
    stream_events: Any,
    webhook_receiver: Any,
) -> None:
    sid = new_sid()
    agent = mint("admin", sub="automation-bot", sid=sid)
    operator = mint("operator", sub="ops-alice", sid=None)
    arguments = {
        "account_id": "ACC-1005",
        "new_limit_usd": 1_500_000,
        "reason": "Q4 expansion signed off by the CFO",
    }
    limit_before = crm_ops.store.credit_limit("acme", "ACC-1005")
    outcome: dict[str, Any] = {}

    async with mcp_client(agent) as client, anyio.create_task_group() as tasks:

        async def agent_call() -> None:
            outcome["result"] = await client.call_tool(UPDATE, arguments)

        tasks.start_soon(agent_call)
        approval = await pending_approval(api, operator, sid)
        approval_id = approval["id"]

        # Paused: persisted with its exact payload, announced, and not executed.
        row = await gateway_db.fetchrow(
            "SELECT status, arguments, requester_subject, approver_roles "
            "FROM approval_requests WHERE id = $1",
            UUID(approval_id),
        )
        assert row["status"] == "PENDING_APPROVAL"
        assert row["arguments"] == arguments
        assert row["requester_subject"] == "automation-bot"
        assert list(row["approver_roles"]) == ["operator"]
        assert crm_ops.store.credit_limit("acme", "ACC-1005") == limit_before
        assert "result" not in outcome  # the agent's call is still held
        requested = webhook(webhook_receiver, "approval.requested", approval_id)
        assert verify(WEBHOOK_KEY, requested.headers["x-mcp-gateway-signature"], requested.body)
        assert requested.event["data"]["arguments"] == arguments

        # The requester can never approve its own call.
        self_approval = await api.post(
            f"/v1/approvals/{approval_id}/decide", json={"decision": "approve"}, headers=auth(agent)
        )
        assert self_approval.status_code == 403

        # An operator approves: the gateway executes the stored call.
        decided = await api.post(
            f"/v1/approvals/{approval_id}/decide",
            json={"decision": "approve", "reason": "within delegated authority"},
            headers=auth(operator),
        )
        assert decided.status_code == 200, decided.text
        assert decided.json()["status"] == "EXECUTED"

    # Resumed: the agent's held call returned the real upstream result.
    result = outcome["result"]
    assert not result.is_error
    assert governance(result)["code"] == "APPROVED"
    assert governance(result)["decided_by"] == "ops-alice"
    assert result.structured_content["new_limit_usd"] == 1_500_000
    assert result.structured_content["previous_limit_usd"] == limit_before
    assert result.structured_content["approval_id"] == approval_id
    assert result.structured_content["changed_by"] == "automation-bot"
    assert crm_ops.store.credit_limit("acme", "ACC-1005") == 1_500_000

    view = decided.json()
    assert [entry["to_status"] for entry in view["audit"]] == [
        "PENDING_APPROVAL",
        "APPROVED",
        "EXECUTING",
        "EXECUTED",
    ]
    assert view["decided_by_subject"] == "ops-alice"
    types = [
        e["type"] for e in await stream_events() if e["data"].get("approval_id") == approval_id
    ]
    assert types == ["approval.requested", "approval.approved", "approval.executed"]


async def test_pause_checkpoint_then_resume_after_inline_wait(
    mcp_client: Any, mint: Any, api: Any, auth: Any, crm_ops: Any
) -> None:
    """No decision within inline_wait_seconds: the agent gets PENDING_APPROVAL (to
    checkpoint), keeps receiving progress meanwhile, and later fetches the outcome."""
    sid = new_sid()
    agent = mint("admin", sub="night-batch", sid=sid)
    operator = mint("operator", sub="ops-carol", sid=None)
    progress: list[str] = []

    async def on_progress(_progress: float, _total: float | None, message: str | None) -> None:
        progress.append(message or "")

    arguments = {
        "account_id": "ACC-1002",
        "new_limit_usd": 300_000,
        "reason": "renewal with updated financials",
    }
    started = time.monotonic()
    async with mcp_client(agent) as client:
        held = await client.call_tool(UPDATE, arguments, progress_callback=on_progress)
    assert time.monotonic() - started >= 3  # held for the inline wait
    assert held.is_error
    assert governance(held)["code"] == "PENDING_APPROVAL"
    approval_id = governance(held)["approval_id"]
    assert "Do not retry" in held.content[0].text
    assert any("waiting for human approval" in message for message in progress)

    # The agent can look up its own approval (to resume), but not other sessions' queues.
    own = await api.get(f"/v1/approvals/{approval_id}", headers=auth(agent))
    assert own.status_code == 200
    assert own.json()["status"] == "PENDING_APPROVAL"
    assert (await api.get("/v1/approvals", headers=auth(agent))).status_code == 403

    decided = await api.post(
        f"/v1/approvals/{approval_id}/decide", json={"decision": "approve"}, headers=auth(operator)
    )
    assert decided.json()["status"] == "EXECUTED"

    resumed = await api.get(f"/v1/approvals/{approval_id}", headers=auth(agent))
    body = resumed.json()
    assert body["status"] == "EXECUTED"
    assert body["result"]["structuredContent"]["new_limit_usd"] == 300_000
    assert crm_ops.store.credit_limit("acme", "ACC-1002") == 300_000


async def test_rejected_calls_never_execute(
    mcp_client: Any, mint: Any, api: Any, auth: Any, crm_ops: Any
) -> None:
    sid = new_sid()
    agent = mint("admin", sid=sid)
    operator = mint("operator", sub="ops-dave", sid=None)
    changes_before = len(crm_ops.store.changes)
    outcome: dict[str, Any] = {}
    async with mcp_client(agent) as client, anyio.create_task_group() as tasks:

        async def agent_call() -> None:
            outcome["result"] = await client.call_tool(
                UPDATE,
                {
                    "account_id": "ACC-1003",
                    "new_limit_usd": 2_000_000,
                    "reason": "please raise it a lot",
                },
            )

        tasks.start_soon(agent_call)
        approval = await pending_approval(api, operator, sid)
        missing_reason = await api.post(
            f"/v1/approvals/{approval['id']}/decide",
            json={"decision": "reject"},
            headers=auth(operator),
        )
        assert missing_reason.status_code == 422
        rejected = await api.post(
            f"/v1/approvals/{approval['id']}/decide",
            json={"decision": "reject", "reason": "26x current exposure; needs underwriting"},
            headers=auth(operator),
        )
        assert rejected.json()["status"] == "REJECTED"

    result = outcome["result"]
    assert governance(result)["code"] == "REJECTED"
    assert "needs underwriting" in result.content[0].text
    assert len(crm_ops.store.changes) == changes_before


async def test_identical_pending_calls_share_one_approval(
    mcp_client: Any, mint: Any, api: Any, auth: Any, crm_ops: Any, gateway_db: Any
) -> None:
    sid = new_sid()
    agent = mint("admin", sid=sid)
    operator = mint("operator", sub="ops-erin", sid=None)
    arguments = {
        "account_id": "ACC-1001",
        "new_limit_usd": 650_000,
        "reason": "seasonal peak capacity",
    }
    changes_before = len(crm_ops.store.changes)
    results: list[Any] = []
    async with mcp_client(agent) as client, anyio.create_task_group() as tasks:

        async def agent_call() -> None:
            results.append(await client.call_tool(UPDATE, arguments))

        tasks.start_soon(agent_call)
        tasks.start_soon(agent_call)
        approval = await pending_approval(api, operator, sid)
        await anyio.sleep(0.3)  # let the second call join the same pending request
        count = await gateway_db.fetchval(
            "SELECT count(*) FROM approval_requests WHERE session_id = $1", sid
        )
        assert count == 1
        await api.post(
            f"/v1/approvals/{approval['id']}/decide",
            json={"decision": "approve"},
            headers=auth(operator),
        )

    assert len(results) == 2
    assert {r.structured_content["change_id"] for r in results if not r.is_error} == {
        crm_ops.store.changes[-1][1].change_id
    }
    assert len(crm_ops.store.changes) == changes_before + 1  # executed exactly once


async def test_decisions_are_guarded(mcp_client: Any, mint: Any, api: Any, auth: Any) -> None:
    sid = new_sid()
    agent = mint("admin", sid=sid)
    async with mcp_client(agent) as client:
        held = await client.call_tool(
            UPDATE,
            {
                "account_id": "ACC-1001",
                "new_limit_usd": 520_000,
                "reason": "minor uplift requested",
            },
        )
    approval_id = governance(held)["approval_id"]
    url = f"/v1/approvals/{approval_id}/decide"
    approve = {"decision": "approve"}

    other_tenant = await api.post(
        url, json=approve, headers=auth(mint("operator", tenant="zenith", sid=None))
    )
    assert other_tenant.status_code == 404  # tenants cannot even see each other's approvals
    auditor = await api.post(url, json=approve, headers=auth(mint("auditor", sid=None)))
    assert auditor.status_code == 403  # may read, may not decide
    agent_role = await api.post(url, json=approve, headers=auth(mint("admin", sid=None)))
    assert agent_role.status_code == 403  # agent roles never hold approvals:decide

    operator = mint("operator", sub="ops-frank", sid=None)
    assert (await api.post(url, json=approve, headers=auth(operator))).status_code == 200
    second = await api.post(url, json=approve, headers=auth(operator))
    assert second.status_code == 409
    assert second.json()["code"] == "already_decided"


async def test_expired_requests_never_execute(
    mcp_client: Any,
    mint: Any,
    api: Any,
    auth: Any,
    gateway_db: Any,
    crm_ops: Any,
    stream_events: Any,
) -> None:
    sid = new_sid()
    agent = mint("admin", sid=sid)
    async with mcp_client(agent) as client:
        held = await client.call_tool(
            UPDATE,
            {
                "account_id": "ACC-1001",
                "new_limit_usd": 999_000,
                "reason": "nobody will look at this",
            },
        )
    approval_id = governance(held)["approval_id"]
    # Simulate the passage of the 30-minute timeout. expires_at is immutable by design, so
    # the guard trigger is lifted for this one statement only.
    async with gateway_db.transaction():
        await gateway_db.execute(
            "ALTER TABLE approval_requests DISABLE TRIGGER approval_requests_guard"
        )
        await gateway_db.execute(
            "UPDATE approval_requests SET created_at = now() - interval '2 hours', "
            "expires_at = now() - interval '1 second' WHERE id = $1",
            UUID(approval_id),
        )
        await gateway_db.execute(
            "ALTER TABLE approval_requests ENABLE TRIGGER approval_requests_guard"
        )

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = await gateway_db.fetchval(
            "SELECT status FROM approval_requests WHERE id = $1", UUID(approval_id)
        )
        if status == "EXPIRED":
            break
        await anyio.sleep(0.1)
    assert status == "EXPIRED"  # the sweeper expired it

    late = await api.post(
        f"/v1/approvals/{approval_id}/decide",
        json={"decision": "approve"},
        headers=auth(mint("operator", sid=None)),
    )
    assert late.status_code == 409
    assert crm_ops.store.credit_limit("acme", "ACC-1001") != 999_000
    assert any(
        e["type"] == "approval.expired" and e["data"]["approval_id"] == approval_id
        for e in await stream_events()
    )


async def test_database_enforces_the_approval_invariants(
    mcp_client: Any, mint: Any, gateway_db: Any
) -> None:
    async with mcp_client(mint("admin")) as client:
        held = await client.call_tool(
            UPDATE,
            {"account_id": "ACC-1001", "new_limit_usd": 510_000, "reason": "invariant check"},
        )
    approval_id = UUID(governance(held)["approval_id"])

    with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="immutable"):
        await gateway_db.execute(
            "UPDATE approval_requests SET arguments = '{\"new_limit_usd\": 5000000}' WHERE id = $1",
            approval_id,
        )
    with pytest.raises(
        asyncpg.IntegrityConstraintViolationError, match="illegal approval transition"
    ):
        await gateway_db.execute(
            "UPDATE approval_requests SET status = 'EXECUTED', decided_at = now(), "
            "decided_by_subject = 'nobody' WHERE id = $1",
            approval_id,
        )
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="append-only"):
        await gateway_db.execute("DELETE FROM approval_audit WHERE approval_id = $1", approval_id)
