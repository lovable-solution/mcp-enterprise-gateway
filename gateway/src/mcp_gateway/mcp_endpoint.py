"""The gateway's own MCP endpoint (``/mcp``), the only door agents have.

* Stateless Streamable HTTP: any replica serves any request, and both 2025-era (handshake)
  and 2026-07-28 (per-request) clients are supported by the SDK.
* Responses stream as SSE, so a call held for approval can send progress notifications as
  keep-alives while it waits.
* ``MCPAuthMiddleware`` authenticates every HTTP request before the MCP layer runs. The
  principal travels to the handlers in ``request.state``: handlers run in the session
  manager's task group, not in the request's task, so a context variable would not reach
  them.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from http import HTTPStatus
from typing import Any, Final

from mcp import MCPError
from mcp.server import Server, ServerRequestContext
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import (
    INVALID_REQUEST,
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
)
from starlette.types import ASGIApp, Receive, Scope, Send

from . import __version__
from .auth.jwt import TokenError
from .auth.principal import Principal
from .errors import PROBLEM_MEDIA_TYPE, problem_body
from .runtime import GatewayRuntime
from .settings import GatewaySettings

INSTRUCTIONS: Final = (
    "Governed access to enterprise tools. Every call is authorised against policy, metered "
    "against your session budget and checked for loops. Some tools need human approval: "
    "such a call may return PENDING_APPROVAL with an approval id instead of a result; do "
    "not retry it."
)


class RuntimeHolder:
    """Late-bound access to the runtime, which exists only once the lifespan started."""

    def __init__(self) -> None:
        self._runtime: GatewayRuntime | None = None

    @property
    def runtime(self) -> GatewayRuntime:
        if self._runtime is None:
            raise RuntimeError("gateway runtime not started")
        return self._runtime

    @runtime.setter
    def runtime(self, value: GatewayRuntime | None) -> None:
        self._runtime = value


def build_mcp_server(holder: RuntimeHolder) -> Server[Any]:
    async def list_tools(
        ctx: ServerRequestContext[Any, Any], params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        tools = await holder.runtime.pipeline.list_tools(_principal(ctx))
        return ListToolsResult(tools=tools)  # private, uncached: the list is per caller

    async def call_tool(
        ctx: ServerRequestContext[Any, Any], params: CallToolRequestParams
    ) -> CallToolResult:
        principal = _principal(ctx)

        async def progress(elapsed: float, message: str) -> None:
            await ctx.session.report_progress(elapsed, None, message)

        return await holder.runtime.pipeline.call_tool(
            principal, params.name, params.arguments or {}, progress=progress
        )

    def tool_input_schema(name: str) -> dict[str, Any] | None:
        return holder.runtime.registry.input_schema(name)

    return Server(
        "mcp-enterprise-gateway",
        version=__version__,
        instructions=INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        get_tool_input_schema=tool_input_schema,
    )


def build_session_manager(
    server: Server[Any], settings: GatewaySettings
) -> StreamableHTTPSessionManager:
    security = None
    if settings.allowed_hosts:
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(settings.allowed_hosts),
            allowed_origins=list(settings.cors_origins),
        )
    return StreamableHTTPSessionManager(
        app=server, stateless=True, json_response=False, security_settings=security
    )


def _principal(ctx: ServerRequestContext[Any, Any]) -> Principal:
    principal = getattr(getattr(ctx.request, "state", None), "principal", None)
    if not isinstance(principal, Principal):  # unreachable behind MCPAuthMiddleware
        raise MCPError(INVALID_REQUEST, "unauthenticated request")
    return principal


class MCPAuthMiddleware:
    """Authenticates every request to the MCP endpoint (agent tokens must carry ``sid``)."""

    def __init__(self, app: ASGIApp, runtime: Callable[[], GatewayRuntime]) -> None:
        self._app = app
        self._runtime = runtime

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        token = _bearer(scope)
        if token is None:
            await _problem(
                send, 401, "Authentication required", "missing bearer token", "unauthorized"
            )
            return
        try:
            principal = await self._runtime().jwt.validate(token)
        except TokenError as exc:
            await _problem(send, 401, "Authentication required", str(exc), "unauthorized")
            return
        if principal.session_id is None:
            await _problem(
                send,
                403,
                "Not permitted",
                "agent tokens must carry a session id (sid claim)",
                "session_required",
            )
            return
        scope.setdefault("state", {})["principal"] = principal
        await self._app(scope, receive, send)


def _bearer(scope: Scope) -> str | None:
    for name, value in scope["headers"]:
        if name == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            token = token.strip()
            return token if scheme.lower() == "bearer" and token else None
    return None


async def _problem(send: Send, status: int, title: str, detail: str, code: str) -> None:
    headers = [(b"content-type", PROBLEM_MEDIA_TYPE.encode())]
    if status == HTTPStatus.UNAUTHORIZED:
        safe = detail.replace('"', "'")
        headers.append(
            (
                b"www-authenticate",
                f'Bearer error="invalid_token", error_description="{safe}"'.encode(),
            )
        )
    body = json.dumps(problem_body(status, title, detail, code)).encode()
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
