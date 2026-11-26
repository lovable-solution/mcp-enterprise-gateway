"""Demo traffic for the operator console (local development only).

Drives a few synthetic agent sessions through the real local gateway, so the console has
live traffic to show without calling a model:

* allowed tool calls and model-usage reports, which raise each session's spend;
* one call the agent's role may not make, which the gateway denies;
* one credit-limit change, which the gateway holds for human approval;
* one agent repeating an identical call, which trips its circuit breaker.

    make demo-traffic                     # about 90 seconds
    uv run python scripts/demo_traffic.py --seconds 180

Every subject starts with "demo-", so this traffic is easy to tell from real agents. The
held call waits in the approval queue like any other and expires unexecuted if nobody
decides it. Model usage is only reported to the gateway's ledger: no model is called and
nothing is billed. Needs the local stack (make up) and the development JWT secret that
make env writes to .env; production gateways verify identity-provider tokens instead, so
this script cannot mint tokens for them.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import random
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from mcp_gateway.devtools.mint_token import mint

TENANT = "acme"
MODEL = "claude-opus-5-5"
SEARCH = "crm-ops__search_accounts"
METRICS = "crm-ops__get_account_metrics"
UPDATE = "crm-ops__update_credit_limit"
ACCOUNTS = ("ACC-1001", "ACC-1002", "ACC-1003", "ACC-1004", "ACC-1005")
# Drop idle connections well before the gateway's own 5 s keep-alive timeout, so a call is
# never sent on a connection the server is closing at that moment.
KEEPALIVE = httpx2.Limits(keepalive_expiry=2.0)
QUERIES = ("freight", "energy", "clinic", "analytics", "foods")


class DevSecrets(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GATEWAY_", env_file=".env", extra="ignore")

    jwt_secret: SecretStr
    jwt_issuer: str = "http://localhost/dev-idp"
    jwt_audience: str = "mcp-gateway"


def log(subject: str, message: str) -> None:
    print(f"{time.strftime('%H:%M:%S')}  {subject:<18} {message}", flush=True)


class Agent:
    def __init__(self, secrets: DevSecrets, gateway: str, subject: str, role: str) -> None:
        self.subject = subject
        self.gateway = gateway
        self.session_id = f"demo-{uuid.uuid4().hex[:12]}"
        self.token = mint(
            secret=secrets.jwt_secret.get_secret_value(),
            issuer=secrets.jwt_issuer,
            audience=secrets.jwt_audience,
            role=role,
            tenant=TENANT,
            subject=subject,
            session_id=self.session_id,
            ttl_seconds=3600,
        )

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[Client]:
        http = httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=httpx2.Timeout(30.0, read=60.0),
            limits=KEEPALIVE,
        )
        transport = streamable_http_client(f"{self.gateway}/mcp", http_client=http)
        async with http, Client(transport, read_timeout_seconds=60.0, cache=None) as client:
            yield client

    async def call(self, client: Client, tool: str, arguments: dict[str, Any]) -> None:
        short = tool.rsplit("__", maxsplit=1)[-1]
        try:
            result = await client.call_tool(tool, arguments)
        except Exception as exc:  # the gateway's refusals arrive as protocol errors
            log(self.subject, f"{short}: refused ({type(exc).__name__}: {str(exc)[:80]})")
            return
        text = " ".join(" ".join(getattr(block, "text", "") for block in result.content).split())
        log(self.subject, f"{short}: {'tool error' if result.is_error else 'ok'} {text[:60]}")

    async def report_usage(self, api: httpx2.AsyncClient) -> None:
        response = await api.post(
            f"/v1/sessions/{self.session_id}/usage",
            headers={"Authorization": f"Bearer {self.token}"},
            json={
                "model": MODEL,
                "input_tokens": random.randint(1_400, 4_200),
                "output_tokens": random.randint(120, 620),
                "cache_read_input_tokens": random.choice((0, 0, 2_048, 3_072)),
            },
        )
        log(self.subject, f"model usage reported ({response.status_code})")


async def reader(
    agent: Agent,
    api: httpx2.AsyncClient,
    until: float,
    *,
    offset: int,
    deny_at: float | None = None,
) -> None:
    """Searches and reads metrics; optionally tries one write its role does not allow.

    Arguments rotate (never random), so no call repeats often enough to look like a loop:
    the gateway's breaker trips on the 4th identical call within 60 s, and that is the
    looper's job alone."""
    searches = itertools.cycle(
        [{"query": query, "limit": limit} for limit in (5, 10, 20) for query in QUERIES]
    )
    accounts = itertools.cycle(ACCOUNTS[offset:] + ACCOUNTS[:offset])
    async with agent.connect() as client:
        while time.monotonic() < until:
            await agent.report_usage(api)
            await agent.call(client, SEARCH, next(searches))
            await asyncio.sleep(random.uniform(2.5, 5.0))
            await agent.call(client, METRICS, {"account_id": next(accounts)})
            if deny_at is not None and time.monotonic() >= deny_at:
                deny_at = None
                await agent.call(
                    client,
                    UPDATE,
                    {
                        "account_id": "ACC-1002",
                        "new_limit_usd": 400_000,
                        "reason": "demo: not allowed",
                    },
                )
            await asyncio.sleep(random.uniform(2.5, 5.0))


