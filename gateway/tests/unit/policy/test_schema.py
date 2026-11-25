"""Schema rules for policies.yaml: closed world, strict types, cross-references."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from mcp_gateway.policy import PolicyDocument, split_tool_name

Data = dict[str, Any]


def set_path(data: Data, path: tuple[str, ...], value: object) -> Data:
    node = data
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return data


def test_minimal_policy_is_valid(policy_data: Data) -> None:
    doc = PolicyDocument.from_data(policy_data)
    assert doc.circuit_breaker.loop_detection.max_identical_calls == 3  # defaults apply
    assert doc.circuit_breaker.loop_detection.window_seconds == 60


# --- Closed world ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        ("polices",),
        ("tools", "svc__set_item", "requires_aproval"),
        ("roles", "reader", "allows"),
        ("budgets", "session", "max_cost"),
        ("pricing", "models", "model-a", "input_per_token"),
    ],
    ids=".".join,
)
def test_unknown_fields_are_rejected(
    policy_data: Data, assert_problem: Any, path: tuple[str, ...]
) -> None:
    set_path(policy_data, path, True)
    assert_problem(policy_data, f"{'.'.join(path)}: unknown field")


def test_a_misspelt_approval_flag_cannot_silently_remove_the_gate(
    policy_data: Data, assert_problem: Any
) -> None:
    del policy_data["tools"]["svc__set_item"]["requires_approval"]
    policy_data["tools"]["svc__set_item"]["requires_aproval"] = True
    assert_problem(policy_data, "tools.svc__set_item.requires_aproval: unknown field")


# --- Strict types -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "value", "fragment"),
    [
        (("tools", "svc__set_item", "requires_approval"), "yes", "valid boolean"),
        (("tools", "svc__set_item", "requires_approval"), 1, "valid boolean"),
        (("budgets", "session", "max_tool_calls"), "10", "valid integer"),
        (("budgets", "session", "max_tool_calls"), 10.0, "valid integer"),
        (("budgets", "session", "max_tool_calls"), True, "valid integer"),
        (("version",), "1", "Input should be 1"),
        (("tools", "svc__get_item", "access"), "READ", "'read' or 'write'"),
        (("metadata", "name"), False, "valid string"),  # YAML 1.1 reads `no` as false
    ],
)
def test_values_are_never_coerced(
    policy_data: Data, assert_problem: Any, path: tuple[str, ...], value: object, fragment: str
) -> None:
    set_path(policy_data, path, value)
    assert_problem(policy_data, fragment)


# --- Tool names -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "get_item",
        "Svc__get_item",
        "my_svc__get_item",
        "svc__",
        "svc-__get_item",
        "svc__-x",
        "s.v.c__x",
    ],
)
def test_tool_names_must_be_qualified(policy_data: Data, assert_problem: Any, name: str) -> None:
    policy_data["tools"][name] = {"access": "read"}
    assert_problem(policy_data, f"tools.{name} (key): must be '<server>__<tool>'")


def test_tool_names_fit_llm_tool_name_limits(policy_data: Data, assert_problem: Any) -> None:
    name = "svc__" + "x" * 60
    policy_data["tools"][name] = {"access": "read"}
    assert_problem(policy_data, "at most 64 characters")


@pytest.mark.parametrize(
    "name", ["crm-ops__update_credit_limit", "a__b", "s3__put-object", "x1__A_b-C"]
)
def test_valid_tool_names(policy_data: Data, name: str) -> None:
    policy_data["tools"][name] = {"access": "read"}
    assert name in PolicyDocument.from_data(policy_data).tools


def test_split_tool_name() -> None:
    assert split_tool_name("crm-ops__update_credit_limit") == ("crm-ops", "update_credit_limit")
    assert split_tool_name("svc__a__b") == ("svc", "a__b")
    with pytest.raises(ValueError, match="not a qualified tool name"):
        split_tool_name("update_credit_limit")


# --- Grants ---------------------------------------------------------------------------


def test_grant_pattern_must_match_a_registered_tool(policy_data: Data, assert_problem: Any) -> None:
    policy_data["roles"]["reader"]["allow"][0]["tools"] = ["svc__list_*"]
    assert_problem(
        policy_data, "roles.reader.allow[0].tools: pattern 'svc__list_*' matches no registered tool"
    )


def test_grant_must_reach_a_tool_at_a_granted_access_level(
    policy_data: Data, assert_problem: Any
) -> None:
    policy_data["roles"]["reader"]["allow"][0]["tools"] = ["svc__set_*"]
    assert_problem(policy_data, "matches only tools whose access is not in [read]")


def test_grant_patterns_are_plain_globs(policy_data: Data, assert_problem: Any) -> None:
    policy_data["roles"]["reader"]["allow"][0]["tools"] = ["svc__[gs]et_*"]
    assert_problem(policy_data, "glob patterns may use letters, digits")


def test_deny_patterns_may_guard_unregistered_tools(policy_data: Data) -> None:
    policy_data["roles"]["writer"]["deny"].append("*__delete_*")
    PolicyDocument.from_data(policy_data)


# --- Inheritance ----------------------------------------------------------------------


def test_inheritance_parent_must_exist(policy_data: Data, assert_problem: Any) -> None:
    policy_data["roles"]["writer"]["inherits"] = ["readers"]
    assert_problem(policy_data, "roles.writer.inherits: unknown role 'readers'")


@pytest.mark.parametrize(
    ("edges", "cycle"),
    [
        ({"reader": ["writer"]}, "reader -> writer -> reader"),
        ({"reader": ["reader"]}, "reader -> reader"),
    ],
)
def test_inheritance_cycles_are_rejected(
    policy_data: Data, assert_problem: Any, edges: dict[str, list[str]], cycle: str
) -> None:
    for role, parents in edges.items():
        policy_data["roles"][role]["inherits"] = parents
    assert_problem(policy_data, f"inheritance cycle: {cycle}")


def test_list_entries_must_be_unique(policy_data: Data, assert_problem: Any) -> None:
    policy_data["approvals"]["approver_roles"] = ["approver", "approver"]
    assert_problem(policy_data, "approvals.approver_roles: duplicate entries: approver")


# --- Approvals and segregation of duties ----------------------------------------------


def test_approver_role_must_exist(policy_data: Data, assert_problem: Any) -> None:
    policy_data["approvals"]["approver_roles"] = ["approvers"]
    assert_problem(policy_data, "approvals.approver_roles: unknown role 'approvers'")


def test_approver_role_must_hold_the_decide_action(policy_data: Data, assert_problem: Any) -> None:
    policy_data["roles"]["approver"]["actions"] = ["approvals:read"]
    assert_problem(policy_data, "role 'approver' lacks the 'approvals:decide' action")


def test_per_tool_approvers_are_checked_too(policy_data: Data, assert_problem: Any) -> None:
    policy_data["tools"]["svc__set_item"]["approval"] = {"approver_roles": ["reader"]}
    assert_problem(
        policy_data,
        "tools.svc__set_item.approval.approver_roles: role 'reader' lacks the 'approvals:decide'",
    )


def test_a_role_cannot_approve_calls_it_can_make(policy_data: Data, assert_problem: Any) -> None:
    policy_data["roles"]["writer"]["actions"] = ["approvals:decide"]
    policy_data["approvals"]["approver_roles"] = ["approver", "writer"]
    assert_problem(
        policy_data,
        "tools.svc__set_item: role 'writer' can both call this tool (roles.writer.allow[0])",
    )


def test_segregation_of_duties_honours_deny_rules(policy_data: Data) -> None:
    # An approver that inherits write grants but is explicitly denied the gated tool is fine.
    policy_data["roles"]["approver"]["inherits"] = ["writer"]
    policy_data["roles"]["approver"]["deny"] = ["svc__set_item"]
    PolicyDocument.from_data(policy_data)


def test_approval_settings_require_the_approval_flag(
    policy_data: Data, assert_problem: Any
) -> None:
    policy_data["tools"]["svc__drop_item"]["approval"] = {"timeout_seconds": 600}
    assert_problem(policy_data, "tools.svc__drop_item: 'approval' settings are only valid when")


def test_critical_tools_must_require_approval(policy_data: Data, assert_problem: Any) -> None:
    policy_data["tools"]["svc__drop_item"]["risk"] = "critical"
    assert_problem(policy_data, "tools with risk 'critical' must set 'requires_approval: true'")


def test_inline_wait_must_be_shorter_than_every_timeout(
    policy_data: Data, assert_problem: Any
) -> None:
    policy_data["approvals"]["inline_wait_seconds"] = 90
    policy_data["tools"]["svc__set_item"]["approval"] = {"timeout_seconds": 60}
    assert_problem(
        policy_data,
        "approvals.inline_wait_seconds (90) must be shorter than "
        "tools.svc__set_item.approval.timeout_seconds (60)",
    )


def test_budget_ledger_must_outlive_pending_approvals(
    policy_data: Data, assert_problem: Any
) -> None:
    policy_data["tools"]["svc__set_item"]["approval"] = {"timeout_seconds": 7200}
    policy_data["budgets"]["ledger_ttl_seconds"] = 3600
    assert_problem(policy_data, "budgets.ledger_ttl_seconds (3600) must exceed the longest")


# --- Budgets and pricing -----------------------------------------------------------------


@pytest.mark.parametrize("limit", ["max_tool_calls", "max_total_tokens", "max_cost_usd"])
def test_session_limits_must_be_positive(
    policy_data: Data, assert_problem: Any, limit: str
) -> None:
    policy_data["budgets"]["session"][limit] = 0
    assert_problem(policy_data, f"budgets.session.{limit}: Input should be greater than 0")


def test_empty_role_budget_override_is_rejected(policy_data: Data, assert_problem: Any) -> None:
    policy_data["roles"]["reader"]["budget"] = {}
    assert_problem(
        policy_data, "roles.reader.budget: a budget override must set at least one limit"
    )


def test_money_is_exact(policy_data: Data) -> None:
    policy_data["pricing"]["models"]["model-a"]["cache_read_per_mtok"] = 0.1
    policy_data["roles"]["reader"]["budget"] = {"max_cost_usd": 0.3}
    doc = PolicyDocument.from_data(policy_data)
    assert doc.pricing.models["model-a"].cache_read_per_mtok == Decimal("0.1")
    reader_budget = doc.roles["reader"].budget
    assert reader_budget is not None
    assert reader_budget.max_cost_usd == Decimal("0.3")


def test_money_is_limited_to_micro_dollars(policy_data: Data, assert_problem: Any) -> None:
    policy_data["budgets"]["session"]["max_cost_usd"] = 0.0000001
    assert_problem(policy_data, "no more than 6 decimal places")


def test_fallback_price_must_not_undercut_listed_models(
    policy_data: Data, assert_problem: Any
) -> None:
    fallback = policy_data["pricing"]["fallback"]
    policy_data["pricing"]["models"]["model-b"] = {**fallback, "output_per_mtok": 9}
    assert_problem(policy_data, "fallback.output_per_mtok (5) is below the highest listed rate (9)")


def test_unlisted_models_are_priced_at_the_fallback(policy_data: Data) -> None:
    pricing = PolicyDocument.from_data(policy_data).pricing
    assert pricing.price_for("model-a") == (pricing.models["model-a"], False)
    assert pricing.price_for("model-z") == (pricing.fallback, True)


# --- Immutability ---------------------------------------------------------------------


def test_policy_is_immutable(policy_data: Data) -> None:
    doc = PolicyDocument.from_data(policy_data)
    with pytest.raises(ValidationError, match="frozen"):
        doc.tools["svc__set_item"].requires_approval = False  # type: ignore[misc]
    assert isinstance(doc.roles["writer"].deny, tuple)
