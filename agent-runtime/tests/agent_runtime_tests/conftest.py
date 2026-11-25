"""Agent-runtime fixtures: a real gateway and crm-ops, a scripted model.

Integration tests run the gateway and crm-ops in-process (uvicorn on ephemeral ports)
against the local stack's PostgreSQL and Redis, with a throwaway gateway database, a
private Redis key prefix and a test policy whose inline approval wait is short. Agent
checkpoints go to the stack's real ``agent_runtime`` database, so a test can resume a
thread from a second runtime instance exactly as a restarted process would.

The model is PydanticAI's ``FunctionModel`` driven by a script: deterministic, free, and
exercising the same agent, toolset, usage reporting and graph code as Claude does.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg
import httpx2
import pytest
import uvicorn
import yaml
from jose import jwt
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr
from pydantic_ai import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from redis.asyncio import Redis

from agent_runtime.agent import build_agent
from agent_runtime.identity import DevTokenMinter
from agent_runtime.runner import AgentRuntime
from agent_runtime.toolset import RuntimeEvent
from crm_ops.server import create_app as create_crm_ops
from crm_ops.settings import CrmOpsSettings
from crm_ops.store import CrmStore
from mcp_gateway.app import create_app as create_gateway
from mcp_gateway.settings import GatewaySettings

REPO_ROOT = Path(__file__).resolve().parents[3]
JWT_SECRET = "agent-tests-jwt-" + "s" * 40
JWT_ISSUER = "https://idp.agent-tests.invalid/"
ASSERTION_KEY = "agent-tests-assertion-" + "k" * 40
INLINE_WAIT_SECONDS = 2


# --- local stack ------------------------------------------------------------------------
def _dotenv() -> dict[str, str]:
    path = REPO_ROOT / ".env"
    if not path.exists():
        pytest.fail("no .env: run `make env && make up` before the integration tests")
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


@dataclass(frozen=True)
class Stack:
    env: dict[str, str]

    @property
    def postgres_port(self) -> int:
        return int(self.env.get("POSTGRES_HOST_PORT", "15432"))

    @property
    def redis_port(self) -> int:
        return int(self.env.get("REDIS_HOST_PORT", "16379"))


@pytest.fixture(scope="session")
def stack() -> Stack:
    result = Stack(_dotenv())
    for name, port in (("PostgreSQL", result.postgres_port), ("Redis", result.redis_port)):
        with socket.socket() as sock:
            sock.settimeout(1)
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                pytest.fail(f"{name} is not reachable on 127.0.0.1:{port}: run `make up`")
    return result


class ServerThread:
    """A uvicorn server on its own thread and event loop."""

    def __init__(self, app: Any) -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port: int = sock.getsockname()[1]
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
            if not self._thread.is_alive() or time.monotonic() > deadline:
                pytest.fail("server failed to start (see captured logs)")
            time.sleep(0.05)

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=15)


@pytest.fixture(scope="session")
def gateway_database(stack: Stack) -> Iterator[str]:
    name = f"mcp_gateway_agent_test_{uuid.uuid4().hex[:8]}"
    owner = stack.env.get("GATEWAY_DB_USER", "mcp_gateway")

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

    asyncio.run(admin(f'CREATE DATABASE "{name}" OWNER "{owner}"'))
    yield name
    asyncio.run(admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


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


def _test_policy(path: Path) -> Path:
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


@pytest.fixture(scope="session")
def gateway(
    stack: Stack,
    gateway_database: str,
    crm_ops: CrmOps,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Gateway]:
    prefix = f"mcpgw-agent-{uuid.uuid4().hex[:8]}"
    settings = GatewaySettings(
        environment="test",
        policy_path=_test_policy(tmp_path_factory.mktemp("policy") / "policies.yaml"),
        database_host="127.0.0.1",
        database_port=stack.postgres_port,
        database_name=gateway_database,
        database_user=stack.env.get("GATEWAY_DB_USER", "mcp_gateway"),
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
        approval_sweep_interval_seconds=0.5,
    )
    server = ServerThread(create_gateway(settings))
    server.start()
    yield Gateway(url=server.url)
    server.stop()

    async def cleanup() -> None:
        redis = Redis(port=stack.redis_port, password=stack.env["REDIS_PASSWORD"])
        async for key in redis.scan_iter(match=f"{prefix}:*", count=500):
            await redis.delete(key)
        await redis.aclose()

    asyncio.run(cleanup())


# --- people and agents ---------------------------------------------------------------------
def operator_token(subject: str = "ops-test") -> str:
    now = int(time.time())
    claims = {
        "iss": JWT_ISSUER,
        "aud": "mcp-gateway",
        "sub": subject,
        "tenant_id": "acme",
        "role": "operator",
        "iat": now,
        "exp": now + 600,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, JWT_SECRET, algorithm="HS256")


@pytest.fixture
async def operator(gateway: Gateway) -> AsyncIterator[httpx2.AsyncClient]:
    """The control-plane API as a finance-ops reviewer."""
    async with httpx2.AsyncClient(
        base_url=gateway.url,
        headers={"Authorization": f"Bearer {operator_token()}"},
        timeout=30.0,
    ) as client:
        yield client


def minter(role: str = "admin", subject: str = "crm-copilot-test") -> DevTokenMinter:
    return DevTokenMinter(
        secret=SecretStr(JWT_SECRET),
        issuer=JWT_ISSUER,
        audience="mcp-gateway",
        tenant_id="acme",
        subject=subject,
        role=role,
        ttl_seconds=600,
    )


@dataclass
class Checkpoints:
    """Fresh connections to the stack's ``agent_runtime`` checkpoint database: each
    ``open()`` is like a new process. Threads registered with ``track`` are deleted after
    the test."""

    conninfo: str
    threads: list[str]

    def open(self) -> AbstractAsyncContextManager[AsyncPostgresSaver]:
        return AsyncPostgresSaver.from_conn_string(self.conninfo)

    def track(self, thread_id: str) -> str:
        self.threads.append(thread_id)
        return thread_id


@pytest.fixture
async def checkpoints(stack: Stack) -> AsyncIterator[Checkpoints]:
    conninfo = make_conninfo(
        host="127.0.0.1",
        port=stack.postgres_port,
        dbname=stack.env.get("AGENT_DB_NAME", "agent_runtime"),
        user=stack.env.get("AGENT_DB_USER", "agent_runtime"),
        password=stack.env["AGENT_DB_PASSWORD"],
        sslmode="disable",
    )
    result = Checkpoints(conninfo=conninfo, threads=[])
    async with result.open() as saver:
        await saver.setup()
    yield result
    async with result.open() as saver:
        for thread_id in result.threads:
            await saver.adelete_thread(thread_id)


# --- the scripted model -----------------------------------------------------------------------
Step = tuple[str, str, dict[str, Any]] | tuple[str, str]
"""("tool", name, arguments) or ("text", answer)."""


class Script:
    """Decides the model's next move from the conversation so far; records what it saw."""

    def __init__(self, decide: Callable[[list[ModelMessage]], Step]) -> None:
        self._decide = decide
        self.seen: list[list[ModelMessage]] = []

    def model(self) -> FunctionModel:
        async def stream(
            messages: list[ModelMessage], _info: AgentInfo
        ) -> AsyncIterator[str | DeltaToolCalls]:
            self.seen.append(list(messages))
            step = self._decide(messages)
            if step[0] == "tool":
                _, name, arguments = step  # type: ignore[misc]
                yield {
                    0: DeltaToolCall(
                        name=name,
                        json_args=json.dumps(arguments),
                        tool_call_id=f"call-{uuid.uuid4().hex[:8]}",
                    )
                }
            else:
                yield step[1]

        def request(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
            self.seen.append(list(messages))
            step = self._decide(messages)
            if step[0] == "tool":
                _, name, arguments = step  # type: ignore[misc]
                return ModelResponse(parts=[ToolCallPart(name, arguments)])
            return ModelResponse(parts=[TextPart(step[1])])

        return FunctionModel(request, stream_function=stream, model_name="scripted-model")


def tool_results(messages: list[ModelMessage]) -> list[Any]:
    """Every tool result the model has been given so far, oldest first."""
    return [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if part.part_kind in {"tool-return", "retry-prompt"}
    ]


@pytest.fixture
def events() -> list[RuntimeEvent]:
    return []


@pytest.fixture
def make_runtime(gateway: Gateway, events: list[RuntimeEvent]) -> Callable[..., AgentRuntime]:
    def make(script: Script, checkpointer: Any, *, role: str = "admin") -> AgentRuntime:
        async def record(event: RuntimeEvent) -> None:
            events.append(event)

        return AgentRuntime(
            agent=build_agent(script.model()),
            checkpointer=checkpointer,
            tokens=minter(role),
            gateway_url=gateway.url,
            gateway_timeout_seconds=30.0,
            approval_poll_seconds=0.2,
            emit=record,
        )

    return make
