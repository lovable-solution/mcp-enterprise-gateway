"""Pydantic v2 schema for ``policies.yaml``, the gateway's declarative governance policy.

A validated :class:`PolicyDocument` is the single source of truth for:

* **RBAC**: which roles may invoke which upstream MCP tools (data plane) and which
  operator actions they may perform (control plane);
* **Human-in-the-loop (HITL)**: which tools are suspended for approval, and who decides;
* **Budgets**: per-session hard caps on tool calls, tokens and USD, plus the price table
  that converts reported token usage into USD;
* **Loop detection**: the circuit breaker that halts an agent repeating the same call.

Every rule below is checked once, when the policy is loaded. A policy that breaks any of
them is rejected as a whole and the gateway refuses to start (fail closed):

1. **Closed world**: unknown keys are errors, so a typo such as ``requires_aproval`` can
   never silently disable a control.
2. **Strict types**: no coercion; ``"yes"`` is not a bool and ``"50"`` is not an int.
3. **Default deny**: a tool absent from ``tools`` is denied to every role (and hidden from
   ``tools/list``); a role absent from ``roles`` can do nothing.
4. **Deny wins**: an explicit ``deny`` beats any ``allow``, including inherited ones.
5. **Checked references**: role inheritance, approver roles and grant patterns are
   validated against each other; a grant pattern that matches no registered tool is an
   error, not a no-op.
6. **Segregation of duties**: a role that can call an approval-gated tool can never be
   an approver for that tool, so agents cannot approve each other's high-risk calls.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from fnmatch import fnmatchcase
from typing import Annotated, Any, Final, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

SCHEMA_VERSION: Final = 1

# --------------------------------------------------------------------------------------
# Naming rules
# --------------------------------------------------------------------------------------

TOOL_NAME_SEPARATOR: Final = "__"
"""Separates the upstream server name from the tool name in a qualified tool name."""

MAX_TOOL_NAME_LENGTH: Final = 64
"""Qualified names are passed to LLM tool-calling APIs verbatim; Anthropic and OpenAI cap
tool names at 64 characters of ``[A-Za-z0-9_-]``."""

# Server names cannot contain underscores, so the first "__" always splits a qualified name
# unambiguously. All patterns avoid regex features that differ between Python, Rust (used
# by pydantic-core) and ECMAScript (used by editors consuming the exported JSON Schema).
_SERVER = r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
_TOOL = r"[A-Za-z0-9][A-Za-z0-9_-]*"
SERVER_NAME_REGEX: Final = rf"^{_SERVER}$"
QUALIFIED_TOOL_NAME_REGEX: Final = rf"^{_SERVER}{TOOL_NAME_SEPARATOR}{_TOOL}$"
TOOL_PATTERN_REGEX: Final = r"^[A-Za-z0-9_*?-]+$"
ROLE_NAME_REGEX: Final = r"^[a-z][a-z0-9_-]{0,31}$"
MODEL_ID_REGEX: Final = r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$"
POLICY_NAME_REGEX: Final = r"^[a-z0-9][a-z0-9._-]{0,63}$"

MAX_APPROVAL_TIMEOUT_SECONDS: Final = 7 * 24 * 3600


def split_tool_name(qualified_name: str) -> tuple[str, str]:
    """Split ``crm-ops__update_credit_limit`` into ``("crm-ops", "update_credit_limit")``."""
    server, sep, tool = qualified_name.partition(TOOL_NAME_SEPARATOR)
    if not sep or not server or not tool:
        msg = f"not a qualified tool name: {qualified_name!r}"
        raise ValueError(msg)
    return server, tool


# --------------------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------------------


class AccessLevel(StrEnum):
    """What a tool does to the world. Role grants are scoped by access level."""

    READ = "read"
    WRITE = "write"


class RiskTier(StrEnum):
    """Business impact if a tool is misused. Surfaced to operators; ``critical`` tools
    must require approval."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ControlAction(StrEnum):
    """Control-plane permissions, used by the operator console and its API."""

    APPROVALS_READ = "approvals:read"
    APPROVALS_DECIDE = "approvals:decide"
    SESSIONS_READ = "sessions:read"
    CIRCUIT_BREAKERS_RESET = "circuit_breakers:reset"
    POLICY_READ = "policy:read"


