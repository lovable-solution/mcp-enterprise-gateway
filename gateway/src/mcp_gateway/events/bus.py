"""Tenant-scoped event bus on Redis Streams.

A stream (not pub/sub) so that server-sent-event clients can reconnect with
``Last-Event-ID`` and miss nothing, and so waiters can start reading from a known
position before checking state (no lost-wakeup race). Streams are capped (``MAXLEN ~``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis

from ..enforcement.keys import RedisKeys

EventType = Literal[
    "approval.requested",
    "approval.approved",
    "approval.rejected",
    "approval.executed",
    "approval.failed",
    "approval.expired",
    "circuit_breaker.tripped",
    "circuit_breaker.reset",
    "budget.warning",
    "budget.exhausted",
    "tool_call.denied",
    "session.activity",
]

APPROVAL_EVENTS: Final[frozenset[str]] = frozenset(
    t for t in get_args(EventType) if t.startswith("approval.")
)
STREAM_START: Final = "0-0"


class GatewayEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str | None = None
    """Stream entry id, assigned when published; doubles as the SSE event id."""
    type: EventType
    tenant_id: str
    session_id: str | None = None
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    data: dict[str, Any] = Field(default_factory=dict)


class EventBus:
    def __init__(self, redis: Redis, keys: RedisKeys, *, maxlen: int) -> None:
        self._redis = redis
        self._keys = keys
        self._maxlen = maxlen

    async def publish(self, event: GatewayEvent) -> GatewayEvent:
        payload = event.model_dump_json(exclude={"id"})
        entry_id = await self._redis.xadd(
            self._keys.events(event.tenant_id),
            {"event": payload},
            maxlen=self._maxlen,
            approximate=True,
        )
        return event.model_copy(update={"id": entry_id})

    async def last_id(self, tenant_id: str) -> str:
        """Position to read from to receive only events published after this call."""
        entries = await self._redis.xrevrange(self._keys.events(tenant_id), count=1)
        return str(entries[0][0]) if entries else STREAM_START

    async def read(
        self, tenant_id: str, after_id: str, *, block_ms: int, count: int = 100
    ) -> list[GatewayEvent]:
        """Events after ``after_id``, waiting up to ``block_ms`` when there are none yet."""
        response = await self._redis.xread(
            {self._keys.events(tenant_id): after_id}, count=count, block=block_ms
        )
        return [
            GatewayEvent.model_validate_json(fields["event"]).model_copy(update={"id": entry_id})
            for entry_id, fields in _entries(response)
        ]


def _entries(response: Any) -> list[tuple[str, dict[str, str]]]:
    """Normalise XREAD replies (RESP2 list or RESP3 map) to ``[(id, fields), ...]``."""
    if not response:
        return []
    streams = response.values() if isinstance(response, dict) else (s[1] for s in response)
    entries: list[tuple[str, dict[str, str]]] = []
    for stream in streams:
        for item in stream:
            # RESP3 may nest the entries one level deeper.
            if isinstance(item, list) and item and isinstance(item[0], list):
                entries.extend((entry_id, fields) for entry_id, fields in item)
            else:
                entry_id, fields = item
                entries.append((entry_id, fields))
    return entries
