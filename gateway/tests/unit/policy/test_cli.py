"""mcp-gateway-policy command line."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_gateway.policy.cli import main


def test_validate_accepts_the_shipped_policy(
    shipped_policy_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["validate", str(shipped_policy_path)]) == 0
    assert "policy 'enterprise-default'" in capsys.readouterr().out


def test_validate_rejects_an_invalid_policy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "policies.yaml"
    bad.write_text("version: 1\nmetadata: {name: x}\npolices: {}\n", encoding="utf-8")
    assert main(["validate", str(bad)]) == 1
    err = capsys.readouterr().err
    assert "polices: unknown field" in err
    assert "roles: Field required" in err


def test_matrix_shows_effective_access(
    shipped_policy_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["matrix", str(shipped_policy_path)]) == 0
    rows = {
        line.split()[0]: line.split()[1:] for line in capsys.readouterr().out.splitlines() if line
    }
    # columns: access, risk, analyst, admin, operator, auditor
    assert rows["crm-ops__update_credit_limit"] == ["write", "high", "-", "HITL", "-", "-"]
    assert rows["crm-ops__search_accounts"] == ["read", "low", "ALLOW", "ALLOW", "-", "-"]
    assert rows["approvals:decide"] == ["-", "-", "yes", "-"]


def test_schema_check_detects_drift(
    tmp_path: Path, shipped_schema_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["schema", "--output", str(shipped_schema_path), "--check"]) == 0
    stale = tmp_path / "schema.json"
    stale.write_text("{}\n", encoding="utf-8")
    assert main(["schema", "--output", str(stale), "--check"]) == 1
    assert "out of date" in capsys.readouterr().err
