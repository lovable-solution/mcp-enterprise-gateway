"""Gateway identity assertions.

crm-ops never sees agent credentials. The gateway authenticates the agent, authorises the
call, then signs a short-lived assertion (HS256, ``aud`` = this server's name) carrying the
caller's tenant, subject, session and, for approval-gated calls, the approval id. Requests
to ``/mcp`` without a valid assertion are rejected before any MCP processing, so even a
client that reaches this server on the network cannot act without the gateway.
"""

from __future__ import annotations

import json
from typing import Any

from jose import jwt
from jose.exceptions import JWTError
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ConfigDict
from starlette.types import ASGIApp, Receive, Scope, Send

ASSERTION_ALGORITHM = "HS256"


class GatewayIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: str
    tenant_id: str | None
    session_id: str | None
    role: str | None
    approval_id: str | None
    token_id: str


class GatewayAssertionMiddleware:
    """Pure-ASGI guard for the MCP endpoint (other routes, e.g. /healthz, pass through)."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        key: str,
        issuer: str,
        audience: str,
        leeway_seconds: int,
        protected_path: str = "/mcp",
    ) -> None:
        self._app = app
        self._key = key
        self._issuer = issuer
        self._audience = audience
        self._leeway = leeway_seconds
        self._protected_path = protected_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(self._protected_path):
            await self._app(scope, receive, send)
            return
        identity = self._verify(_bearer_token(scope))
        if identity is None:
            await _reject(send)
            return
        scope.setdefault("state", {})["gateway_identity"] = identity
        await self._app(scope, receive, send)

    def _verify(self, token: str | None) -> GatewayIdentity | None:
        if not token:
            return None
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                self._key,
                algorithms=[ASSERTION_ALGORITHM],
                audience=self._audience,
                issuer=self._issuer,
                options={
                    "require_exp": True,
                    "require_iat": True,
                    "require_sub": True,
                    "require_jti": True,
                    "leeway": self._leeway,
                },
            )
        except JWTError:
            return None
        return GatewayIdentity(
            subject=str(claims["sub"]),
            tenant_id=claims.get("tenant_id"),
            session_id=claims.get("sid"),
            role=claims.get("role"),
            approval_id=claims.get("approval_id"),
            token_id=str(claims["jti"]),
        )


def tenant_identity(ctx: Context) -> tuple[GatewayIdentity, str]:
    """The verified gateway identity for this call and its tenant (required by every tool)."""
    request = ctx.request_context.request
    state = getattr(request, "state", None)
    identity = getattr(state, "gateway_identity", None)
    if not isinstance(identity, GatewayIdentity):
        raise ToolError("missing gateway identity")  # unreachable behind the middleware
    if not identity.tenant_id:
        raise ToolError("the gateway assertion carries no tenant")
    return identity, identity.tenant_id


def _bearer_token(scope: Scope) -> str | None:
    for name, value in scope["headers"]:
        if name == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            return token.strip() if scheme.lower() == "bearer" else None
    return None


async def _reject(send: Send) -> None:
    body = json.dumps({"error": "unauthorized", "detail": "missing or invalid gateway assertion"})
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b'Bearer realm="crm-ops"'),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body.encode()})
