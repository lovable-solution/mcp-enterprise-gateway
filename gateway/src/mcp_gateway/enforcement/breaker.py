"""Loop detection and the per-session circuit breaker.

Semantics (policies.yaml ``circuit_breaker``): a session that sends the same tool call
(same tool, canonically identical arguments) more than ``max_identical_calls`` times
within ``window_seconds`` trips its breaker. Every further tool call of that session is
refused until ``cooldown_seconds`` pass, or until an operator resets it when
``require_manual_reset`` is set. The check runs before RBAC, so an agent hammering a tool
it may not even call is halted as well.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from redis.asyncio import Redis

from ..policy.models import CircuitBreakerPolicy
from .keys import RedisKeys
from .scripts import BREAKER_RESET, LOOP_CHECK


@dataclass(frozen=True, slots=True)
class BreakerTrip:
    """Why a session's breaker is open (stored as JSON in the breaker key)."""

    tool: str
    fingerprint: str
    identical_calls_allowed: int
    window_seconds: int
    tripped_at: str
    manual_reset_required: bool

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> BreakerTrip:
        return cls(**json.loads(raw))


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    TRIPPED = "tripped"
    """Opened by this very call."""


@dataclass(frozen=True, slots=True)
class BreakerVerdict:
    state: BreakerState
    identical_calls: int
    trip: BreakerTrip | None = None
    retry_after_seconds: int | None = None
    """None while the breaker waits for a manual reset."""

    @property
    def blocked(self) -> bool:
        return self.state is not BreakerState.CLOSED


class CircuitBreaker:
    def __init__(self, redis: Redis, keys: RedisKeys) -> None:
        self._redis = redis
        self._keys = keys
        self._check = redis.register_script(LOOP_CHECK)
        self._reset = redis.register_script(BREAKER_RESET)

    async def check(
        self,
        *,
        tenant_id: str,
        session_id: str,
        tool: str,
        fingerprint: str,
        policy: CircuitBreakerPolicy,
        index_ttl_seconds: int,
    ) -> BreakerVerdict:
        """Record this call in its loop window and report the session's breaker state."""
        detection = policy.loop_detection
        trip = BreakerTrip(
            tool=tool,
            fingerprint=fingerprint,
            identical_calls_allowed=detection.max_identical_calls,
            window_seconds=detection.window_seconds,
            tripped_at=datetime.now(UTC).isoformat(),
            manual_reset_required=policy.require_manual_reset,
        )
        cooldown = 0 if policy.require_manual_reset else policy.cooldown_seconds
        state, detail, extra = await self._check(
            keys=[
                self._keys.breaker(tenant_id, session_id),
                self._keys.loop(tenant_id, session_id, fingerprint),
                self._keys.loop_index(tenant_id, session_id),
            ],
            args=[
                "1" if detection.enabled else "0",
                detection.window_seconds * 1000,
                detection.max_identical_calls,
                cooldown,
                uuid4().hex,
                trip.to_json(),
                fingerprint,
                index_ttl_seconds,
            ],
        )
        if state == "open":
            return BreakerVerdict(
                BreakerState.OPEN, 0, BreakerTrip.from_json(detail), _retry_after(int(extra))
            )
        if state == "tripped":
            return BreakerVerdict(BreakerState.TRIPPED, int(extra), trip, cooldown or None)
        return BreakerVerdict(BreakerState.CLOSED, int(detail))

    async def status(
        self, tenant_id: str, session_id: str
    ) -> tuple[BreakerTrip | None, int | None]:
        """The open trip (if any) and the seconds until it closes by itself."""
        key = self._keys.breaker(tenant_id, session_id)
        async with self._redis.pipeline(transaction=False) as pipe:
            pipe.get(key)
            pipe.pttl(key)
            raw, pttl = await pipe.execute()
        if raw is None:
            return None, None
        return BreakerTrip.from_json(raw), _retry_after(int(pttl))

    async def reset(self, tenant_id: str, session_id: str) -> bool:
        """Close the breaker and forget the session's loop windows. True if it was open."""
        was_open = await self._reset(
            keys=[
                self._keys.breaker(tenant_id, session_id),
                self._keys.loop_index(tenant_id, session_id),
            ],
            args=[self._keys.loop_prefix(tenant_id, session_id)],
        )
        return bool(was_open)


def _retry_after(pttl_ms: int) -> int | None:
    return None if pttl_ms < 0 else max(1, math.ceil(pttl_ms / 1000))
