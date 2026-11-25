"""Pure pieces of the runtime: settings guardrails, fingerprints, webhook signatures,
budget arithmetic, upstream failure classification and approval results."""

from __future__ import annotations

import re
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx2
import pytest
from pydantic import ValidationError

from mcp_gateway.approvals.models import Approval, ApprovalStatus
from mcp_gateway.enforcement.budget import (
    BudgetSnapshot,
    UsageReport,
    micros_to_usd,
    price_usage,
    usd_to_micros,
)
from mcp_gateway.enforcement.fingerprint import call_fingerprint
from mcp_gateway.events.webhooks import sign, verify
from mcp_gateway.pipeline import _approval_result
from mcp_gateway.policy import load_policy
from mcp_gateway.policy.models import SessionBudget
from mcp_gateway.settings import GatewaySettings
from mcp_gateway.upstream.registry import _never_reached


def gateway_settings(**overrides: Any) -> GatewaySettings:
    base: dict[str, Any] = {
        "database_password": "x",
        "upstream_signing_key": "u" * 40,
        "jwt_issuer": "https://idp.example.test/",
        "jwt_algorithms": ["HS256"],
        "jwt_secret": "s" * 40,
    }
    return GatewaySettings(**(base | overrides))


# --- settings -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"jwt_algorithms": ["HS256", "RS256"]}, "must not mix HMAC and asymmetric"),
        ({"jwt_secret": "short"}, "at least 32 characters"),
        ({"jwt_algorithms": ["RS256"], "jwt_secret": None}, "need GATEWAY_JWT_JWKS_URL"),
        ({"webhook_url": "https://hooks.example.test/x"}, "needs GATEWAY_WEBHOOK_HMAC_KEY"),
        ({"upstream_signing_key": "short"}, "at least 32 characters"),
        ({"upstreams": {"CRM_OPS": "http://crm:8001/mcp"}}, "not a valid server name"),
        ({"upstreams": {"crm-ops": "crm:8001"}}, "absolute http(s) URL"),
        ({"jwt_jwks_url": "ftp://idp/jwks", "jwt_algorithms": ["RS256"]}, "absolute http(s) URL"),
    ],
)
def test_settings_refuse_weak_configurations(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=re.escape(message)):
        gateway_settings(**overrides)


def test_secrets_never_appear_in_reprs() -> None:
    rendered = repr(gateway_settings())
    assert "s" * 40 not in rendered
    assert "u" * 40 not in rendered


# --- fingerprints ------------------------------------------------------------------------


def test_fingerprints_ignore_key_order_and_integral_floats() -> None:
    a = call_fingerprint("t", {"q": "x", "n": 10, "nested": {"b": [1, 2.0], "a": None}})
    b = call_fingerprint("t", {"nested": {"a": None, "b": [1.0, 2]}, "n": 10.0, "q": "x"})
    assert a == b


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (("t", {"q": "x"}), ("t", {"q": "x "})),
        (("t", {"q": "x"}), ("u", {"q": "x"})),
        (("t", {"n": 1.5}), ("t", {"n": 1})),
        (("t", {"v": [1, 2]}), ("t", {"v": [2, 1]})),
    ],
)
def test_fingerprints_distinguish_different_calls(
    left: tuple[str, dict[str, Any]], right: tuple[str, dict[str, Any]]
) -> None:
    assert call_fingerprint(*left) != call_fingerprint(*right)


# --- webhook signatures -----------------------------------------------------------------------


def test_webhook_signatures_verify_and_detect_tampering() -> None:
    key, body = b"k" * 40, b'{"type":"approval.requested"}'
    header = sign(key, int(time.time()), body)
    assert verify(key, header, body)
    assert not verify(key, header, body + b" ")
    assert not verify(b"x" * 40, header, body)
    assert not verify(key, sign(key, int(time.time()) - 3600, body), body)  # stale: replay
    assert not verify(key, "garbage", body)


# --- budget arithmetic ------------------------------------------------------------------------


def test_money_conversions_round_up() -> None:
    assert usd_to_micros(Decimal("0.0000001")) == 1  # never under-count
    assert usd_to_micros(Decimal("2.000001")) == 2_000_001
    assert micros_to_usd(2_500_000) == Decimal("2.500000")


