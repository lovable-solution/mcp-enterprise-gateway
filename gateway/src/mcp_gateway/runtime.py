"""Assembles the gateway's components at start-up and tears them down at shutdown.

Start-up fails closed: an invalid policy, a policy that registers tools for a server with
no configured upstream, or unreachable Redis/PostgreSQL abort the process. Upstream MCP
servers may come up later; discovery retries in the background of each request.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field

import asyncpg
import httpx2
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from redis.asyncio import Redis

from platform_telemetry import Telemetry, configure_telemetry

from . import __version__
from .approvals.service import ApprovalService
from .approvals.store import ApprovalStore
from .auth.jwt import JWTValidator
from .db.migrate import apply_migrations
from .db.pool import create_pool
from .enforcement.breaker import CircuitBreaker
from .enforcement.budget import BudgetLedger
from .enforcement.keys import RedisKeys
from .enforcement.sessions import SessionDirectory
from .events.bus import EventBus
from .events.notifier import Notifier
from .events.webhooks import WebhookDispatcher
from .pipeline import ToolCallPipeline
from .policy.engine import PolicyEngine
from .policy.loader import LoadedPolicy, load_policy
from .policy.models import PolicyDocument, split_tool_name
from .settings import GatewaySettings
from .telemetry import Instruments
from .upstream.identity import AssertionSigner
from .upstream.registry import UpstreamRegistry

logger = logging.getLogger(__name__)


class ConfigurationError(RuntimeError):
    pass


@dataclass(eq=False)
class GatewayRuntime:
    settings: GatewaySettings
    policy: LoadedPolicy
    engine: PolicyEngine
    instruments: Instruments
    jwt: JWTValidator
    redis: Redis
    db: asyncpg.Pool
    bus: EventBus
    notifier: Notifier
    breaker: CircuitBreaker
    budget: BudgetLedger
    sessions: SessionDirectory
    registry: UpstreamRegistry
    approvals: ApprovalService
    pipeline: ToolCallPipeline
    http: httpx2.AsyncClient
    telemetry: Telemetry
    webhooks: WebhookDispatcher | None
    _tasks: list[asyncio.Task[None]] = field(default_factory=list)

    @classmethod
    async def start(cls, settings: GatewaySettings) -> GatewayRuntime:
        telemetry = configure_telemetry(
            "mcp-gateway", __version__, environment=settings.environment
        )
        instrumentor = AsyncPGInstrumentor()  # type: ignore[no-untyped-call]
        if not instrumentor.is_instrumented_by_opentelemetry:
            instrumentor.instrument()
        policy = load_policy(settings.policy_path)
        _check_upstream_coverage(policy.document, settings)
        engine = PolicyEngine(policy.document)
        instruments = Instruments.create()
        http = httpx2.AsyncClient(timeout=httpx2.Timeout(10.0))
        redis = Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password.get_secret_value()
            if settings.redis_password
            else None,
            ssl=settings.redis_tls,
            decode_responses=True,
            max_connections=settings.redis_pool_size,
            health_check_interval=30,
        )
        db: asyncpg.Pool | None = None
        try:
            await redis.ping()
            db = await create_pool(settings)
            if settings.database_auto_migrate:
                await apply_migrations(db)
            keys = RedisKeys(settings.redis_key_prefix)
            bus = EventBus(redis, keys, maxlen=settings.event_stream_maxlen)
            webhooks = None
            if settings.webhook_url and settings.webhook_hmac_key:
                webhooks = WebhookDispatcher(
                    settings.webhook_url,
                    settings.webhook_hmac_key.get_secret_value().encode(),
                    http,
                    max_attempts=settings.webhook_max_attempts,
                    timeout_seconds=settings.webhook_timeout_seconds,
                )
            notifier = Notifier(bus, webhooks)
            registry = UpstreamRegistry(
                upstreams=settings.upstreams,
                policy=policy.document,
                signer=AssertionSigner(
                    settings.upstream_signing_key.get_secret_value(),
                    ttl_seconds=settings.upstream_assertion_ttl_seconds,
                ),
                instruments=instruments,
                timeout_seconds=settings.upstream_timeout_seconds,
                catalog_ttl_seconds=settings.tool_catalog_ttl_seconds,
                strict_arguments=settings.strict_tool_arguments,
            )
            budget = BudgetLedger(redis, keys)
            sessions = SessionDirectory(redis, keys)
            breaker = CircuitBreaker(redis, keys)
            approvals = ApprovalService(
                store=ApprovalStore(db),
                bus=bus,
                notifier=notifier,
                registry=registry,
                budget=budget,
                policy=policy,
                engine=engine,
                instruments=instruments,
            )
            pipeline = ToolCallPipeline(
                policy=policy,
                engine=engine,
                registry=registry,
                breaker=breaker,
                budget=budget,
                sessions=sessions,
                approvals=approvals,
                notifier=notifier,
                instruments=instruments,
            )
        except BaseException:
            if db is not None:
                await db.close()
            await redis.aclose()
            await http.aclose()
            raise
        runtime = cls(
            settings=settings,
            policy=policy,
            engine=engine,
            instruments=instruments,
            jwt=JWTValidator(settings, http),
            redis=redis,
            db=db,
            bus=bus,
            notifier=notifier,
            breaker=breaker,
            budget=budget,
            sessions=sessions,
            registry=registry,
            approvals=approvals,
            pipeline=pipeline,
            http=http,
            telemetry=telemetry,
            webhooks=webhooks,
        )
        await registry.refresh(force=True)  # unreachable upstreams are logged and retried
        runtime._tasks.append(
            asyncio.create_task(runtime._sweep_approvals(), name="approval-sweeper")
        )
        logger.info(
            "gateway ready: policy %r (%s), upstreams %s",
            policy.document.metadata.name,
            policy.digest[:19],
            registry.health(),
        )
        return runtime

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self.webhooks is not None:
            await self.webhooks.aclose()
        await self.db.close()
        await self.redis.aclose()
        await self.http.aclose()
        self.telemetry.shutdown()

    async def _sweep_approvals(self) -> None:
        """Expire overdue approvals (any replica may run it; the UPDATE is idempotent)."""
        while True:
            try:
                await self.approvals.expire_due()
            except Exception:
                logger.exception("approval expiry sweep failed")
            await asyncio.sleep(self.settings.approval_sweep_interval_seconds)


def _check_upstream_coverage(policy: PolicyDocument, settings: GatewaySettings) -> None:
    servers = {split_tool_name(name)[0] for name in policy.tools}
    missing = sorted(servers - settings.upstreams.keys())
    if missing:
        msg = (
            f"the policy registers tools for {', '.join(missing)} but GATEWAY_UPSTREAMS has no "
            "endpoint for them"
        )
        raise ConfigurationError(msg)
