"""RBAC decisions, on the shipped policy and on targeted synthetic policies."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from mcp_gateway.policy import ControlAction, DenyReason, PolicyDocument, PolicyEngine

SEARCH = "crm-ops__search_accounts"
METRICS = "crm-ops__get_account_metrics"
UPDATE_LIMIT = "crm-ops__update_credit_limit"


# --- Shipped policy: analyst can read, admin can write (behind approval) --------------


@pytest.mark.parametrize("tool", [SEARCH, METRICS])
def test_analyst_can_call_read_tools(engine: PolicyEngine, tool: str) -> None:
    decision = engine.check_tool("analyst", tool)
    assert decision.allowed
    assert decision.rule == "roles.analyst.allow[0]"
    assert not decision.requires_approval


def test_analyst_cannot_call_write_tools(engine: PolicyEngine) -> None:
    decision = engine.check_tool("analyst", UPDATE_LIMIT)
    assert not decision.allowed
    assert decision.reason is DenyReason.NO_MATCHING_GRANT


def test_admin_write_calls_are_routed_to_human_approval(engine: PolicyEngine) -> None:
    decision = engine.check_tool("admin", UPDATE_LIMIT)
    assert decision.allowed
    assert decision.rule == "roles.admin.allow[0]"
    assert decision.requires_approval
    assert decision.approver_roles == ("operator",)
    assert decision.approval_timeout_seconds == 1800  # per-tool override of the 900s default


def test_admin_inherits_analyst_read_grants(engine: PolicyEngine) -> None:
    assert engine.check_tool("admin", SEARCH).rule == "roles.analyst.allow[0]"


@pytest.mark.parametrize("role", ["operator", "auditor"])
def test_human_roles_have_no_tool_access(engine: PolicyEngine, role: str) -> None:
    assert engine.visible_tools(role) == ()


def test_control_plane_actions(engine: PolicyEngine) -> None:
    assert engine.allows_action("operator", ControlAction.APPROVALS_DECIDE)
    assert engine.allows_action("auditor", ControlAction.APPROVALS_READ)
    assert not engine.allows_action("auditor", ControlAction.APPROVALS_DECIDE)
    assert not engine.allows_action("admin", ControlAction.APPROVALS_DECIDE)  # agents never approve
    assert not engine.allows_action("ghost", ControlAction.POLICY_READ)


def test_tools_list_is_filtered_per_role(engine: PolicyEngine) -> None:
    assert engine.visible_tools("analyst") == (SEARCH, METRICS)
    assert engine.visible_tools("admin") == (SEARCH, METRICS, UPDATE_LIMIT)
    assert engine.visible_tools("nobody") == ()


@pytest.mark.parametrize(
    ("role", "tool", "reason"),
    [
        ("ghost", SEARCH, DenyReason.UNKNOWN_ROLE),
        ("", SEARCH, DenyReason.UNKNOWN_ROLE),
        ("admin", "crm-ops__export_all", DenyReason.UNREGISTERED_TOOL),
        ("admin", "update_credit_limit", DenyReason.UNREGISTERED_TOOL),
        ("admin", "", DenyReason.UNREGISTERED_TOOL),
    ],
)
def test_unknown_roles_and_tools_are_denied(
    engine: PolicyEngine, role: str, tool: str, reason: DenyReason
) -> None:
    decision = engine.check_tool(role, tool)
    assert not decision.allowed
    assert decision.reason is reason


def test_session_budgets_apply_role_overrides(engine: PolicyEngine) -> None:
    analyst = engine.session_budget("analyst")
    assert (analyst.max_tool_calls, analyst.max_total_tokens, analyst.max_cost_usd) == (
        50,
        500_000,
        Decimal("2.00"),
    )
    admin = engine.session_budget("admin")
    assert (admin.max_tool_calls, admin.max_cost_usd) == (100, Decimal("10.00"))
    assert engine.session_budget("auditor") == engine.policy.budgets.session


def test_admin_deny_guards_tools_registered_later(shipped_data: dict[str, Any]) -> None:
    shipped_data["tools"]["crm-ops__delete_account"] = {"access": "write", "risk": "high"}
    engine = PolicyEngine(PolicyDocument.from_data(shipped_data))
    decision = engine.check_tool("admin", "crm-ops__delete_account")
    assert not decision.allowed
    assert decision.reason is DenyReason.EXPLICIT_DENY
    assert decision.rule == "roles.admin.deny[0]"


# --- Synthetic policy: precedence -----------------------------------------------------


def test_deny_beats_allow_even_when_inherited(policy_data: dict[str, Any]) -> None:
    policy_data["roles"]["child"] = {
        "description": "Explicitly re-grants what its parent denies.",
        "inherits": ["writer"],
        "allow": [{"tools": ["svc__drop_item"], "access": ["write"]}],
    }
    engine = PolicyEngine(PolicyDocument.from_data(policy_data))
    decision = engine.check_tool("child", "svc__drop_item")
    assert not decision.allowed
    assert decision.reason is DenyReason.EXPLICIT_DENY
    assert decision.rule == "roles.writer.deny[0]"


def test_grants_are_scoped_by_access_level(policy_data: dict[str, Any]) -> None:
    engine = PolicyEngine(PolicyDocument.from_data(policy_data))
    # reader's grant covers svc__get_* at read level only; writer adds svc__* at write level.
    assert engine.check_tool("reader", "svc__get_item").allowed
    assert not engine.check_tool("reader", "svc__set_item").allowed
    assert engine.check_tool("writer", "svc__set_item").requires_approval
    assert engine.check_tool("writer", "svc__set_item").approver_roles == ("approver",)