# --------------------------------------------------------------------------------------
# Constrained scalar types
# --------------------------------------------------------------------------------------


def _reject_duplicates[T](values: tuple[T, ...]) -> tuple[T, ...]:
    seen: set[T] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(str(value))
        seen.add(value)
    if duplicates:
        msg = f"duplicate entries: {', '.join(sorted(duplicates))}"
        raise ValueError(msg)
    return values


QualifiedToolName = Annotated[
    str, StringConstraints(pattern=QUALIFIED_TOOL_NAME_REGEX, max_length=MAX_TOOL_NAME_LENGTH)
]
ToolPattern = Annotated[
    str, StringConstraints(pattern=TOOL_PATTERN_REGEX, max_length=MAX_TOOL_NAME_LENGTH)
]
RoleName = Annotated[str, StringConstraints(pattern=ROLE_NAME_REGEX)]
ModelId = Annotated[str, StringConstraints(pattern=MODEL_ID_REGEX)]
Description = Annotated[str, StringConstraints(min_length=1, max_length=500)]

#: Non-negative USD amount, exact (``Decimal``), at most 6 decimal places (1 micro-USD).
UsdAmount = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=6)]
#: Strictly positive USD amount.
PositiveUsd = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=6)]

RoleList = Annotated[tuple[RoleName, ...], AfterValidator(_reject_duplicates)]
ToolPatternList = Annotated[tuple[ToolPattern, ...], AfterValidator(_reject_duplicates)]
AccessList = Annotated[tuple[AccessLevel, ...], AfterValidator(_reject_duplicates)]
ActionList = Annotated[tuple[ControlAction, ...], AfterValidator(_reject_duplicates)]


# Pydantic exports constrained dict keys as JSON Schema ``patternProperties``, which still
# admits keys that do not match; closing the mapping makes editors flag bad names too.
_CLOSED_KEYS: Final[dict[str, Any]] = {"additionalProperties": False}


class _PolicyModel(BaseModel):
    """Base for every policy node: immutable, strictly typed, closed world."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
        use_attribute_docstrings=True,
    )


# --------------------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------------------


class ApprovalOverride(_PolicyModel):
    """Per-tool overrides of the global ``approvals`` settings."""

    approver_roles: RoleList | None = Field(default=None, min_length=1)
    """Roles allowed to decide this tool's approval requests (default:
    ``approvals.approver_roles``). Each must hold the ``approvals:decide`` action."""

    timeout_seconds: int | None = Field(default=None, ge=60, le=MAX_APPROVAL_TIMEOUT_SECONDS)
    """Seconds a request may stay ``PENDING_APPROVAL`` before it becomes ``EXPIRED``
    (default: ``approvals.timeout_seconds``)."""


class ToolPolicy(_PolicyModel):
    """Governance metadata for one upstream tool. Unregistered tools are denied."""

    access: AccessLevel
    """``read`` for side-effect-free tools, ``write`` for anything that changes state.
    The gateway trusts this classification, never the upstream server's own hints."""

    risk: RiskTier = RiskTier.LOW
    """Business impact if misused. Shown to operators; ``critical`` requires approval."""

    requires_approval: bool = False
    """Suspend every call for a human decision before it reaches the upstream server."""

    approval: ApprovalOverride | None = None
    """Approval settings for this tool; only valid when ``requires_approval`` is true."""

    cost_usd_per_call: UsdAmount = Decimal(0)
    """Flat USD charged to the session budget each time the call is forwarded upstream
    (e.g. a metered data provider)."""

    description: Description | None = None
    """Operator-facing note. Never sent to agents."""

    @model_validator(mode="after")
    def _check_approval_consistency(self) -> Self:
        if self.approval is not None and not self.requires_approval:
            msg = "'approval' settings are only valid when 'requires_approval' is true"
            raise ValueError(msg)
        if self.risk is RiskTier.CRITICAL and not self.requires_approval:
            msg = "tools with risk 'critical' must set 'requires_approval: true'"
            raise ValueError(msg)
        return self


