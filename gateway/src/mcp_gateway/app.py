"""FastAPI application: the MCP endpoint for agents and the control-plane API.

/mcp                                   MCP Streamable HTTP (agents; JWT with sid)
/v1/approvals[/{id}[/decide]]          approval queue (operators) and lookup (agents)
/v1/sessions[/{sid}[/usage|/circuit-breaker/reset]]
/v1/events                             server-sent events (operator console)
/v1/policy                             loaded policy and access matrix
/healthz, /readyz                      probes
"""

from __future__ import annotations

from collections.abc import AsyncIterator, MutableMapping
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
from starlette.routing import Route

from . import __version__
from .api import approvals, events, health, policy, sessions
from .errors import install_problem_handlers
from .mcp_endpoint import MCPAuthMiddleware, RuntimeHolder, build_mcp_server, build_session_manager
from .runtime import GatewayRuntime
from .settings import GatewaySettings

_UNTRACED_PATHS = frozenset({"/healthz", "/readyz"})


def _exclude_probes(scope: MutableMapping[str, Any]) -> bool:
    return scope.get("path") in _UNTRACED_PATHS


def create_app(settings: GatewaySettings | None = None) -> FastAPI:
    settings = settings or GatewaySettings()
    holder = RuntimeHolder()
    session_manager = build_session_manager(build_mcp_server(holder), settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime = await GatewayRuntime.start(settings)
        holder.runtime = runtime
        app.state.runtime = runtime
        try:
            async with session_manager.run():
                yield
        finally:
            holder.runtime = None
            await runtime.close()

    app = FastAPI(
        title="MCP Enterprise Gateway",
        version=__version__,
        summary="Policy enforcement point between AI agents and enterprise MCP servers.",
        lifespan=lifespan,
        telemetry={"exclude": _exclude_probes},
    )
    install_problem_handlers(app)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
        )
    for module in (health, approvals, sessions, events, policy):
        app.include_router(module.router)
    # An exact route (not a Mount): POST /mcp must not be redirected to /mcp/.
    app.router.routes.append(
        Route(
            "/mcp",
            endpoint=MCPAuthMiddleware(
                StreamableHTTPASGIApp(session_manager), lambda: holder.runtime
            ),
        )
    )
    return app
