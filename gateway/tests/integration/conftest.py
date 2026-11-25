"""Integration fixtures: everything real except the agent.

* PostgreSQL and Redis from the local compose stack (``make up``); tests get a throwaway
  database and their own Redis key prefix, so they never touch development data.
* crm-ops and the gateway run as real uvicorn servers on ephemeral ports, and tests talk
  to them over HTTP with the official MCP client, exactly as an agent would.
* A local webhook receiver captures (and lets tests verify) signed deliveries.

If the stack is not running these tests fail (they do not skip): guardrail tests that
silently skip would report success for checks that never ran.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import asyncpg
import httpx2
import pytest
import uvicorn
import yaml
from jose import jwt
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import SecretStr
from redis.asyncio import Redis

from crm_ops.server import create_app as create_crm_ops
from crm_ops.settings import CrmOpsSettings
from crm_ops.store import CrmStore
from mcp_gateway.app import create_app as create_gateway
from mcp_gateway.settings import GatewaySettings

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]
JWT_SECRET = "jwt-" + "s" * 40
JWT_ISSUER = "https://idp.test.invalid/"
ASSERTION_KEY = "assertion-" + "k" * 40
WEBHOOK_KEY = "webhook-" + "w" * 40
INLINE_WAIT_SECONDS = 3


# --- local stack ------------------------------------------------------------------------
@dataclass(frozen=True)
class Stack:
    env: dict[str, str]

    @property
    def postgres_port(self) -> int:
        return int(self.env.get("POSTGRES_HOST_PORT", "15432"))

    @property
    def redis_port(self) -> int:
        return int(self.env.get("REDIS_HOST_PORT", "16379"))

    @property
    def gateway_db_user(self) -> str:
        return self.env.get("GATEWAY_DB_USER", "mcp_gateway")


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def _port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture(scope="session")
def stack() -> Stack:
    dotenv = REPO_ROOT / ".env"
    if not dotenv.exists():
        pytest.fail("no .env: run `make env && make up` before the integration tests")
    result = Stack(_read_dotenv(dotenv))
    for name, port in (("PostgreSQL", result.postgres_port), ("Redis", result.redis_port)):
        if not _port_open(port):
            pytest.fail(f"{name} is not reachable on 127.0.0.1:{port}: run `make up`")
    return result


@pytest.fixture(scope="session")
def test_database(stack: Stack) -> Iterator[str]:
    name = f"mcp_gateway_test_{uuid.uuid4().hex[:8]}"

    async def admin(sql: str) -> None:
        connection = await asyncpg.connect(
            host="127.0.0.1",
            port=stack.postgres_port,
            user=stack.env.get("POSTGRES_SUPERUSER", "postgres"),
            password=stack.env["POSTGRES_PASSWORD"],
            database="postgres",
        )
        try:
            await connection.execute(sql)
        finally:
            await connection.close()

    asyncio.run(admin(f'CREATE DATABASE "{name}" OWNER "{stack.gateway_db_user}"'))
    yield name
    asyncio.run(admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


# --- servers -----------------------------------------------------------------------------
def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


class ServerThread:
    """A uvicorn server on its own thread and event loop."""

    def __init__(self, app: Any) -> None:
        self.port = _free_port()
        config = uvicorn.Config(
            app, host="127.0.0.1", port=self.port, log_level="warning", lifespan="on"
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self._thread.start()
        deadline = time.monotonic() + 30
        while not self._server.started:
            if not self._thread.is_alive():
                pytest.fail("server failed to start (see captured logs)")
            if time.monotonic() > deadline:
                pytest.fail("server did not start within 30 s")
            time.sleep(0.05)

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=15)


@dataclass
class CrmOps:
    url: str
    store: CrmStore


@pytest.fixture(scope="session")
def crm_ops() -> Iterator[CrmOps]:
    app = create_crm_ops(CrmOpsSettings(gateway_assertion_key=SecretStr(ASSERTION_KEY)))
    server = ServerThread(app.asgi)
    server.start()
    yield CrmOps(url=f"{server.url}/mcp", store=app.store)
    server.stop()


@dataclass
class Delivery:
    headers: dict[str, str]
    body: bytes

    @property
    def event(self) -> dict[str, Any]:
        payload: dict[str, Any] = json.loads(self.body)
        return payload


@dataclass
class WebhookReceiver:
    url: str
    deliveries: list[Delivery] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def of_type(self, event_type: str) -> list[Delivery]:
        with self.lock:
            return [
                d for d in self.deliveries if d.headers.get("x-mcp-gateway-event") == event_type
            ]


@pytest.fixture(scope="session")
def webhook_receiver() -> Iterator[WebhookReceiver]:
    receiver = WebhookReceiver(url="")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            with receiver.lock:
                receiver.deliveries.append(
                    Delivery(headers={k.lower(): v for k, v in self.headers.items()}, body=body)
                )
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_args: object) -> None:  # silence
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    receiver.url = f"http://127.0.0.1:{server.server_address[1]}/hooks/approvals"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield receiver
    server.shutdown()
    server.server_close()


def _test_policy(path: Path) -> Path:
    """The shipped policy, plus a role with a tiny budget and a short inline wait."""
    data = yaml.safe_load((REPO_ROOT / "config" / "policies.yaml").read_text(encoding="utf-8"))
    data["approvals"]["inline_wait_seconds"] = INLINE_WAIT_SECONDS
    data["roles"]["capped"] = {
        "description": "Analyst with a two-call budget (tests).",
        "inherits": ["analyst"],
        "budget": {"max_tool_calls": 2},
    }
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


@dataclass
class Gateway:
    url: str
    settings: GatewaySettings
    redis_prefix: str


@pytest.fixture(scope="session")
def gateway(
    stack: Stack,
    test_database: str,
    crm_ops: CrmOps,
    webhook_receiver: WebhookReceiver,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Gateway]:
    prefix = f"mcpgw-test-{uuid.uuid4().hex[:8]}"
    settings = GatewaySettings(
        environment="test",
        policy_path=_test_policy(tmp_path_factory.mktemp("policy") / "policies.yaml"),
        database_host="127.0.0.1",
        database_port=stack.postgres_port,
        database_name=test_database,
        database_user=stack.gateway_db_user,
        database_password=SecretStr(stack.env["GATEWAY_DB_PASSWORD"]),
        database_ssl="disable",
        redis_host="127.0.0.1",
        redis_port=stack.redis_port,
        redis_password=SecretStr(stack.env["REDIS_PASSWORD"]),
        redis_key_prefix=prefix,
        upstreams={"crm-ops": crm_ops.url},
        upstream_signing_key=SecretStr(ASSERTION_KEY),
        jwt_issuer=JWT_ISSUER,
        jwt_algorithms=["HS256"],
        jwt_secret=SecretStr(JWT_SECRET),
        webhook_url=webhook_receiver.url,
        webhook_hmac_key=SecretStr(WEBHOOK_KEY),
        approval_sweep_interval_seconds=0.5,
    )
    server = ServerThread(create_gateway(settings))
    server.start()
    yield Gateway(url=server.url, settings=settings, redis_prefix=prefix)
    server.stop()

    async def cleanup() -> None:
        redis = Redis(port=stack.redis_port, password=stack.env["REDIS_PASSWORD"])
        async for key in redis.scan_iter(match=f"{prefix}:*", count=500):
            await redis.delete(key)
        await redis.aclose()

    asyncio.run(cleanup())


# --- clients -------------------------------------------------------------------------------
Mint = Callable[..., str]


@pytest.fixture(scope="session")
def mint() -> Mint:
    """``mint(role, tenant="acme", sub=None, sid=<fresh>, ttl=300, **claims)`` -> JWT."""

    def _mint(
        role: str,
        *,
        tenant: str = "acme",
        sub: str | None = None,
        sid: str | None = "fresh",
        ttl: int = 300,
        secret: str = JWT_SECRET,
        **claims: Any,
    ) -> str:
        now = int(time.time())
        payload: dict[str, Any] = {
            "iss": JWT_ISSUER,
            "aud": "mcp-gateway",
            "sub": sub or f"{role}-{uuid.uuid4().hex[:6]}",
            "tenant_id": tenant,
            "role": role,
            "iat": now,
            "exp": now + ttl,
            "jti": uuid.uuid4().hex,
        }
        if sid is not None:
            payload["sid"] = f"s-{uuid.uuid4().hex[:12]}" if sid == "fresh" else sid
        payload.update(claims)
        return jwt.encode(
            {k: v for k, v in payload.items() if v is not None}, secret, algorithm="HS256"
        )

    return _mint


McpClientFactory = Callable[[str], AbstractAsyncContextManager[Client]]


@pytest.fixture
def mcp_client(gateway: Gateway) -> McpClientFactory:
    """``async with mcp_client(token) as client`` -> MCP client connected through the gateway."""

    @asynccontextmanager
    async def connect(token: str) -> AsyncIterator[Client]:
        http = httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {token}"}, timeout=httpx2.Timeout(30.0, read=60.0)
        )
        transport = streamable_http_client(f"{gateway.url}/mcp", http_client=http)
        async with http, Client(transport, read_timeout_seconds=60.0, cache=None) as client:
            yield client

    return connect


@pytest.fixture
async def api(gateway: Gateway) -> AsyncIterator[httpx2.AsyncClient]:
    """Plain HTTP client for the control-plane API (pass ``headers=`` per request)."""
    async with httpx2.AsyncClient(base_url=gateway.url, timeout=30.0) as client:
        yield client


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def auth() -> Callable[[str], dict[str, str]]:
    return bearer


@pytest.fixture
async def gateway_db(
    stack: Stack, test_database: str
) -> AsyncIterator[asyncpg.Connection[asyncpg.Record]]:
    connection = await asyncpg.connect(
        host="127.0.0.1",
        port=stack.postgres_port,
        user=stack.gateway_db_user,
        password=stack.env["GATEWAY_DB_PASSWORD"],
        database=test_database,
    )
    for json_type in ("json", "jsonb"):  # decode like the gateway's own pool does
        await connection.set_type_codec(
            json_type, encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )
    yield connection
    await connection.close()


@pytest.fixture
async def stream_events(stack: Stack, gateway: Gateway) -> AsyncIterator[Callable[..., Any]]:
    """``await stream_events(tenant="acme")`` -> every event published in the tenant stream."""
    redis = Redis(
        port=stack.redis_port, password=stack.env["REDIS_PASSWORD"], decode_responses=True
    )

    async def read(tenant: str = "acme") -> list[dict[str, Any]]:
        entries: list[tuple[str, dict[str, str]]] = await redis.xrange(  # type: ignore[assignment]
            f"{gateway.redis_prefix}:{{{tenant}}}:events"
        )  # decode_responses=True: ids and fields are str
        return [json.loads(fields["event"]) | {"id": entry_id} for entry_id, fields in entries]

    yield read
    await redis.aclose()