# --------------------------------------------------------------------------------------
# Roles
# --------------------------------------------------------------------------------------


class RoleGrant(_PolicyModel):
    """Allows the tools matched by ``tools`` whose ``access`` is listed in ``access``."""

    tools: ToolPatternList = Field(min_length=1)
    """Case-sensitive glob patterns over qualified tool names (``*`` and ``?`` only),
    e.g. ``crm-ops__*``. Each pattern must match at least one registered tool."""

    access: AccessList = Field(min_length=1)
    """Access levels covered. A matched tool is allowed only if its access is listed."""


class BudgetOverride(_PolicyModel):
    """Per-role replacement for individual ``budgets.session`` limits."""

    max_tool_calls: int | None = Field(default=None, gt=0)
    max_total_tokens: int | None = Field(default=None, gt=0)
    max_cost_usd: PositiveUsd | None = None

    @model_validator(mode="after")
    def _check_not_empty(self) -> Self:
        if (
            self.max_tool_calls is None
            and self.max_total_tokens is None
            and (self.max_cost_usd is None)
        ):
            msg = "a budget override must set at least one limit"
            raise ValueError(msg)
        return self


class RolePolicy(_PolicyModel):
    """A role named by the JWT ``role`` claim."""

    description: Description
    """What the role is for. Shown in the operator console."""

    inherits: RoleList = ()
    """Roles whose ``allow``, ``deny`` and ``actions`` are merged into this one
    (transitively). Budgets are not inherited."""

    allow: tuple[RoleGrant, ...] = ()
    """Tool grants (data plane). Nothing is allowed unless a grant matches."""

    deny: ToolPatternList = ()
    """Glob patterns that are always denied, overriding every grant (including inherited
    ones). Unlike grants, deny patterns may guard tools that are not registered yet."""

    actions: ActionList = ()
    """Control-plane actions this role may perform."""

    budget: BudgetOverride | None = None
    """Overrides individual ``budgets.session`` limits for sessions of this role."""


# --------------------------------------------------------------------------------------
# Approvals, budgets, pricing, circuit breaker
# --------------------------------------------------------------------------------------


class ApprovalPolicy(_PolicyModel):
    """Global human-in-the-loop settings."""

    approver_roles: RoleList = Field(min_length=1)
    """Default roles allowed to approve or reject. Each must hold ``approvals:decide``."""

    timeout_seconds: int = Field(default=900, ge=60, le=MAX_APPROVAL_TIMEOUT_SECONDS)
    """Seconds a request may stay ``PENDING_APPROVAL`` before it becomes ``EXPIRED``
    (an expired request is rejected; it never executes)."""

    inline_wait_seconds: int = Field(default=20, ge=0, le=120)
    """How long the gateway holds the agent's ``tools/call`` open waiting for a decision.
    If no decision arrives in time the call returns ``PENDING_APPROVAL`` with the approval
    id, and the agent suspends (checkpoints) until it is decided. Keep this below the load
    balancer idle timeout."""

    require_distinct_approver: bool = True
    """Four-eyes rule: the principal that requested a call can never approve it."""


class SessionBudget(_PolicyModel):
    """Hard caps for one agent session (JWT ``sid``). Exceeding any one denies further
    tool calls in the session."""

    max_tool_calls: int = Field(gt=0)
    """Tool calls forwarded upstream."""

    max_total_tokens: int = Field(gt=0)
    """All tokens reported for the session: input, output, cache writes and cache reads."""

    max_cost_usd: PositiveUsd
    """Token cost (priced via ``pricing``) plus per-call tool costs."""

    def merged(self, override: BudgetOverride | None) -> SessionBudget:
        """Return these limits with every limit set in ``override`` replaced."""
        if override is None:
            return self
        return self.model_copy(update=override.model_dump(exclude_none=True))


