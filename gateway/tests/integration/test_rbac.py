"""(a) RBAC: tool calls a role is not granted are denied before anything reaches upstream."""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import httpx2
import pytest
from mcp import MCPError
from mcp_types import INVALID_PARAMS

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

SEARCH = "crm-ops__search_accounts"
METRICS = "crm-ops__get_account_metrics"
UPDATE = "crm-ops__update_credit_limit"
UPDATE_ARGS = {"account_id": "ACC-1001", "new_limit_usd": 1, "reason": "attempted by an analyst"}


async def test_tools_list_is_filtered_by_role(mcp_client: Any, mint: Any) -> None:
    async with mcp_client(mint("analyst")) as client:
        analyst_tools = {tool.name for tool in (await client.list_tools()).tools}
    assert analyst_tools == {SEARCH, METRICS}

    async with mcp_client(mint("admin")) as client:
        admin_tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(admin_tools) == {SEARCH, METRICS, UPDATE}
    update = admin_tools[UPDATE]
    assert update.annotations is not None
    assert (update.annotations.read_only_hint, update.annotations.destructive_hint) == (False, True)
    assert "human approval" in (update.description or "")
    assert update.input_schema["additionalProperties"] is False  # closed, as enforced


async def test_analyst_cannot_call_a_write_tool(
    mcp_client: Any, mint: Any, crm_ops: Any, gateway_db: Any, telemetry: Any
) -> None:
    limit_before = crm_ops.store.credit_limit("acme", "ACC-1001")
    async with mcp_client(mint("analyst", sub="analyst-rbac")) as client:
        with pytest.raises(MCPError) as excinfo:
            await client.call_tool(UPDATE, UPDATE_ARGS)

    assert excinfo.value.error.code == INVALID_PARAMS
    assert excinfo.value.error.message == f"Unknown tool: {UPDATE}"
    # Nothing happened downstream: no upstream call, no approval request.
    assert crm_ops.store.credit_limit("acme", "ACC-1001") == limit_before
    pending = await gateway_db.fetchval(
        "SELECT count(*) FROM approval_requests WHERE requester_subject = 'analyst-rbac'"
    )
    assert pending == 0
    # The denial is observable: a metric data point and a decision span.
    denials = telemetry.metric_points("mcp_gateway.policy.denials")
    assert any(
        attrs.get("mcp_gateway.policy.deny_reason") == "no_matching_grant"
        and attrs.get("gen_ai.tool.name") == UPDATE
        for attrs, _ in denials
    )
    assert any(
        span.name == "gateway.policy.check"
        and (span.attributes or {}).get("mcp_gateway.policy.decision") == "deny"
        for span in telemetry.finished_spans()
    )


async def test_denied_and_nonexistent_tools_look_the_same(mcp_client: Any, mint: Any) -> None:
    """Agents cannot probe which tools exist behind the gateway."""
    errors = []
    async with mcp_client(mint("analyst")) as client:
        for name in (UPDATE, "crm-ops__export_all_accounts", "not_even_qualified"):
            with pytest.raises(MCPError) as excinfo:
                await client.call_tool(name, {})
            errors.append(
                (excinfo.value.error.code, excinfo.value.error.message.replace(name, "?"))
            )
    assert len(set(errors)) == 1


async def test_human_and_unknown_roles_have_no_tools(mcp_client: Any, mint: Any) -> None:
    for role in ("operator", "auditor", "intern"):
        async with mcp_client(mint(role)) as client:
            assert (await client.list_tools()).tools == []
            with pytest.raises(MCPError):
                await client.call_tool(SEARCH, {"query": "a"})


async def test_tenants_only_see_their_own_accounts(mcp_client: Any, mint: Any) -> None:
    async with mcp_client(mint("analyst", tenant="acme")) as client:
        acme = await client.call_tool(SEARCH, {"query": "a", "limit": 25})
    async with mcp_client(mint("analyst", tenant="zenith")) as client:
        zenith = await client.call_tool(SEARCH, {"query": "a", "limit": 25})
        foreign = await client.call_tool(METRICS, {"account_id": "ACC-1001"})

    def account_ids(result: Any) -> set[str]:
        return {account["account_id"] for account in result.structured_content["accounts"]}

    assert account_ids(acme)
    assert all(i.startswith("ACC-1") for i in account_ids(acme))
    assert account_ids(zenith)
    assert all(i.startswith("ACC-2") for i in account_ids(zenith))
    assert foreign.is_error  # another tenant's account is indistinguishable from none


def _unsigned_token(**claims: Any) -> str:
    def part(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'none', 'typ': 'JWT'})}.{part(claims)}."


@pytest.mark.parametrize(
    ("case", "expected_status"),
    [
        ("missing", 401),
        ("garbage", 401),
        ("bad_signature", 401),
        ("expired", 401),
        ("wrong_audience", 401),
        ("wrong_issuer", 401),
        ("alg_none", 401),
        ("missing_tenant", 401),
        ("no_session", 403),
    ],
)
async def test_mcp_endpoint_rejects_invalid_tokens(
    api: httpx2.AsyncClient, mint: Any, case: str, expected_status: int
) -> None:
    now = int(time.time())
    tokens: dict[str, str | None] = {
        "missing": None,
        "garbage": "not-a-jwt",
        "bad_signature": mint("analyst", secret="another-secret-" + "x" * 40),
        "expired": mint("analyst", ttl=-300),
        "wrong_audience": mint("analyst", aud="some-other-service"),
        "wrong_issuer": mint("analyst", iss="https://evil.invalid/"),
        "alg_none": _unsigned_token(
            sub="x",
            role="admin",
            tenant_id="acme",
            sid="s1",
            aud="mcp-gateway",
            iat=now,
            exp=now + 60,
        ),
        "missing_tenant": mint("analyst", tenant_id=None),
        "no_session": mint("analyst", sid=None),
    }
    token = tokens[case]
    headers = {"Accept": "application/json, text/event-stream"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    response = await api.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=headers
    )
    assert response.status_code == expected_status
    assert response.headers["content-type"].startswith("application/problem+json")
    if expected_status == 401:
        assert response.headers["www-authenticate"].startswith('Bearer error="invalid_token"')
