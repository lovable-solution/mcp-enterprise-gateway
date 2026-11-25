"""Per-session hard caps: tool calls, tokens and USD (model usage + metered tools)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

SEARCH = "crm-ops__search_accounts"
METRICS = "crm-ops__get_account_metrics"


def governance(result: Any) -> dict[str, Any]:
    meta: dict[str, Any] = (result.meta or {}).get("mcp-enterprise-gateway/governance", {})
    return meta


def new_sid() -> str:
    return f"s-{uuid.uuid4().hex[:12]}"


async def report(api: Any, token: str, sid: str, **usage: Any) -> Any:
    return await api.post(
        f"/v1/sessions/{sid}/usage", json=usage, headers={"Authorization": f"Bearer {token}"}
    )


async def test_tool_call_cap_is_hard(mcp_client: Any, mint: Any, stream_events: Any) -> None:
    sid = new_sid()
    async with mcp_client(mint("capped", sid=sid)) as client:  # max_tool_calls: 2
        assert not (await client.call_tool(SEARCH, {"query": "alder"})).is_error
        assert not (await client.call_tool(SEARCH, {"query": "birch"})).is_error
        refused = await client.call_tool(SEARCH, {"query": "cedar"})
        refused_again = await client.call_tool(SEARCH, {"query": "dunmore"})
    assert governance(refused)["code"] == "BUDGET_EXCEEDED"
    assert governance(refused)["limit"] == "max_tool_calls"
    assert governance(refused)["budget"]["tool_calls"] == 2  # refused calls cost nothing
    assert governance(refused_again)["code"] == "BUDGET_EXCEEDED"
    exhausted = [
        e
        for e in await stream_events()
        if e["type"] == "budget.exhausted" and e["session_id"] == sid
    ]
    assert len(exhausted) == 1  # announced once, not on every refusal


async def test_reported_model_usage_counts_against_the_dollar_cap(
    mcp_client: Any, mint: Any, api: Any
) -> None:
    sid = new_sid()
    agent = mint("analyst", sid=sid)  # analyst budget: $2.00
    accepted = await report(
        api, agent, sid, model="claude-opus-5-5", input_tokens=250_000, output_tokens=50_000
    )
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert Decimal(body["cost_usd"]) == Decimal("2.00")  # 250k x $4/M + 50k x $20/M
    assert body["budget"]["exhausted_limit"] == "max_cost_usd"
    async with mcp_client(agent) as client:
        refused = await client.call_tool(SEARCH, {"query": "alder"})
    assert governance(refused)["code"] == "BUDGET_EXCEEDED"
    assert governance(refused)["limit"] == "max_cost_usd"


async def test_every_token_type_is_priced_exactly(mint: Any, api: Any) -> None:
    sid = new_sid()
    body = (
        await report(
            api,
            mint("analyst", sid=sid),
            sid,
            model="claude-sonnet-5-5",
            input_tokens=1_234,
            output_tokens=567,
            cache_creation_input_tokens=1_000,
            cache_read_input_tokens=20_000,
            cache_ttl="1h",
        )
    ).json()
    # (1234 x 2 + 567 x 10 + 1000 x 4 [1h cache write] + 20000 x 0.10) / 1M
    assert Decimal(body["cost_usd"]) == Decimal("0.014138")
    assert body["priced_at_fallback"] is False
    assert body["budget"]["total_tokens"] == 22_801


async def test_unpriced_models_are_charged_the_conservative_fallback(mint: Any, api: Any) -> None:
    sid = new_sid()
    body = (
        await report(
            api,
            mint("analyst", sid=sid),
            sid,
            model="brand-new-model",
            input_tokens=1_000,
            output_tokens=0,
        )
    ).json()
    assert body["priced_at_fallback"] is True
    assert Decimal(body["cost_usd"]) == Decimal("0.01")  # fallback $10 / M input


async def test_metered_tools_charge_their_cost(
    mcp_client: Any, mint: Any, api: Any, auth: Any
) -> None:
    sid = new_sid()
    agent = mint("analyst", sid=sid)
    async with mcp_client(agent) as client:
        for account in ("ACC-1001", "ACC-1002"):
            assert not (await client.call_tool(METRICS, {"account_id": account})).is_error
    own = (await api.get(f"/v1/sessions/{sid}", headers=auth(agent))).json()
    assert own["budget"]["tool_calls"] == 2
    assert Decimal(own["budget"]["tool_cost_usd"]) == Decimal("0.004")  # 2 x $0.002


async def test_usage_reports_are_scoped_and_validated(mint: Any, api: Any) -> None:
    sid = new_sid()
    agent = mint("analyst", sid=sid)
    foreign = await report(
        api, agent, new_sid(), model="claude-opus-5-5", input_tokens=1, output_tokens=1
    )
    assert foreign.status_code == 403
    negative = await report(
        api, agent, sid, model="claude-opus-5-5", input_tokens=-5, output_tokens=0
    )
    assert negative.status_code == 422
    unknown_field = await report(
        api, agent, sid, model="claude-opus-5-5", input_tokens=1, output_tokens=1, discount=1
    )
    assert unknown_field.status_code == 422


async def test_budget_warning_is_emitted_once(mint: Any, api: Any, stream_events: Any) -> None:
    sid = new_sid()
    agent = mint("analyst", sid=sid)  # $2.00 cap, warn at 80 %
    await report(
        api, agent, sid, model="claude-opus-5-5", input_tokens=425_000, output_tokens=0
    )  # $1.70
    await report(
        api, agent, sid, model="claude-opus-5-5", input_tokens=12_500, output_tokens=0
    )  # +$0.05
    events = [e for e in await stream_events() if e["session_id"] == sid]
    assert [e["type"] for e in events].count("budget.warning") == 1
    assert [e["type"] for e in events].count("session.activity") == 2
