"""Loading policies.yaml: YAML hazards, provenance, error reporting."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
import yaml

from mcp_gateway.policy import LoadedPolicy, PolicyLoadError, load_policy, parse_policy


def load_errors(text: str) -> tuple[str, ...]:
    with pytest.raises(PolicyLoadError) as excinfo:
        parse_policy(text, source="test.yaml")
    return excinfo.value.problems


def test_shipped_policy_loads(shipped: LoadedPolicy) -> None:
    doc = shipped.document
    assert set(doc.tools) == {
        "crm-ops__search_accounts",
        "crm-ops__get_account_metrics",
        "crm-ops__update_credit_limit",
    }
    assert doc.tools["crm-ops__update_credit_limit"].requires_approval
    assert not doc.tools["crm-ops__search_accounts"].requires_approval


def test_digest_identifies_the_exact_bytes_loaded(
    shipped: LoadedPolicy, shipped_policy_path: Path
) -> None:
    expected = hashlib.sha256(shipped_policy_path.read_bytes()).hexdigest()
    assert shipped.digest == f"sha256:{expected}"
    assert shipped.source.endswith("policies.yaml")


def test_duplicate_keys_are_rejected(policy_data: dict[str, Any]) -> None:
    # The second entry would otherwise silently replace the gated definition.
    text = yaml.safe_dump(policy_data, sort_keys=False).replace(
        "  svc__drop_item:", "  svc__set_item:\n    access: write\n  svc__drop_item:", 1
    )
    (problem,) = load_errors(text)
    assert "found duplicate key 'svc__set_item'" in problem
    assert "first defined at line" in problem


def test_yaml_merge_keys_may_be_overridden_explicitly(policy_data: dict[str, Any]) -> None:
    text = yaml.safe_dump(policy_data, sort_keys=False)
    anchored = "  svc__get_item: &read_tool\n    access: read\n    risk: medium"
    merged = "  svc__get_other:\n    <<: *read_tool\n    access: write\n  svc__drop_item:"
    text = text.replace("  svc__get_item:\n    access: read", anchored, 1)
    text = text.replace("  svc__drop_item:", merged, 1)
    other = parse_policy(text).document.tools["svc__get_other"]
    assert (other.access, other.risk) == ("write", "medium")  # explicit key wins, rest merged


def test_yaml_syntax_errors_report_their_position() -> None:
    (problem,) = load_errors("version: 1\nroles: [unclosed\n")
    assert problem.startswith("YAML line ")


@pytest.mark.parametrize("text", ["- just\n- a list\n", "plain scalar\n", ""])
def test_document_must_be_a_mapping(text: str) -> None:
    assert load_errors(text) == ("the document must be a YAML mapping",)


@pytest.mark.parametrize(
    ("original", "replacement", "expected"),
    [
        (
            "  name: unit-test",
            "  name: unit-test\n  description: 2026-11-25",
            "metadata.description: YAML read a date",
        ),
        ("max_tool_calls: 10", "max_tool_calls: .nan", "budgets.session.max_tool_calls: nan is"),
        (
            "  svc__get_item:",
            "  yes:\n    access: read\n  svc__get_item:",
            "tools.True: mapping keys must be strings (YAML read a bool)",
        ),
    ],
    ids=["unquoted-date", "nan", "yaml11-boolean-key"],
)
def test_values_json_cannot_represent_are_reported_with_their_path(
    policy_data: dict[str, Any], original: str, replacement: str, expected: str
) -> None:
    text = yaml.safe_dump(policy_data, sort_keys=False)
    assert original in text
    (problem,) = load_errors(text.replace(original, replacement, 1))
    assert problem.startswith(expected)


def test_every_schema_problem_is_reported_at_once(policy_data: dict[str, Any]) -> None:
    policy_data["tools"]["svc__set_item"]["requires_aproval"] = True
    policy_data["budgets"]["session"]["max_tool_calls"] = -1
    found = load_errors(yaml.safe_dump(policy_data))
    assert len(found) == 2
    assert any(p.startswith("tools.svc__set_item.requires_aproval") for p in found)
    assert any(p.startswith("budgets.session.max_tool_calls") for p in found)


def test_missing_file_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(PolicyLoadError, match="cannot read file"):
        load_policy(tmp_path / "absent.yaml")


def test_error_message_names_the_source(tmp_path: Path) -> None:
    path = tmp_path / "policies.yaml"
    path.write_text("version: 2\n")
    with pytest.raises(PolicyLoadError) as excinfo:
        load_policy(path)
    assert str(excinfo.value).startswith(f"invalid policy {path}:")