def test_usage_is_priced_per_token_type(shipped_policy_path: Any) -> None:
    pricing = load_policy(shipped_policy_path).document.pricing
    priced = price_usage(
        UsageReport(
            model="claude-opus-5-5",
            input_tokens=1_000_000,
            output_tokens=100_000,
            cache_creation_input_tokens=200_000,
            cache_read_input_tokens=3_000_000,
        ),
        pricing,
    )
    # 4.00 + 2.00 + 200k x $5/M (5-minute writes) + 3M x $0.20/M
    assert priced.cost_usd == Decimal("4") + Decimal("2") + Decimal("1") + Decimal("0.6")
    assert not priced.priced_at_fallback


def test_snapshot_reports_the_first_exhausted_limit() -> None:
    limits = SessionBudget(max_tool_calls=5, max_total_tokens=1000, max_cost_usd=Decimal("1.00"))
    fresh = BudgetSnapshot.from_ledger({}, limits)
    assert fresh.exhausted_limit is None
    assert fresh.utilisation == 0
    by_cost = BudgetSnapshot.from_ledger({"cost_micros": "1000000", "tool_calls": "2"}, limits)
    assert by_cost.exhausted_limit == "max_cost_usd"
    by_calls = BudgetSnapshot.from_ledger({"tool_calls": "5", "cost_micros": "1000000"}, limits)
    assert by_calls.exhausted_limit == "max_tool_calls"
    assert by_calls.utilisation == 1.0


# --- upstream failures -------------------------------------------------------------------
def _caused_by(cause: BaseException) -> BaseException:
    try:
        try:
            raise cause
        except BaseException as inner:
            raise RuntimeError("transport failed") from inner
    except RuntimeError as outer:
        return outer


@pytest.mark.parametrize(
    ("exc", "never_reached"),
    [
        (httpx2.ConnectError("connection refused"), True),
        (httpx2.ConnectTimeout("connect timed out"), True),
        (_caused_by(httpx2.ConnectError("refused")), True),
        (ExceptionGroup("task group", [httpx2.ConnectError("refused")]), True),
        (ExceptionGroup("outer", [ExceptionGroup("inner", [httpx2.ConnectTimeout("t")])]), True),
        (httpx2.ReadTimeout("no answer"), False),  # the request was sent: it may have run
        (httpx2.RemoteProtocolError("connection dropped"), False),
        (ExceptionGroup("task group", [httpx2.ReadError("reset")]), False),
        (RuntimeError("anything else"), False),
    ],
)
def test_only_calls_that_never_reached_the_upstream_count_as_not_executed(
    exc: BaseException, never_reached: bool
) -> None:
    assert _never_reached(exc) is never_reached


# --- approval results --------------------------------------------------------------------
def _approval(status: ApprovalStatus, **fields: Any) -> Approval:
    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "tenant_id": "acme",
        "session_id": "s-1",
        "requester_subject": "agent-7",
        "requester_role": "admin",
        "tool_name": "crm-ops__update_credit_limit",
        "arguments": {"account_id": "ACC-1001", "new_limit_usd": 1},
        "arguments_digest": "d",
        "risk": "high",
        "approver_roles": ("operator",),
        "status": status,
        "policy_name": "p",
        "policy_digest": "x",
        "policy_rule": "r",
        "trace_id": None,
        "created_at": now,
        "expires_at": now + timedelta(minutes=30),
    }
    return Approval(**(base | fields))


@pytest.mark.parametrize("status", [ApprovalStatus.APPROVED, ApprovalStatus.EXECUTING])
def test_an_approved_call_in_flight_is_reported_as_running_not_awaiting_review(
    status: ApprovalStatus,
) -> None:
    result, _ = _approval_result(_approval(status))
    meta = (result.meta or {})["mcp-enterprise-gateway/governance"]
    text = result.content[0].text  # type: ignore[union-attr]
    assert (meta["code"], meta["status"]) == ("PENDING_APPROVAL", status.value)
    assert "approved and is being executed" in text
    assert "Do not retry" in text
    assert "held for human approval" not in text


def test_a_failed_execution_reports_the_error() -> None:
    result, _ = _approval_result(
        _approval(ApprovalStatus.FAILED, error="crm-ops unreachable", decided_by_subject="ops")
    )
    meta = (result.meta or {})["mcp-enterprise-gateway/governance"]
    assert meta["code"] == "APPROVAL_FAILED"
    assert meta["error"] == "crm-ops unreachable"
