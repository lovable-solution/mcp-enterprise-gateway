"""The committed JSON Schema (config/policies.schema.json) is generated, not hand-edited."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from mcp_gateway.policy.schema import render_policy_json_schema


@pytest.fixture(scope="module")
def validator(shipped_schema_path: Path) -> Draft202012Validator:
    schema = json.loads(shipped_schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_committed_schema_matches_the_models(shipped_schema_path: Path) -> None:
    assert shipped_schema_path.read_text(encoding="utf-8") == render_policy_json_schema(), (
        "config/policies.schema.json is stale; regenerate it with `make schema`"
    )


def test_shipped_policy_satisfies_the_json_schema(
    validator: Draft202012Validator, shipped_data: dict[str, Any]
) -> None:
    errors = [error.message for error in validator.iter_errors(shipped_data)]
    assert errors == []


def test_json_schema_catches_typos_for_editors(
    validator: Draft202012Validator, shipped_data: dict[str, Any]
) -> None:
    shipped_data["tools"]["crm-ops__update_credit_limit"]["requires_aproval"] = True
    assert not validator.is_valid(shipped_data)


def test_json_schema_enforces_tool_name_shape(
    validator: Draft202012Validator, shipped_data: dict[str, Any]
) -> None:
    shipped_data["tools"]["update_credit_limit"] = {"access": "write"}
    assert not validator.is_valid(shipped_data)
