"""crm-ops MCP server: three CRM tools behind the gateway's identity assertion.

Served over stateless Streamable HTTP with JSON responses: every request is independent,
so any replica can serve any call and the gateway needs no session affinity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from opentelemetry import metrics, trace
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp

from platform_telemetry import configure_telemetry

from . import __version__
from .identity import GatewayAssertionMiddleware, tenant_identity
from .models import (
    ACCOUNT_ID_PATTERN,
    AccountMetrics,
    CreditLimitChange,
    SearchAccountsResult,
    Segment,
)
from .settings import CrmOpsSettings
from .store import AccountNotFoundError, CreditPolicyError, CrmStore

AccountIdArg = Annotated[
    str, Field(pattern=ACCOUNT_ID_PATTERN, description="Account id, for example ACC-1001.")
]


def build_server(store: CrmStore) -> MCPServer:
    server = MCPServer(
        "crm-ops",
        version=__version__,
        instructions=(
            "CRM accounts of the calling tenant: search, receivables metrics and "
            "credit-limit changes."
        ),
    )
    limit_changes = metrics.get_meter("crm_ops").create_counter(
        "crm_ops.credit_limit.changes", unit="{change}", description="Applied credit-limit changes"
    )

    @server.tool(
        name="search_accounts",
        annotations=ToolAnnotations(
            title="Search accounts",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def search_accounts(
        ctx: Context,
        query: Annotated[
            str,
            Field(
                min_length=1,
                max_length=100,
                description="Case-insensitive text matched against id, name, industry and owner.",
            ),
        ],
        segment: Annotated[
            Segment | None, Field(description="Only return accounts in this segment.")
        ] = None,
        limit: Annotated[int, Field(ge=1, le=25, description="Maximum accounts to return.")] = 10,
    ) -> SearchAccountsResult:
        """Search the calling tenant's CRM accounts."""
        _, tenant = tenant_identity(ctx)
        trace.get_current_span().set_attribute("crm_ops.tenant.id", tenant)
        return store.search(tenant, query, segment, limit)

    @server.tool(
        name="get_account_metrics",
        annotations=ToolAnnotations(
            title="Get account metrics",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def get_account_metrics(ctx: Context, account_id: AccountIdArg) -> AccountMetrics:
        """Revenue, open receivables with ageing, credit utilisation and DSO for one account."""
        _, tenant = tenant_identity(ctx)
        trace.get_current_span().set_attribute("crm_ops.tenant.id", tenant)
        try:
            return store.metrics(tenant, account_id)
        except AccountNotFoundError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(
        name="update_credit_limit",
        annotations=ToolAnnotations(
            title="Update credit limit",
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    async def update_credit_limit(
        ctx: Context,
        account_id: AccountIdArg,
        new_limit_usd: Annotated[
            int, Field(ge=0, le=10_000_000, description="New credit limit in whole US dollars.")
        ],
        reason: Annotated[
            str,
            Field(
                min_length=10,
                max_length=500,
                description="Business justification, recorded with the change.",
            ),
        ],
    ) -> CreditLimitChange:
        """Set a new credit limit for one account."""
        identity, tenant = tenant_identity(ctx)
        span = trace.get_current_span()
        span.set_attribute("crm_ops.tenant.id", tenant)
        span.set_attribute("crm_ops.account.id", account_id)
        try:
            change = await store.update_credit_limit(
                tenant,
                account_id,
                new_limit_usd,
                reason=reason,
                changed_by=identity.subject,
                approval_id=identity.approval_id,
            )
        except (AccountNotFoundError, CreditPolicyError) as exc:
            raise ToolError(str(exc)) from exc
        limit_changes.add(1, {"crm_ops.tenant.id": tenant})
        return change

    return server


async def healthz(_request: Request) -> Response:
    return JSONResponse({"status": "ok"})


@dataclass(frozen=True, slots=True)
class CrmOpsApp:
    asgi: ASGIApp
    server: MCPServer
    store: CrmStore


def create_app(
    settings: CrmOpsSettings | None = None, *, store: CrmStore | None = None
) -> CrmOpsApp:
    settings = settings or CrmOpsSettings()
    configure_telemetry("crm-ops", __version__, environment=settings.environment)
    store = store or CrmStore.seeded()
    server = build_server(store)
    starlette_app = server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(settings.allowed_hosts),
            allowed_origins=[],
        ),
    )
    starlette_app.router.routes.append(Route("/healthz", healthz, methods=["GET"]))
    asgi = GatewayAssertionMiddleware(
        starlette_app,
        key=settings.gateway_assertion_key.get_secret_value(),
        issuer=settings.gateway_issuer,
        audience=settings.audience,
        leeway_seconds=settings.assertion_leeway_seconds,
    )
    return CrmOpsApp(asgi=asgi, server=server, store=store)