class BudgetPolicy(_PolicyModel):
    """Per-session budget enforcement (ledgers live in Redis)."""

    session: SessionBudget
    """Default limits for every session; roles may override individual limits."""

    warn_at_ratio: float = Field(default=0.8, gt=0, lt=1)
    """Emit a ``budget.warning`` event once any limit reaches this fraction."""

    ledger_ttl_seconds: int = Field(default=86_400, ge=3_600, le=30 * 86_400)
    """Idle time after which a session's ledger expires. Must outlive every approval
    timeout so a ledger cannot expire while a call awaits approval."""


class ModelPrice(_PolicyModel):
    """USD per million tokens, by token type, as billed by the model provider."""

    input_per_mtok: UsdAmount
    output_per_mtok: UsdAmount
    cache_write_5m_per_mtok: UsdAmount
    """Prompt-cache writes with the default 5-minute TTL."""
    cache_write_1h_per_mtok: UsdAmount
    """Prompt-cache writes with the 1-hour TTL."""
    cache_read_per_mtok: UsdAmount


class PricingPolicy(_PolicyModel):
    """Converts reported token usage into USD for budget enforcement."""

    currency: Literal["USD"] = "USD"

    models: dict[ModelId, ModelPrice] = Field(min_length=1, json_schema_extra=_CLOSED_KEYS)
    """Prices keyed by the model id the provider reports in its response."""

    fallback: ModelPrice
    """Applied to usage from any model not listed in ``models``. Must be at least as
    expensive as every listed model, so an unpriced model can only over-count."""

    def price_for(self, model_id: str) -> tuple[ModelPrice, bool]:
        """Return ``(price, is_fallback)`` for a model id."""
        price = self.models.get(model_id)
        return (price, False) if price is not None else (self.fallback, True)

    @model_validator(mode="after")
    def _check_fallback_is_conservative(self) -> Self:
        problems = []
        for field in ModelPrice.model_fields:
            ceiling = max(getattr(price, field) for price in self.models.values())
            if getattr(self.fallback, field) < ceiling:
                problems.append(
                    f"fallback.{field} ({getattr(self.fallback, field)}) is below the "
                    f"highest listed rate ({ceiling})"
                )
        if problems:
            msg = "fallback pricing must not undercut any listed model: " + "; ".join(problems)
            raise ValueError(msg)
        return self


class LoopDetectionPolicy(_PolicyModel):
    """Trips the circuit breaker when an agent repeats an identical call."""

    enabled: bool = True

    max_identical_calls: int = Field(default=3, ge=1, le=1_000)
    """Identical calls (same session, tool and canonical arguments) allowed within the
    window. The next identical call trips the breaker."""

    window_seconds: int = Field(default=60, ge=1, le=86_400)
    """Sliding window over which identical calls are counted."""


class CircuitBreakerPolicy(_PolicyModel):
    """What happens once loop detection trips."""

    loop_detection: LoopDetectionPolicy = Field(default_factory=LoopDetectionPolicy)

    cooldown_seconds: int = Field(default=300, ge=1, le=86_400)
    """How long a tripped breaker blocks every tool call in the session."""

    require_manual_reset: bool = False
    """Keep a tripped breaker open until an operator resets it, ignoring the cooldown."""


# --------------------------------------------------------------------------------------
# Role resolution (shared by validation and the engine)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResolvedRole:
    """A role with inheritance flattened. Rules keep the role that declared them so every
    decision can be attributed to a precise policy location."""

    name: str
    lineage: tuple[str, ...]
    """The role itself, then every ancestor (depth-first, deduplicated)."""
    grants: tuple[tuple[str, int, RoleGrant], ...]
    """``(declaring role, index in its allow list, grant)``."""
    denies: tuple[tuple[str, int, str], ...]
    """``(declaring role, index in its deny list, pattern)``."""
    actions: frozenset[ControlAction]


def deciding_rule(
    role: ResolvedRole, tool: str, access: AccessLevel
) -> tuple[Literal["deny", "allow"], str] | None:
    """The rule that decides whether ``role`` may call ``tool`` (a registered tool with the
    given access level): every deny pattern is checked before any grant. Returns the
    verdict and the rule's policy location, or ``None`` when nothing matches."""
    for owner, index, pattern in role.denies:
        if fnmatchcase(tool, pattern):
            return "deny", f"roles.{owner}.deny[{index}]"
    for owner, index, grant in role.grants:
        if access in grant.access and any(fnmatchcase(tool, p) for p in grant.tools):
            return "allow", f"roles.{owner}.allow[{index}]"
    return None


