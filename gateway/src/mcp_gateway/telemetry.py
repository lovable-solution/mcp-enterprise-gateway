"""Gateway telemetry: span attribute names and metric instruments.

Spans: the MCP SDK emits one SERVER span per inbound MCP message (``tools/call <tool>``)
with the OpenTelemetry MCP semantic-convention attributes, continuing the caller's trace
from ``_meta``; FastAPI emits HTTP spans for every route. The gateway annotates the SDK
span with its decision and adds a child span per enforcement step; the upstream call's
CLIENT span propagates the trace on to the MCP server.

Metrics (exported through the collector to Prometheus): tool-call latency and outcomes,
policy denials, circuit-breaker trips, budget rejections, token and USD spend, approvals.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from opentelemetry import metrics, trace
from opentelemetry.metrics import Counter, Histogram

tracer: Final = trace.get_tracer("mcp_gateway")


class Attr:
    TENANT: Final = "mcp_gateway.tenant.id"
    SESSION: Final = "mcp_gateway.session.id"
    SUBJECT: Final = "enduser.id"
    ROLE: Final = "mcp_gateway.principal.role"
    TOOL: Final = "gen_ai.tool.name"
    UPSTREAM: Final = "mcp_gateway.upstream.server"
    OUTCOME: Final = "mcp_gateway.outcome"
    DECISION: Final = "mcp_gateway.policy.decision"
    DENY_REASON: Final = "mcp_gateway.policy.deny_reason"
    RULE: Final = "mcp_gateway.policy.rule"
    POLICY_DIGEST: Final = "mcp_gateway.policy.digest"
    APPROVAL_ID: Final = "mcp_gateway.approval.id"
    BREAKER_STATE: Final = "mcp_gateway.circuit_breaker.state"
    IDENTICAL_CALLS: Final = "mcp_gateway.loop.identical_calls"
    BUDGET_LIMIT: Final = "mcp_gateway.budget.limit"
    MODEL: Final = "gen_ai.request.model"
    TOKEN_TYPE: Final = "gen_ai.token.type"  # noqa: S105 - attribute name, not a secret
    COST_SOURCE: Final = "mcp_gateway.cost.source"


class Outcome(StrEnum):
    """Final disposition of a tool call, recorded on spans and metrics."""

    SUCCEEDED = "succeeded"
    TOOL_ERROR = "tool_error"
    DENIED = "denied"
    INVALID_ARGUMENTS = "invalid_arguments"
    CIRCUIT_OPEN = "circuit_open"
    BUDGET_EXCEEDED = "budget_exceeded"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    APPROVAL_FAILED = "approval_failed"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"


@dataclass(frozen=True, slots=True)
class Instruments:
    tool_calls: Counter
    tool_call_duration: Histogram
    policy_denials: Counter
    breaker_trips: Counter
    budget_rejections: Counter
    tokens: Counter
    cost_usd: Counter
    approvals: Counter
    upstream_duration: Histogram

    @classmethod
    def create(cls) -> Instruments:
        meter = metrics.get_meter("mcp_gateway")
        return cls(
            tool_calls=meter.create_counter(
                "mcp_gateway.tool_calls", unit="{call}", description="Tool calls by outcome."
            ),
            tool_call_duration=meter.create_histogram(
                "mcp_gateway.tool_call.duration",
                unit="s",
                description="Gateway-side latency of tools/call, enforcement included.",
            ),
            policy_denials=meter.create_counter(
                "mcp_gateway.policy.denials", unit="{call}", description="RBAC denials by reason."
            ),
            breaker_trips=meter.create_counter(
                "mcp_gateway.circuit_breaker.trips",
                unit="{trip}",
                description="Sessions halted by loop detection.",
            ),
            budget_rejections=meter.create_counter(
                "mcp_gateway.budget.rejections",
                unit="{call}",
                description="Tool calls refused because a session limit was reached.",
            ),
            tokens=meter.create_counter(
                "mcp_gateway.tokens", unit="{token}", description="Model tokens reported by agents."
            ),
            cost_usd=meter.create_counter(
                "mcp_gateway.cost.usd",
                unit="{USD}",
                description="Spend charged to session budgets (model tokens and metered tools).",
            ),
            approvals=meter.create_counter(
                "mcp_gateway.approvals",
                unit="{request}",
                description="Approval state transitions.",
            ),
            upstream_duration=meter.create_histogram(
                "mcp_gateway.upstream.duration",
                unit="s",
                description="Latency of calls to upstream MCP servers.",
            ),
        )
