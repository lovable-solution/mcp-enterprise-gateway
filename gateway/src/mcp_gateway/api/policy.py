"""Read-only view of the loaded policy (operator console)."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import BaseModel

from ..auth.principal import Principal
from ..policy.models import ControlAction
from .deps import RuntimeDep, require

router = APIRouter(tags=["policy"])

Access = Literal["allow", "approval", "deny"]


class ToolSummary(BaseModel):
    name: str
    access: str
    risk: str
    requires_approval: bool
    approver_roles: list[str]
    cost_usd_per_call: Decimal
    available: bool
    """Whether the upstream server currently offers the tool."""


class RoleSummary(BaseModel):
    name: str
    description: str
    actions: list[str]


class PolicySummary(BaseModel):
    name: str
    digest: str
    roles: list[RoleSummary]
    tools: list[ToolSummary]
    matrix: dict[str, dict[str, Access]]
    upstreams: dict[str, str]


@router.get("/v1/policy", summary="The loaded policy and its effective access matrix")
async def get_policy(
    runtime: RuntimeDep, _principal: Annotated[Principal, require(ControlAction.POLICY_READ)]
) -> PolicySummary:
    document, engine = runtime.policy.document, runtime.engine
    available = runtime.registry.cached_catalog()
    matrix: dict[str, dict[str, Access]] = {}
    for role in document.roles:
        row: dict[str, Access] = {}
        for tool in document.tools:
            decision = engine.check_tool(role, tool)
            row[tool] = (
                "deny"
                if not decision.allowed
                else "approval"
                if decision.requires_approval
                else "allow"
            )
        matrix[role] = row
    tools = []
    for name, spec in document.tools.items():
        approvers: list[str] = []
        if spec.requires_approval:
            override = spec.approval.approver_roles if spec.approval else None
            approvers = list(override or document.approvals.approver_roles)
        tools.append(
            ToolSummary(
                name=name,
                access=spec.access.value,
                risk=spec.risk.value,
                requires_approval=spec.requires_approval,
                approver_roles=approvers,
                cost_usd_per_call=spec.cost_usd_per_call,
                available=name in available,
            )
        )
    roles = [
        RoleSummary(
            name=name, description=role.description, actions=sorted(engine_actions(runtime, name))
        )
        for name, role in document.roles.items()
    ]
    return PolicySummary(
        name=document.metadata.name,
        digest=runtime.policy.digest,
        roles=roles,
        tools=tools,
        matrix=matrix,
        upstreams=runtime.registry.health(),
    )


def engine_actions(runtime: RuntimeDep, role: str) -> list[str]:
    return [action.value for action in ControlAction if runtime.engine.allows_action(role, action)]
