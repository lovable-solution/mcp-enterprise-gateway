"""(b) Loop detection: a session sending the same call more than 3 times in 60 s is halted."""

from __future__ import annotations

import time
import uuid
from typing import Any

import pytest
from mcp import MCPError

from mcp_gateway.events.webhooks import verify

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

SEARCH = "crm-ops__search_accounts"
METRICS = "crm-ops__get_account_metrics"
UPDATE = "crm-ops__update_credit_limit"
WEBHOOK_KEY = b"webhook-" + b"w" * 40


def governance(result: Any) -> dict[str, Any]:
    meta: dict[str, Any] = (result.meta or {}).get("mcp-enterprise-gateway/governance", {})
    return meta


def new_sid() -> str:
    return f"s-{uuid.uuid4().hex[:12]}"


async def test_fourth_identical_call_trips_the_breaker(
    mcp_client: Any, mint: Any, stream_events: Any, telemetry: Any, webhook_receiver: Any
) -> None:
    sid = new_sid()
    async with mcp_client(mint("analyst", sid=sid)) as client:
        for _ in range(3):
            assert not (await client.call_tool(SEARCH, {"query": "freight", "limit": 5})).is_error
        # Same call with keys reordered: canonicalisation still sees a repeat.
        tripped = await client.call_tool(SEARCH, {"limit": 5, "query": "freight"})
        # The whole session is halted, not just the repeated tool.
        other_tool = await client.call_tool(METRICS, {"account_id": "ACC-1001"})

    assert tripped.is_error
    assert governance(tripped)["code"] == "CIRCUIT_OPEN"
    assert governance(tripped)["retry_after_seconds"] == 300
    assert "loop detected" in tripped.content[0].text
    assert governance(other_tool)["code"] == "CIRCUIT_OPEN"

    events = [e for e in await stream_events() if e["session_id"] == sid]
    trips = [e for e in events if e["type"] == "circuit_breaker.tripped"]
    assert len(trips) == 1
    assert trips[0]["data"]["tool"] == SEARCH
    assert trips[0]["data"]["identical_calls"] == 4
    assert any(
        point_attrs.get("mcp_gateway.outcome") == "circuit_open"
        for point_attrs, _ in telemetry.metric_points("mcp_gateway.tool_calls")
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        deliveries = [
            d
            for d in webhook_receiver.of_type("circuit_breaker.tripped")
            if d.event["session_id"] == sid
        ]
        if deliveries:
            break
        time.sleep(0.05)
    assert deliveries, "no signed webhook for the trip"
    delivery = deliveries[0]
    assert verify(WEBHOOK_KEY, delivery.headers["x-mcp-gateway-signature"], delivery.body)


async def test_breakers_are_per_session(mcp_client: Any, mint: Any) -> None:
    agent = "analyst-shared-identity"
    async with mcp_client(mint("analyst", sub=agent, sid=new_sid())) as looping:
        for _ in range(4):
            last = await looping.call_tool(SEARCH, {"query": "energy"})
    assert governance(last)["code"] == "CIRCUIT_OPEN"
    async with mcp_client(mint("analyst", sub=agent, sid=new_sid())) as fresh:
        assert not (await fresh.call_tool(SEARCH, {"query": "energy"})).is_error


async def test_distinct_calls_are_not_a_loop(mcp_client: Any, mint: Any) -> None:
    async with mcp_client(mint("analyst")) as client:
        for query in ("alder", "birch", "cedar", "dunmore", "elm", "alder "):
            assert not (await client.call_tool(SEARCH, {"query": query})).is_error


async def test_hammering_a_forbidden_tool_also_trips(mcp_client: Any, mint: Any) -> None:
    """Loop detection runs before RBAC: retrying a denied call is a loop too."""
    args = {"account_id": "ACC-1001", "new_limit_usd": 10, "reason": "retrying until it works"}
    async with mcp_client(mint("analyst")) as client:
        for _ in range(3):
            with pytest.raises(MCPError):
                await client.call_tool(UPDATE, args)
        fourth = await client.call_tool(UPDATE, args)
    assert governance(fourth)["code"] == "CIRCUIT_OPEN"


async def test_only_operators_can_reset_a_breaker(
    mcp_client: Any, mint: Any, api: Any, auth: Any, stream_events: Any
) -> None:
    sid = new_sid()
    agent = mint("analyst", sid=sid)
    operator = mint("operator", sub="ops-bob", sid=None)
    async with mcp_client(agent) as client:
        for _ in range(4):
            await client.call_tool(SEARCH, {"query": "mining", "limit": 1})

    status = await api.get(f"/v1/sessions/{sid}", headers=auth(operator))
    assert status.status_code == 200
    assert status.json()["circuit_breaker"]["open"] is True
    assert status.json()["circuit_breaker"]["trip"]["tool"] == SEARCH

    denied = await api.post(f"/v1/sessions/{sid}/circuit-breaker/reset", headers=auth(agent))
    assert denied.status_code == 403
    reset = await api.post(f"/v1/sessions/{sid}/circuit-breaker/reset", headers=auth(operator))
    assert reset.status_code == 200
    assert reset.json()["circuit_breaker"]["open"] is False

    async with mcp_client(agent) as client:
        assert not (await client.call_tool(SEARCH, {"query": "mining", "limit": 1})).is_error
    resets = [e for e in await stream_events() if e["type"] == "circuit_breaker.reset"]
    assert any(e["session_id"] == sid and e["data"]["reset_by"] == "ops-bob" for e in resets)
