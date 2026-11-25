"""Pure pieces: usage arithmetic, governance mapping, approval outcomes, identity, settings."""

from __future__ import annotations

from typing import Any

import pytest
from jose import jwt
from mcp_types import CallToolResult, TextContent
from pydantic import SecretStr, ValidationError
from pydantic_ai import (
    CallDeferred,
    ModelRequest,
    ModelResponse,
    ModelRetry,
    TextPart,
    ToolCallPart,
    ToolFailed,
    ToolReturn,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RequestUsage

from agent_runtime.agent import SERVER_SIDE_FALLBACK_BETA, claude_model
from agent_runtime.gateway import GOVERNANCE_META_KEY
from agent_runtime.graph import approval_outcome, consistent, deferred_results, find_exception
from agent_runtime.identity import DevTokenMinter, IdentityError, StaticToken, check_session_id
from agent_runtime.settings import AgentSettings
from agent_runtime.toolset import GovernanceHaltError, interpret
from agent_runtime.usage import usage_report


def settings(**overrides: Any) -> AgentSettings:
    base: dict[str, Any] = {"dev_jwt_secret": "d" * 40, "checkpointer": "memory"}
    return AgentSettings(_env_file=None, **(base | overrides))


# --- usage ------------------------------------------------------------------------------------
def test_usage_reports_count_cached_tokens_once() -> None:
    """PydanticAI's input_tokens include cache reads and writes; Anthropic's (and the
    gateway's) do not."""
    response = ModelResponse(
        parts=[TextPart("hi")],
        model_name="claude-opus-5-5",
        usage=RequestUsage(
            input_tokens=2_400,
            output_tokens=50,
            cache_read_tokens=2_000,
            cache_write_tokens=300,
            cache_write_1h_tokens=200,
        ),
    )
    assert usage_report(response) == {
        "model": "claude-opus-5-5",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_creation_input_tokens": 300,
        "cache_read_input_tokens": 2_000,
        "cache_creation": {"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 200},
    }


def test_usage_reports_name_the_model_that_answered() -> None:
    """With server-side fallbacks the answering model can differ from the requested one;
    the gateway must price the one that ran."""
    response = ModelResponse(
        parts=[TextPart("hi")],
        model_name="claude-opus-4-8",
        usage=RequestUsage(input_tokens=10, output_tokens=5),
    )
    report = usage_report(response)
    assert report["model"] == "claude-opus-4-8"
    assert "cache_creation" not in report


# --- governance verdicts --------------------------------------------------------------------
def result(
    code: str | None, text: str = "message", *, is_error: bool = True, **meta: Any
) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        is_error=is_error,
        meta={GOVERNANCE_META_KEY: {"code": code, **meta}} if code else None,
    )


def test_plain_results_reach_the_model() -> None:
    ok = CallToolResult(
        content=[TextContent(type="text", text="{}")],
        structured_content={"accounts": []},
        is_error=False,
    )
    assert interpret(ok) == {"accounts": []}
    with pytest.raises(ToolFailed, match="on hold"):
        interpret(result(None, "account is on hold"))


def test_held_calls_are_deferred_with_their_approval_id() -> None:
    with pytest.raises(CallDeferred) as deferred:
        interpret(result("PENDING_APPROVAL", "held", approval_id="a-1", expires_at="t"))
    assert deferred.value.metadata is not None
    assert deferred.value.metadata["approval_id"] == "a-1"
    assert deferred.value.metadata["message"] == "held"


def test_inline_approvals_return_the_upstream_result() -> None:
    approved = CallToolResult(
        content=[TextContent(type="text", text="{}")],
        structured_content={"new_limit_usd": 1},
        is_error=False,
        meta={GOVERNANCE_META_KEY: {"code": "APPROVED", "decided_by": "ops"}},
    )
    assert interpret(approved) == {"new_limit_usd": 1}


@pytest.mark.parametrize("code", ["BUDGET_EXCEEDED", "CIRCUIT_OPEN"])
def test_a_stopped_session_halts_the_run(code: str) -> None:
    with pytest.raises(GovernanceHaltError) as halt:
        interpret(result(code, "stopped", limit="max_tool_calls"))
    assert halt.value.code == code


def test_invalid_arguments_ask_the_model_to_correct_them() -> None:
    with pytest.raises(ModelRetry, match="new_limit_usd"):
        interpret(result("INVALID_ARGUMENTS", "new_limit_usd must be an integer"))


@pytest.mark.parametrize(
    "code", ["REJECTED", "EXPIRED", "APPROVAL_FAILED", "UPSTREAM_UNAVAILABLE", "OUTCOME_UNKNOWN"]
)
def test_terminal_verdicts_are_tool_failures(code: str) -> None:
    with pytest.raises(ToolFailed, match="explained"):
        interpret(result(code, "explained"))


# --- approval outcomes -----------------------------------------------------------------------
def test_an_executed_approval_returns_the_recorded_result_and_reviewer() -> None:
    outcome = approval_outcome(
        {
            "id": "a-1",
            "status": "EXECUTED",
            "decided_by_subject": "ops-alice",
            "result": {
                "content": [{"type": "text", "text": "{}"}],
                "structuredContent": {"new_limit_usd": 650_000},
                "isError": False,
            },
        }
    )
    assert outcome == {
        "kind": "return",
        "value": {
            "approved_by": "ops-alice",
            "approval_id": "a-1",
            "result": {"new_limit_usd": 650_000},
        },
    }


@pytest.mark.parametrize(
    ("view", "expected"),
    [
        (
            {"status": "REJECTED", "decided_by_subject": "ops-bob", "decision_reason": "too high"},
            "ops-bob rejected this call (too high). It was not executed.",
        ),
        ({"status": "EXPIRED"}, "expired. It was not executed."),
        ({"status": "FAILED", "error": "crm-ops unreachable"}, "crm-ops unreachable"),
        (
            {
                "status": "EXECUTED",
                "result": {"content": [{"type": "text", "text": "on hold"}], "isError": True},
            },
            "the tool failed: on hold",
        ),
    ],
)
def test_other_decisions_are_failures_the_model_must_report(
    view: dict[str, Any], expected: str
) -> None:
    outcome = approval_outcome(view)
    assert outcome is not None
    assert outcome["kind"] == "failed"
    assert expected in outcome["message"]


@pytest.mark.parametrize("status", ["PENDING_APPROVAL", "APPROVED", "EXECUTING"])
def test_undecided_or_running_approvals_keep_the_thread_waiting(status: str) -> None:
    assert approval_outcome({"status": status}) is None


def test_decided_outcomes_become_deferred_tool_results() -> None:
    results = deferred_results(
        {
            "c-1": {"kind": "return", "value": {"ok": True}},
            "c-2": {"kind": "failed", "message": "rejected"},
        }
    )
    assert results is not None
    assert results.calls["c-1"] == ToolReturn({"ok": True})
    assert results.calls["c-2"] == ToolFailed("rejected")
    assert deferred_results({}) is None


# --- history and errors -----------------------------------------------------------------------
def test_unanswered_tool_calls_are_dropped_from_a_stopped_run() -> None:
    prompt = ModelRequest(parts=[UserPromptPart("go")])
    one = ToolCallPart("crm-ops__search_accounts", {"query": "x"}, tool_call_id="c-1")
    two = ToolCallPart("crm-ops__search_accounts", {"query": "y"}, tool_call_id="c-2")
    calls = ModelResponse(parts=[one, two])
    empty = ModelRequest(parts=[])
    partial = ModelRequest(parts=[ToolReturnPart(one.tool_name, "ok", tool_call_id="c-1")])
    complete = ModelRequest(
        parts=[
            ToolReturnPart(one.tool_name, "ok", tool_call_id="c-1"),
            ToolReturnPart(two.tool_name, "ok", tool_call_id="c-2"),
        ]
    )
    assert consistent([prompt, calls]) == [prompt]
    assert consistent([prompt, calls, empty]) == [prompt]  # what a raising tool leaves
    assert consistent([prompt, calls, partial]) == [prompt]  # a parallel call never answered
    assert consistent([prompt, calls, complete]) == [prompt, calls, complete]
    answer = ModelResponse(parts=[TextPart("done")])
    assert consistent([prompt, answer]) == [prompt, answer]
    assert consistent([]) == []


def test_halts_are_found_inside_task_group_errors() -> None:
    halt = GovernanceHaltError("CIRCUIT_OPEN", "loop", {})
    nested = ExceptionGroup("tools", [ValueError("x"), ExceptionGroup("inner", [halt])])
    assert find_exception(nested, GovernanceHaltError) is halt
    assert find_exception(ValueError("x"), GovernanceHaltError) is None


# --- identity -----------------------------------------------------------------------------------
def test_dev_tokens_carry_the_thread_as_session() -> None:
    minter = DevTokenMinter(
        secret=SecretStr("k" * 40),
        issuer="https://idp.test/",
        audience="mcp-gateway",
        tenant_id="acme",
        subject="crm-copilot",
        role="admin",
    )
    claims = jwt.decode(
        minter.token_for("t-1234"), "k" * 40, algorithms=["HS256"], audience="mcp-gateway"
    )
    assert (claims["sid"], claims["sub"], claims["role"]) == ("t-1234", "crm-copilot", "admin")
    assert minter.new_session_id().startswith("t-")


def test_a_static_token_only_serves_its_own_session() -> None:
    token = jwt.encode({"sid": "s-42", "sub": "bot"}, "k" * 40, algorithm="HS256")
    provider = StaticToken(SecretStr(token))
    assert provider.new_session_id() == "s-42"
    assert provider.token_for("s-42") == token
    with pytest.raises(IdentityError, match="belongs to session 's-42'"):
        provider.token_for("t-other")


@pytest.mark.parametrize("bad", ["", "a/b", "{x}", "x" * 200, "-leading"])
def test_thread_ids_must_be_valid_session_ids(bad: str) -> None:
    with pytest.raises(IdentityError):
        check_session_id(bad)


# --- settings and model ------------------------------------------------------------------------
def test_settings_need_gateway_credentials() -> None:
    with pytest.raises(ValidationError, match="AGENT_GATEWAY_TOKEN"):
        AgentSettings(_env_file=None, checkpointer="memory")
    with pytest.raises(ValidationError, match="AGENT_DATABASE_PASSWORD"):
        AgentSettings(_env_file=None, dev_jwt_secret=SecretStr("d" * 40))


def test_stack_variable_names_are_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("GATEWAY_JWT_SECRET", "g" * 40)
    monkeypatch.setenv("AGENT_DB_PASSWORD", "p" * 20)
    loaded = AgentSettings(_env_file=None)
    assert loaded.anthropic_api_key is not None
    assert loaded.dev_jwt_secret is not None
    assert loaded.database_password is not None
    assert "sk-test" not in repr(loaded)


def test_claude_defaults_adaptive_thinking_and_server_side_fallbacks() -> None:
    model, model_settings = claude_model(settings(anthropic_api_key="sk-test"))
    assert model.model_name == "claude-opus-5-5"
    assert model_settings.get("anthropic_thinking") == {"type": "adaptive"}
    assert model_settings.get("anthropic_effort") == "medium"
    assert model_settings.get("anthropic_betas") == [SERVER_SIDE_FALLBACK_BETA]
    assert model_settings.get("extra_body") == {"fallbacks": "default"}
    assert "temperature" not in model_settings  # not accepted by Claude Opus 5.5
    _, without = claude_model(settings(anthropic_api_key="sk-test", server_side_fallbacks=False))
    assert "extra_body" not in without


def test_claude_needs_an_api_key() -> None:
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        claude_model(settings())
