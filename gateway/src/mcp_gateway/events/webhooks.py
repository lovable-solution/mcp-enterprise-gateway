"""Signed webhook deliveries (approval queue and safety alerts).

Each delivery is ``POST <GATEWAY_WEBHOOK_URL>`` with the event as JSON and the headers

    X-MCP-Gateway-Event:      approval.requested
    X-MCP-Gateway-Delivery:   <uuid, stable across retries>
    X-MCP-Gateway-Signature:  t=<unix seconds>,v1=<hex HMAC-SHA256(key, "<t>.<body>")>

Receivers verify the HMAC over the timestamp and the raw body, and reject stale
timestamps to stop replays. Deliveries run in the background with exponential backoff, so
a slow receiver never delays a tool call or an approval decision.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
from typing import Final
from uuid import uuid4

import httpx2

from .bus import GatewayEvent

logger = logging.getLogger(__name__)

SIGNATURE_HEADER: Final = "X-MCP-Gateway-Signature"
EVENT_HEADER: Final = "X-MCP-Gateway-Event"
DELIVERY_HEADER: Final = "X-MCP-Gateway-Delivery"

WEBHOOK_EVENTS: Final[frozenset[str]] = frozenset(
    {
        "approval.requested",
        "approval.approved",
        "approval.rejected",
        "approval.executed",
        "approval.failed",
        "approval.expired",
        "circuit_breaker.tripped",
        "budget.exhausted",
    }
)


def sign(key: bytes, timestamp: int, body: bytes) -> str:
    digest = hmac.new(key, f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def verify(key: bytes, header: str, body: bytes, *, tolerance_seconds: int = 300) -> bool:
    """Reference receiver-side check (used by the tests)."""
    parts = dict(part.split("=", 1) for part in header.split(",") if "=" in part)
    try:
        timestamp = int(parts["t"])
    except (KeyError, ValueError):
        return False
    if abs(time.time() - timestamp) > tolerance_seconds:
        return False
    expected = sign(key, timestamp, body)
    return hmac.compare_digest(expected, header)


class WebhookDispatcher:
    def __init__(
        self,
        url: str,
        key: bytes,
        http_client: httpx2.AsyncClient,
        *,
        max_attempts: int,
        timeout_seconds: float,
    ) -> None:
        self._url = url
        self._key = key
        self._http = http_client
        self._max_attempts = max_attempts
        self._timeout = timeout_seconds
        self._pending: set[asyncio.Task[None]] = set()

    def submit(self, event: GatewayEvent) -> None:
        if event.type not in WEBHOOK_EVENTS:
            return
        task = asyncio.create_task(self._deliver(event), name=f"webhook:{event.type}")
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def aclose(self, timeout_seconds: float = 5.0) -> None:
        if self._pending:
            await asyncio.wait(set(self._pending), timeout=timeout_seconds)
        for task in set(self._pending):
            task.cancel()

    async def _deliver(self, event: GatewayEvent) -> None:
        body = event.model_dump_json().encode()
        delivery = str(uuid4())
        for attempt in range(1, self._max_attempts + 1):
            headers = {
                "Content-Type": "application/json",
                EVENT_HEADER: event.type,
                DELIVERY_HEADER: delivery,
                SIGNATURE_HEADER: sign(self._key, int(time.time()), body),
            }
            try:
                response = await self._http.post(
                    self._url, content=body, headers=headers, timeout=self._timeout
                )
                if response.is_success:
                    return
                problem = f"HTTP {response.status_code}"
            except httpx2.HTTPError as exc:
                problem = type(exc).__name__
            if attempt < self._max_attempts:
                await asyncio.sleep(0.5 * 2 ** (attempt - 1))
        logger.warning(
            "webhook %s for %s failed after %d attempts: %s",
            delivery,
            event.type,
            self._max_attempts,
            problem,
        )
