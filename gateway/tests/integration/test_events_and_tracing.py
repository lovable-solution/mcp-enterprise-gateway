"""Operator event stream (SSE) and end-to-end distributed tracing."""

from __future__ import annotations

import json
from typing import Any

import anyio
import httpx2
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

SEARCH = "crm-ops__search_accounts"


async def read_sse(
    gateway_url: str,
    token: str,
    *,
    count: int,
    last_event_id: str | None = None,
    timeout: float = 10,
) -> list[dict[str, str]]:
    """Read ``count`` events (comments and keep-alives skipped) from /v1/events."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}
    if last_event_id is not None:
        headers["Last-Event-ID"] = last_event_id
    events: list[dict[str, str]] = []
    current: dict[str, str] = {}
    with anyio.fail_after(timeout):
        async with (
            httpx2.AsyncClient(base_url=gateway_url, timeout=None) as client,
            client.stream("GET", "/v1/events", headers=headers) as response,
        ):
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if not line:
                    if "event" in current:
                        events.append(current)
                        if len(events) == count:
                            return events
                    current = {}
                elif not line.startswith(":") and ":" in line:
                    field, _, value = line.partition(":")
                    current[field] = value.strip()
    return events


async def test_sse_streams_live_events_and_resumes_from_last_event_id(
    gateway: Any, mcp_client: Any, mint: Any
) -> None:
    operator = mint("operator", sid=None)
    received: list[dict[str, str]] = []

    async with anyio.create_task_group() as tasks:

        async def listen() -> None:
            received.extend(await read_sse(gateway.url, operator, count=2))

        tasks.start_soon(listen)
        await anyio.sleep(0.5)  # subscribed before the activity happens
        async with mcp_client(mint("analyst", sub="sse-agent")) as client:
            await client.call_tool(SEARCH, {"query": "alder"})
            await client.call_tool(SEARCH, {"query": "birch"})

    assert [e["event"] for e in received] == ["session.activity", "session.activity"]
    first, second = received
    assert json.loads(first["data"])["data"]["subject"] == "sse-agent"
    # Reconnecting with Last-Event-ID resumes right after it: nothing lost, nothing repeated.
    resumed = await read_sse(gateway.url, operator, count=1, last_event_id=first["id"])
    assert resumed[0]["id"] == second["id"]


async def test_events_need_a_control_plane_role(gateway: Any, mint: Any) -> None:
    async with httpx2.AsyncClient(base_url=gateway.url) as client:
        response = await client.get(
            "/v1/events", headers={"Authorization": f"Bearer {mint('analyst')}"}
        )
    assert response.status_code == 403


async def test_one_trace_spans_agent_gateway_and_mcp_server(
    mcp_client: Any, mint: Any, telemetry: Any
) -> None:
    async with mcp_client(mint("analyst", sub="traced-agent")) as client:
        result = await client.call_tool(SEARCH, {"query": "cedar"})
    assert not result.is_error

    spans = telemetry.finished_spans()
    gateway_span = next(
        s
        for s in reversed(spans)
        if s.name == f"tools/call {SEARCH}"
        and (s.attributes or {}).get("enduser.id") == "traced-agent"
    )
    trace_id = gateway_span.context.trace_id
    in_trace = {s.name: s for s in spans if s.context.trace_id == trace_id}

    # agent CLIENT span -> gateway SERVER span -> enforcement spans -> upstream CLIENT span
    # -> crm-ops SERVER span: one trace, propagated through MCP _meta.
    assert f"MCP send tools/call {SEARCH}" in in_trace  # the agent's own client span
    for step in (
        "gateway.circuit_breaker.check",
        "gateway.policy.check",
        "gateway.budget.charge",
        "gateway.upstream.call",
    ):
        assert step in in_trace, f"missing {step}"
    assert "MCP send tools/call search_accounts" in in_trace  # gateway -> crm-ops
    upstream_server = in_trace["tools/call search_accounts"]  # crm-ops' server span
    assert (upstream_server.attributes or {}).get("crm_ops.tenant.id") == "acme"

    attributes = gateway_span.attributes or {}
    assert attributes["mcp_gateway.tenant.id"] == "acme"
    assert attributes["mcp_gateway.policy.decision"] == "allow"
    assert attributes["mcp_gateway.outcome"] == "succeeded"
    assert attributes["gen_ai.tool.name"] == SEARCH
