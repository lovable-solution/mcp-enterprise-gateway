"""crm-ops in-process: gateway assertions, tenant scoping, tool contracts, credit policy."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import httpx2
import pytest
from jose import jwt
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp_types import CallToolResult, TextContent
from pydantic import SecretStr

from crm_ops.models import AccountStatus, Segment
from crm_ops.server import CrmOpsApp, create_app
from crm_ops.settings import CrmOpsSettings
from crm_ops.store import Account, CrmStore

pytestmark = pytest.mark.anyio

KEY = "assertion-" + "k" * 40
BASE_URL = "http://localhost:8001"


def text(result: CallToolResult) -> str:
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


def assertion(*, key: str = KEY, ttl: int = 60, **overrides: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": "mcp-gateway",
        "aud": "crm-ops",
        "sub": "agent-42",
        "tenant_id": "acme",
        "sid": "s-1",
        "role": "admin",
        "iat": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
    } | overrides
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm="HS256")


@pytest.fixture
async def crm() -> AsyncIterator[CrmOpsApp]:
    app = create_app(CrmOpsSettings(gateway_assertion_key=SecretStr(KEY)))
    starlette = app.asgi._app  # type: ignore[attr-defined]  # the wrapped Starlette app
    async with starlette.router.lifespan_context(starlette):
        yield app


Connect = Callable[..., AbstractAsyncContextManager[Client]]


@pytest.fixture
def connect(crm: CrmOpsApp) -> Connect:
    @asynccontextmanager
    async def _connect(**claims: Any) -> AsyncIterator[Client]:
        http = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=crm.asgi),
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {assertion(**claims)}"},
        )
        async with (
            http,
            Client(streamable_http_client(f"{BASE_URL}/mcp", http_client=http)) as client,
        ):
            yield client

    return _connect


@pytest.mark.parametrize(
    "token",
    [
        None,
        assertion(key="another-key-" + "x" * 40),
        assertion(aud="billing"),
        assertion(iss="someone-else"),
        assertion(ttl=-120),
        assertion(jti=None),
    ],
    ids=["missing", "forged", "wrong-audience", "wrong-issuer", "expired", "no-jti"],
)
async def test_mcp_requires_a_valid_gateway_assertion(crm: CrmOpsApp, token: str | None) -> None:
    headers = {"Accept": "application/json, text/event-stream"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=crm.asgi), base_url=BASE_URL
    ) as http:
        response = await http.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=headers
        )
        health = await http.get("/healthz")
    assert response.status_code == 401
    assert health.status_code == 200  # probes need no assertion


async def test_tools_publish_contracts(connect: Connect) -> None:
    async with connect() as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(tools) == {"search_accounts", "get_account_metrics", "update_credit_limit"}
    for name in ("search_accounts", "get_account_metrics"):
        hints = tools[name].annotations
        assert hints is not None
        assert hints.read_only_hint is True
    update = tools["update_credit_limit"]
    assert update.annotations is not None
    assert update.annotations.destructive_hint is True
    assert set(update.input_schema["required"]) == {"account_id", "new_limit_usd", "reason"}
    assert all(tool.output_schema for tool in tools.values())


async def test_search_and_metrics_are_tenant_scoped(connect: Connect) -> None:
    async with connect(tenant_id="zenith") as client:
        found = await client.call_tool("search_accounts", {"query": "a", "limit": 25})
        foreign = await client.call_tool("get_account_metrics", {"account_id": "ACC-1001"})
    ids = {a["account_id"] for a in found.structured_content["accounts"]}
    assert ids == {"ACC-2001", "ACC-2002", "ACC-2003"}
    assert foreign.is_error
    assert "no account ACC-1001" in text(foreign)


async def test_metrics_are_derived_from_receivables(connect: Connect) -> None:
    async with connect() as client:
        metrics = (
            await client.call_tool("get_account_metrics", {"account_id": "ACC-1001"})
        ).structured_content
    ageing = metrics["ageing"]
    assert metrics["open_receivables_usd"] == sum(ageing.values()) == 490_000
    assert metrics["credit_utilisation"] == pytest.approx(490_000 / 500_000)


async def test_credit_limit_changes_record_who_and_which_approval(
    crm: CrmOpsApp, connect: Connect
) -> None:
    async with connect(sub="automation-bot", approval_id="apr-123") as client:
        result = await client.call_tool(
            "update_credit_limit",
            {
                "account_id": "ACC-1002",
                "new_limit_usd": 275_000,
                "reason": "renewal with new financials",
            },
        )
    change = result.structured_content
    assert (change["previous_limit_usd"], change["new_limit_usd"]) == (250_000, 275_000)
    assert (change["changed_by"], change["approval_id"]) == ("automation-bot", "apr-123")
    assert crm.store.credit_limit("acme", "ACC-1002") == 275_000


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (
            {"account_id": "ACC-1004", "new_limit_usd": 100_000, "reason": "account on hold here"},
            "on hold",
        ),
        (
            {
                "account_id": "ACC-1001",
                "new_limit_usd": 6_000_000,
                "reason": "far above the ceiling",
            },
            "underwriting",
        ),
        (
            {"account_id": "1001", "new_limit_usd": 1, "reason": "malformed account id"},
            "account_id",
        ),
    ],
)
async def test_credit_policy_and_argument_validation(
    crm: CrmOpsApp, connect: Connect, arguments: dict[str, Any], message: str
) -> None:
    changes_before = len(crm.store.changes)
    async with connect() as client:
        result = await client.call_tool("update_credit_limit", arguments)
    assert result.is_error
    assert message in text(result)
    assert len(crm.store.changes) == changes_before


async def test_tenantless_identity_cannot_use_tools(connect: Connect) -> None:
    """The gateway's discovery identity (no tenant) may list tools but not call them."""
    async with connect(tenant_id=None, sub="mcp-gateway") as client:
        assert len((await client.list_tools()).tools) == 3
        result = await client.call_tool("search_accounts", {"query": "a"})
    assert result.is_error
    assert "carries no tenant" in text(result)


def test_metrics_of_an_account_without_revenue_report_zero_dso() -> None:
    account = Account(
        tenant_id="acme",
        account_id="ACC-9001",
        name="Newco",
        segment=Segment.SMB,
        industry="software",
        owner="m.chen",
        status=AccountStatus.ACTIVE,
        credit_limit_usd=10_000,
        annual_revenue_usd=0,
        ageing=(1_000, 0, 0, 0, 0),
        risk_rating="medium",
    )
    metrics = CrmStore([account]).metrics("acme", "ACC-9001")
    assert metrics.days_sales_outstanding == 0
    assert metrics.open_receivables_usd == 1_000
