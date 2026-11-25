"""FastAPI dependencies: runtime access, authentication, control-plane authorisation."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..auth.jwt import TokenError
from ..auth.principal import Principal
from ..errors import Forbidden, Unauthorized
from ..policy.models import ControlAction
from ..runtime import GatewayRuntime

_bearer = HTTPBearer(auto_error=False, description="Agent or operator JWT issued by the IdP")


def runtime_of(request: Request) -> GatewayRuntime:
    runtime: GatewayRuntime = request.app.state.runtime
    return runtime


RuntimeDep = Annotated[GatewayRuntime, Depends(runtime_of)]


async def current_principal(
    runtime: RuntimeDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer)],
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise Unauthorized("missing bearer token")
    try:
        return await runtime.jwt.validate(credentials.credentials)
    except TokenError as exc:
        raise Unauthorized(str(exc)) from exc


PrincipalDep = Annotated[Principal, Depends(current_principal)]


def require(action: ControlAction) -> Any:
    """``Annotated[Principal, require(...)]``: authenticated and allowed ``action`` by policy."""

    async def dependency(principal: PrincipalDep, runtime: RuntimeDep) -> Principal:
        if not runtime.engine.allows_action(principal.role, action):
            raise Forbidden(f"role {principal.role!r} may not perform {action.value!r}")
        return principal

    return Depends(dependency)
