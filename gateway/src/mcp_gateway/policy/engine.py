"""Pure, deterministic evaluation of a validated policy (no I/O, no clock).

The role x tool decision table is finite and static for a given policy, so it is computed
once at construction; request-time checks are dictionary lookups. Every decision carries
the policy location of the rule that produced it, for audit logs and trace attributes.

Evaluation order for ``check_tool(role, tool)``:

1. unknown role                    -> deny (``unknown_role``)
2. tool not in the registry        -> deny (``unregistered_tool``)
3. any ``deny`` pattern matches    -> deny (``explicit_deny``), inherited rules included
4. a grant matches pattern + level -> allow (approval still required if the tool says so)
5. otherwise                       -> deny (``no_matching_grant``)
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from .models import ControlAction, PolicyDocument, SessionBudget, deciding_rule, resolve_roles


class DenyReason(StrEnum):
    UNKNOWN_ROLE = "unknown_role"
    UNREGISTERED_TOOL = "unregistered_tool"
    EXPLICIT_DENY = "explicit_deny"
    NO_MATCHING_GRANT = "no_matching_grant"


@dataclass(frozen=True, slots=True)
class ToolDecision:
    """Outcome of an RBAC check. ``allowed`` does not imply immediate execution: calls to
    tools with ``requires_approval`` are suspended for a human decision first."""

    role: str
    tool: str
    allowed: bool
    reason: DenyReason | None = None
    rule: str | None = None
    """Policy location that decided, e.g. ``roles.analyst.allow[0]``."""
    requires_approval: bool = False
    approver_roles: tuple[str, ...] = ()
    approval_timeout_seconds: int | None = None


class PolicyEngine:
    """Answers authorization questions for one immutable policy."""

    def __init__(self, policy: PolicyDocument) -> None:
        self._policy = policy
        self._roles = resolve_roles(policy.roles)
        self._decisions = {
            (role, tool): self._decide(role, tool) for role in policy.roles for tool in policy.tools
        }

    @property
    def policy(self) -> PolicyDocument:
        return self._policy

    def check_tool(self, role: str, tool: str) -> ToolDecision:
        """May ``role`` call ``tool``? Unknown roles and tools are denied, never raised."""
        decision = self._decisions.get((role, tool))
        if decision is not None:
            return decision
        reason = (
            DenyReason.UNKNOWN_ROLE if role not in self._roles else DenyReason.UNREGISTERED_TOOL
        )
        return ToolDecision(role=role, tool=tool, allowed=False, reason=reason)

    def visible_tools(self, role: str) -> tuple[str, ...]:
        """Tools to advertise to ``role`` in ``tools/list`` (policy order)."""
        return tuple(tool for tool in self._policy.tools if self.check_tool(role, tool).allowed)

    def allows_action(self, role: str, action: ControlAction) -> bool:
        resolved = self._roles.get(role)
        return resolved is not None and action in resolved.actions

    def session_budget(self, role: str) -> SessionBudget:
        """Effective per-session limits: defaults with the role's own overrides applied."""
        role_policy = self._policy.roles.get(role)
        override = role_policy.budget if role_policy is not None else None
        return self._policy.budgets.session.merged(override)

    def _decide(self, role: str, tool: str) -> ToolDecision:
        spec = self._policy.tools[tool]
        verdict = deciding_rule(self._roles[role], tool, spec.access)
        if verdict is None:
            return ToolDecision(
                role=role, tool=tool, allowed=False, reason=DenyReason.NO_MATCHING_GRANT
            )
        outcome, rule = verdict
        if outcome == "deny":
            return ToolDecision(
                role=role, tool=tool, allowed=False, reason=DenyReason.EXPLICIT_DENY, rule=rule
            )
        allowed = ToolDecision(role=role, tool=tool, allowed=True, rule=rule)
        if not spec.requires_approval:
            return allowed
        defaults, override = self._policy.approvals, spec.approval
        approvers, timeout = defaults.approver_roles, defaults.timeout_seconds
        if override is not None:
            approvers = override.approver_roles or approvers
            timeout = override.timeout_seconds or timeout
        return replace(
            allowed,
            requires_approval=True,
            approver_roles=approvers,
            approval_timeout_seconds=timeout,
        )