async def credit_agent(agent: Agent, api: httpx2.AsyncClient, until: float, hold_at: float) -> None:
    """Reviews accounts in turn, then asks for a credit-limit change that waits for a human."""
    accounts = itertools.cycle(("ACC-1003", "ACC-1001", "ACC-1005", "ACC-1002", "ACC-1004"))
    async with agent.connect() as client:
        held = False
        while time.monotonic() < until:
            await agent.report_usage(api)
            await agent.call(client, METRICS, {"account_id": next(accounts)})
            if not held and time.monotonic() >= hold_at:
                held = True
                log(
                    agent.subject,
                    "update_credit_limit: asking (held for approval until the inline wait ends)",
                )
                await agent.call(
                    client,
                    UPDATE,
                    {
                        "account_id": "ACC-1003",
                        "new_limit_usd": 90_000,
                        "reason": "Demo traffic: Cedar Analytics uses 93% of its $75,000 limit; "
                        "raise to $90,000 for the renewal quarter.",
                    },
                )
            await asyncio.sleep(random.uniform(5.0, 8.0))


async def looper(agent: Agent, start_at: float) -> None:
    """Repeats one identical call until the loop breaker stops the session."""
    await asyncio.sleep(max(0.0, start_at - time.monotonic()))
    async with agent.connect() as client:
        for _ in range(4):
            await agent.call(client, METRICS, {"account_id": "ACC-1002"})
            await asyncio.sleep(0.6)


async def main(gateway: str, seconds: int) -> None:
    secrets = DevSecrets()  # read from the environment or .env
    start = time.monotonic()
    until = start + seconds
    recon = Agent(secrets, gateway, "demo-recon", "analyst")
    forecast = Agent(secrets, gateway, "demo-forecast", "analyst")
    credit = Agent(secrets, gateway, "demo-credit", "admin")
    loop = Agent(secrets, gateway, "demo-looper", "analyst")
    print(f"Demo traffic for {seconds}s against {gateway} (tenant {TENANT}). Ctrl+C stops it.")
    async with httpx2.AsyncClient(base_url=gateway, timeout=30.0, limits=KEEPALIVE) as api:
        # One agent failing (say, the gateway restarting) must not stop the others.
        outcomes = await asyncio.gather(
            reader(recon, api, until, offset=0, deny_at=start + seconds * 0.3),
            reader(forecast, api, until, offset=2),
            credit_agent(credit, api, until, hold_at=start + 8),
            looper(loop, start + seconds * 0.45),
            return_exceptions=True,
        )
    for agent, outcome in zip((recon, forecast, credit, loop), outcomes, strict=True):
        if isinstance(outcome, BaseException):
            log(agent.subject, f"stopped early: {type(outcome).__name__}: {outcome}")
    print("Done. The held demo call waits in the approval queue until it is decided or expires.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--gateway", default="http://127.0.0.1:8000", help="gateway base URL")
    parser.add_argument("--seconds", type=int, default=90, help="how long to run (default 90)")
    args = parser.parse_args()
    try:
        asyncio.run(main(args.gateway.rstrip("/"), max(20, args.seconds)))
    except KeyboardInterrupt:
        print("\nStopped.")
