"""Shared fixtures. Policies are built as plain data (what YAML parses into) so tests
exercise the same strict JSON-mode validation path as the real loader."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from mcp_gateway.policy import LoadedPolicy, PolicyDocument, PolicyEngine, load_policy
from mcp_gateway.policy.loader import format_validation_errors

REPO_ROOT = Path(__file__).resolve().parents[2]
SHIPPED_POLICY = REPO_ROOT / "config" / "policies.yaml"
SHIPPED_SCHEMA = REPO_ROOT / "config" / "policies.schema.json"

PRICE: dict[str, float] = {
    "input_per_mtok": 1.0,
    "output_per_mtok": 5.0,
    "cache_write_5m_per_mtok": 1.25,
    "cache_write_1h_per_mtok": 2.0,
    "cache_read_per_mtok": 0.1,
}


def make_policy() -> dict[str, Any]:
    """Smallest policy exercising inheritance, deny, approval and pricing."""
    return {
        "version": 1,
        "metadata": {"name": "unit-test"},
        "roles": {
            "reader": {
                "description": "Reads items.",
                "allow": [{"tools": ["svc__get_*"], "access": ["read"]}],
            },
            "writer": {
                "description": "Writes items.",
                "inherits": ["reader"],
                "allow": [{"tools": ["svc__*"], "access": ["write"]}],
                "deny": ["svc__drop_*"],
            },
            "approver": {
                "description": "Decides approvals.",
                "actions": ["approvals:read", "approvals:decide"],
            },
        },
        "tools": {
            "svc__get_item": {"access": "read"},
            "svc__set_item": {"access": "write", "requires_approval": True},
            "svc__drop_item": {"access": "write"},
        },
        "approvals": {"approver_roles": ["approver"]},
        "budgets": {
            "session": {"max_tool_calls": 10, "max_total_tokens": 10_000, "max_cost_usd": 1.5}
        },
        "pricing": {"models": {"model-a": dict(PRICE)}, "fallback": dict(PRICE)},
    }


def _problems(data: dict[str, Any]) -> list[str]:
    with pytest.raises(ValidationError) as excinfo:
        PolicyDocument.from_data(data)
    return format_validation_errors(excinfo.value)


def _assert_problem(data: dict[str, Any], fragment: str) -> None:
    found = _problems(data)
    assert any(fragment in line for line in found), f"{fragment!r} not in {found}"


Problems = Callable[[dict[str, Any]], list[str]]
AssertProblem = Callable[[dict[str, Any], str], None]


@pytest.fixture
def policy_data() -> dict[str, Any]:
    """A fresh, valid policy as plain data; tests mutate it."""
    return make_policy()


@pytest.fixture
def problems() -> Problems:
    """Validate data expecting failure; return the operator-facing problem lines."""
    return _problems


@pytest.fixture
def assert_problem() -> AssertProblem:
    """Assert that validating data fails with a problem containing a fragment."""
    return _assert_problem


@pytest.fixture(scope="session")
def shipped_policy_path() -> Path:
    return SHIPPED_POLICY


@pytest.fixture(scope="session")
def shipped_schema_path() -> Path:
    return SHIPPED_SCHEMA


@pytest.fixture
def shipped_data() -> dict[str, Any]:
    """The shipped policy as fresh plain data (safe to mutate)."""
    data: dict[str, Any] = yaml.safe_load(SHIPPED_POLICY.read_text(encoding="utf-8"))
    return data


@pytest.fixture(scope="session")
def shipped() -> LoadedPolicy:
    return load_policy(SHIPPED_POLICY)


@pytest.fixture(scope="session")
def engine(shipped: LoadedPolicy) -> PolicyEngine:
    return PolicyEngine(shipped.document)
