"""Directory of recently active agent sessions per tenant (for the operator console)."""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from redis.asyncio import Redis

from ..auth.principal import Principal
from .keys import RedisKeys


class SessionInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    subject: str
    role: str
    first_seen: datetime
    last_seen: datetime


class SessionDirectory:
    def __init__(self, redis: Redis, keys: RedisKeys) -> None:
        self._redis = redis
        self._keys = keys

    async def touch(self, principal: Principal, ttl_seconds: int) -> None:
        assert principal.session_id is not None  # noqa: S101 - agent principals only
        now = time.time()
        meta = self._keys.session_meta(principal.tenant_id, principal.session_id)
        index = self._keys.sessions(principal.tenant_id)
        async with self._redis.pipeline(transaction=False) as pipe:
            pipe.hset(
                meta,
                mapping={"subject": principal.subject, "role": principal.role, "last_seen": now},
            )
            pipe.hsetnx(meta, "first_seen", now)
            pipe.expire(meta, ttl_seconds)
            pipe.zadd(index, {principal.session_id: now})
            pipe.expire(index, ttl_seconds)
            await pipe.execute()

    async def recent(
        self, tenant_id: str, *, ttl_seconds: int, limit: int = 100
    ) -> list[SessionInfo]:
        index = self._keys.sessions(tenant_id)
        await self._redis.zremrangebyscore(index, "-inf", time.time() - ttl_seconds)
        session_ids = [str(sid) for sid in await self._redis.zrevrange(index, 0, limit - 1)]
        async with self._redis.pipeline(transaction=False) as pipe:
            for session_id in session_ids:
                pipe.hgetall(self._keys.session_meta(tenant_id, session_id))
            metas = await pipe.execute()
        return [
            info
            for session_id, meta in zip(session_ids, metas, strict=True)
            if (info := _info(session_id, _decoded(meta))) is not None
        ]

    async def get(self, tenant_id: str, session_id: str) -> SessionInfo | None:
        meta = await self._redis.hgetall(self._keys.session_meta(tenant_id, session_id))
        return _info(session_id, _decoded(meta))


def _decoded(mapping: Mapping[Any, Any]) -> dict[str, str]:
    """Redis replies are already decoded (decode_responses=True); make that explicit."""
    return {str(key): str(value) for key, value in mapping.items()}


def _info(session_id: str, meta: dict[str, str]) -> SessionInfo | None:
    if not meta:
        return None
    return SessionInfo(
        session_id=session_id,
        subject=meta["subject"],
        role=meta["role"],
        first_seen=datetime.fromtimestamp(float(meta.get("first_seen", meta["last_seen"])), UTC),
        last_seen=datetime.fromtimestamp(float(meta["last_seen"]), UTC),
    )
