"""Failure handling against the real stack: the event hub under reconnects and slow
consumers, budget refunds, and an upstream server that disappears mid-session."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from decimal import Decimal
from typing import Any

import anyio
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import SecretStr
from redis.asyncio import Redis

from crm_ops.server import create_app as create_crm_ops
from crm_ops.settings import CrmOpsSettings
from mcp_gateway.app import create_app as create_gateway
from mcp_gateway.enforcement.budget import BudgetLedger
from mcp_gateway.enforcement.keys import RedisKeys
from mcp_gateway.events.bus import EventBus, GatewayEvent
from mcp_gateway.events.hub import EventHub, SubscriptionLaggedError
from mcp_gateway.policy.models import SessionBudget

from .conftest import ASSERTION_KEY, Gateway, ServerThread, Stack

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

TENANT = "acme"


@pytest.fixture
async def redis(stack: Stack) -> AsyncIterator[Redis]:
    client = Redis(
        port=stack.redis_port, password=stack.env["REDIS_PASSWORD"], decode_responses=True
    )
    yield client
    await client.aclose()


@pytest.fixture
async def keys(redis: Redis) -> AsyncIterator[RedisKeys]:
    prefix = f"mcpgw-fail-{uuid.uuid4().hex[:8]}"
    yield RedisKeys(prefix)
    async for key in redis.scan_iter(match=f"{prefix}:*", count=500):
        await redis.delete(key)


@pytest.fixture
def bus(redis: Redis, keys: RedisKeys) -> EventBus:
    return EventBus(redis, keys, maxlen=1_000)


async def publish(bus: EventBus, count: int, start: int = 0) -> list[GatewayEvent]:
    return [
        await bus.publish(
            GatewayEvent(type="session.activity", tenant_id=TENANT, data={"n": start + n})
        )
        for n in range(count)
    ]


async def drain(feed: Any, count: int) -> list[int]:
    numbers: list[int] = []
    for _ in range(count):
        event = await feed.next(timeout=5)
        assert event is not None, f"only {len(numbers)} of {count} events arrived"
        numbers.append(event.data["n"])
    return numbers


# --- event hub ---------------------------------------------------------------------------
async def test_every_subscriber_gets_live_events_through_one_reader(bus: EventBus) -> None:
    hub = EventHub(bus, block_ms=200)
    async with hub.subscribe(TENANT) as first, hub.subscribe(TENANT) as second:
        assert len(hub._readers) == 1
        await publish(bus, 3)
        assert await drain(first, 3) == [0, 1, 2]
        assert await drain(second, 3) == [0, 1, 2]
        assert await first.next(timeout=0.3) is None  # nothing more, no duplicates
    assert hub.subscriber_count(TENANT) == 0
    assert TENANT not in hub._readers  # the last subscriber stopped the reader
    await hub.aclose()


async def test_resuming_pages_through_a_long_backlog_then_goes_live(bus: EventBus) -> None:
    hub = EventHub(bus, block_ms=200, backlog_page_size=4)
    published = await publish(bus, 11)
    resume_after = published[2].id
    async with hub.subscribe(TENANT, after=resume_after) as feed:
        await publish(bus, 2, start=11)  # arrive while the backlog is still being paged
        assert await drain(feed, 10) == list(range(3, 13))  # no gaps, no duplicates
        assert await feed.next(timeout=0.3) is None
    await hub.aclose()


async def test_a_resume_point_in_the_future_means_from_now(bus: EventBus) -> None:
    hub = EventHub(bus, block_ms=200)
    await publish(bus, 2)
    async with hub.subscribe(TENANT, after="99999999999999-0") as feed:
        await publish(bus, 1, start=2)
        assert await drain(feed, 1) == [2]
    await hub.aclose()


async def test_a_slow_subscriber_is_dropped_without_affecting_others(bus: EventBus) -> None:
    hub = EventHub(bus, block_ms=200, queue_size=3)
    async with hub.subscribe(TENANT) as slow, hub.subscribe(TENANT) as fast:
        received: list[int] = []

        async def consume() -> None:
            received.extend(await drain(fast, 10))

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(consume)
            for n in range(10):
                await publish(bus, 1, start=n)
                await anyio.sleep(0.02)  # the fast consumer keeps up; the slow one never reads
        assert received == list(range(10))
        with pytest.raises(SubscriptionLaggedError):
            await slow.next(timeout=1)
    await hub.aclose()


# --- budget refunds ----------------------------------------------------------------------
async def test_refunds_undo_a_charge_and_never_go_negative(redis: Redis, keys: RedisKeys) -> None:
    ledger = BudgetLedger(redis, keys)
    limits = SessionBudget(max_tool_calls=10, max_total_tokens=1_000, max_cost_usd=Decimal(1))
    cost = Decimal("0.002")
    for _ in range(2):
        await ledger.charge_tool_call(
            tenant_id=TENANT, session_id="s-refund", limits=limits, cost_usd=cost, ttl_seconds=60
        )
    await ledger.refund_tool_call(tenant_id=TENANT, session_id="s-refund", cost_usd=cost)
    snapshot = await ledger.snapshot(TENANT, "s-refund", limits)
    assert (snapshot.tool_calls, snapshot.cost_usd, snapshot.tool_cost_usd) == (1, cost, cost)
    for _ in range(3):
        await ledger.refund_tool_call(tenant_id=TENANT, session_id="s-refund", cost_usd=cost)
    snapshot = await ledger.snapshot(TENANT, "s-refund", limits)
    assert (snapshot.tool_calls, snapshot.cost_usd) == (0, Decimal(0))


# --- an upstream that disappears ---------------------------------------------------------
@pytest.fixture
def doomed_crm_ops() -> Iterator[ServerThread]:
    app = create_crm_ops(CrmOpsSettings(gateway_assertion_key=SecretStr(ASSERTION_KEY)))
    server = ServerThread(app.asgi)
    server.start()
    yield server
    server.stop()


@pytest.fixture
def lone_gateway(
    gateway: Gateway, doomed_crm_ops: ServerThread, stack: Stack
) -> Iterator[ServerThread]:
    """A second gateway replica whose only upstream is the doomed crm-ops."""
    prefix = f"mcpgw-lone-{uuid.uuid4().hex[:8]}"
    settings = gateway.settings.model_copy(
        update={"upstreams": {"crm-ops": f"{doomed_crm_ops.url}/mcp"}, "redis_key_prefix": prefix}
    )
    server = ServerThread(create_gateway(settings))
    server.start()
    yield server
    server.stop()

    async def cleanup() -> None:
        client = Redis(port=stack.redis_port, password=stack.env["REDIS_PASSWORD"])
        async for key in client.scan_iter(match=f"{prefix}:*", count=500):
            await client.delete(key)
        await client.aclose()

    asyncio.run(cleanup())


async def test_an_unreachable_upstream_is_reported_and_the_call_refunded(
    lone_gateway: ServerThread, doomed_crm_ops: ServerThread, mint: Any
) -> None:
    sid = f"s-{uuid.uuid4().hex[:12]}"
    token = mint("analyst", sid=sid)
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx2.AsyncClient(headers=headers, timeout=30.0) as http:
        transport = streamable_http_client(f"{lone_gateway.url}/mcp", http_client=http)
        async with Client(transport, read_timeout_seconds=30.0, cache=None) as client:
            ok = await client.call_tool("crm-ops__search_accounts", {"query": "alder"})
            assert not ok.is_error

            doomed_crm_ops.stop()
            failed = await client.call_tool("crm-ops__search_accounts", {"query": "birch"})

        session = await http.get(f"{lone_gateway.url}/v1/sessions/{sid}")
    governance = (failed.meta or {})["mcp-enterprise-gateway/governance"]
    assert failed.is_error
    assert governance["code"] == "UPSTREAM_UNAVAILABLE"
    assert "did not run" in failed.content[0].text  # type: ignore[union-attr]
    assert session.status_code == 200, session.text
    assert session.json()["budget"]["tool_calls"] == 1  # the failed call was refunded