def resolve_roles(roles: Mapping[str, RolePolicy]) -> dict[str, ResolvedRole]:
    """Flatten role inheritance. Raises ``ValueError`` on an unknown parent or a cycle."""

    def lineage(name: str, path: tuple[str, ...]) -> list[str]:
        if name in path:
            msg = f"inheritance cycle: {' -> '.join((*path, name))}"
            raise ValueError(msg)
        role = roles.get(name)
        if role is None:
            msg = f"unknown role {name!r}"
            raise ValueError(msg)
        ordered = [name]
        for parent in role.inherits:
            ordered += [r for r in lineage(parent, (*path, name)) if r not in ordered]
        return ordered

    resolved: dict[str, ResolvedRole] = {}
    for name in roles:
        chain = tuple(lineage(name, ()))
        resolved[name] = ResolvedRole(
            name=name,
            lineage=chain,
            grants=tuple((r, i, g) for r in chain for i, g in enumerate(roles[r].allow)),
            denies=tuple((r, i, p) for r in chain for i, p in enumerate(roles[r].deny)),
            actions=frozenset(a for r in chain for a in roles[r].actions),
        )
    return resolved


# --------------------------------------------------------------------------------------
# Document
# --------------------------------------------------------------------------------------


class PolicyMetadata(_PolicyModel):
    """Identifies the policy in decision logs, traces and the operator console."""

    name: Annotated[str, StringConstraints(pattern=POLICY_NAME_REGEX)]
    """Identifier recorded with every policy decision (alongside the file digest)."""

    description: Description | None = None


class PolicyDocument(_PolicyModel):
    """Root of ``policies.yaml``."""

    version: Literal[1]
    """Schema version. Bumped only for incompatible changes."""

    metadata: PolicyMetadata

    roles: dict[RoleName, RolePolicy] = Field(min_length=1, json_schema_extra=_CLOSED_KEYS)
    """Roles, keyed by the value of the JWT ``role`` claim."""

    tools: dict[QualifiedToolName, ToolPolicy] = Field(
        min_length=1,
        json_schema_extra={**_CLOSED_KEYS, "propertyNames": {"maxLength": MAX_TOOL_NAME_LENGTH}},
    )
    """Registry of every tool the gateway will route, keyed by qualified name
    ``<server>__<tool>`` (the name agents see). Anything not listed is denied."""

    approvals: ApprovalPolicy
    budgets: BudgetPolicy
    pricing: PricingPolicy
    circuit_breaker: CircuitBreakerPolicy = Field(default_factory=CircuitBreakerPolicy)

    @classmethod
    def from_data(cls, data: Any) -> Self:
        """Validate plain data (as parsed from YAML) with strict JSON semantics.

        Round-tripping through JSON gives YAML input exactly the guarantees of a JSON
        document: strings map onto enums, arrays onto immutable tuples and numbers onto
        exact ``Decimal`` money values, while nothing is coerced across types. Values with
        no JSON equivalent (unquoted YAML timestamps, NaN, infinities) are rejected.
        """
        return cls.model_validate_json(json.dumps(data, allow_nan=False))

    @model_validator(mode="after")
    def _check_cross_references(self) -> Self:
        problems = list(_cross_reference_problems(self))
        if problems:
            raise ValueError("\n".join(problems))
        return self


def _cross_reference_problems(doc: PolicyDocument) -> Iterator[str]:
    """Yield one message per broken invariant that spans more than one section."""
    inheritance = list(_inheritance_problems(doc))
    yield from inheritance
    if inheritance:
        return  # every later check needs resolvable roles
    resolved = resolve_roles(doc.roles)
    yield from _grant_problems(doc)
    yield from _approver_problems(doc, resolved)
    yield from _segregation_of_duties_problems(doc, resolved)
    yield from _timing_problems(doc)


def _inheritance_problems(doc: PolicyDocument) -> Iterator[str]:
    unknown_parent = False
    for name, role in doc.roles.items():
        for parent in role.inherits:
            if parent not in doc.roles:
                unknown_parent = True
                yield f"roles.{name}.inherits: unknown role {parent!r}"
    if not unknown_parent:
        try:
            resolve_roles(doc.roles)
        except ValueError as exc:
            yield f"roles: {exc}"


def _grant_problems(doc: PolicyDocument) -> Iterator[str]:
    """Every grant pattern must reach a registered tool at one of the granted levels."""
    for name, role in doc.roles.items():
        for index, grant in enumerate(role.allow):
            for pattern in grant.tools:
                matched = [tool for tool in doc.tools if fnmatchcase(tool, pattern)]
                if not matched:
                    yield (
                        f"roles.{name}.allow[{index}].tools: pattern {pattern!r} matches no "
                        "registered tool"
                    )
                elif not any(doc.tools[tool].access in grant.access for tool in matched):
                    levels = ", ".join(sorted(grant.access))
                    yield (
                        f"roles.{name}.allow[{index}]: pattern {pattern!r} matches only tools "
                        f"whose access is not in [{levels}]"
                    )


def _approver_roles(doc: PolicyDocument, spec: ToolPolicy) -> tuple[str, ...]:
    if spec.approval is not None and spec.approval.approver_roles is not None:
        return spec.approval.approver_roles
    return doc.approvals.approver_roles


def _approver_problems(doc: PolicyDocument, resolved: Mapping[str, ResolvedRole]) -> Iterator[str]:
    """Approvers must exist and hold the ``approvals:decide`` action."""
    approver_lists = [("approvals.approver_roles", doc.approvals.approver_roles)]
    approver_lists += [
        (f"tools.{tool}.approval.approver_roles", spec.approval.approver_roles)
        for tool, spec in doc.tools.items()
        if spec.approval is not None and spec.approval.approver_roles is not None
    ]
    decide = ControlAction.APPROVALS_DECIDE
    for path, approvers in approver_lists:
        for approver in approvers:
            if approver not in resolved:
                yield f"{path}: unknown role {approver!r}"
            elif decide not in resolved[approver].actions:
                yield f"{path}: role {approver!r} lacks the {decide.value!r} action"


def _segregation_of_duties_problems(
    doc: PolicyDocument, resolved: Mapping[str, ResolvedRole]
) -> Iterator[str]:
    """No role may approve calls that its own members are allowed to make."""
    for tool, spec in doc.tools.items():
        if not spec.requires_approval:
            continue
        for approver in _approver_roles(doc, spec):
            if approver not in resolved:
                continue  # reported by _approver_problems
            verdict = deciding_rule(resolved[approver], tool, spec.access)
            if verdict is not None and verdict[0] == "allow":
                yield (
                    f"tools.{tool}: role {approver!r} can both call this tool ({verdict[1]}) "
                    "and approve it; approvers must not be able to make the calls they approve"
                )


def _timing_problems(doc: PolicyDocument) -> Iterator[str]:
    """The inline wait must end before any approval can expire, and budget ledgers must
    outlive every pending approval."""
    timeouts = {"approvals.timeout_seconds": doc.approvals.timeout_seconds}
    timeouts |= {
        f"tools.{tool}.approval.timeout_seconds": spec.approval.timeout_seconds
        for tool, spec in doc.tools.items()
        if spec.approval is not None and spec.approval.timeout_seconds is not None
    }
    inline_wait = doc.approvals.inline_wait_seconds
    for path, timeout in timeouts.items():
        if inline_wait >= timeout:
            yield (
                f"approvals.inline_wait_seconds ({inline_wait}) must be shorter than "
                f"{path} ({timeout})"
            )
    longest_path, longest = max(timeouts.items(), key=lambda item: item[1])
    if doc.budgets.ledger_ttl_seconds <= longest:
        yield (
            f"budgets.ledger_ttl_seconds ({doc.budgets.ledger_ttl_seconds}) must exceed "
            f"the longest approval timeout, {longest_path} ({longest})"
        )
