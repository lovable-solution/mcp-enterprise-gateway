"""Liveness and readiness probes (unauthenticated; they reveal no tenant data)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .deps import RuntimeDep

router = APIRouter(tags=["health"])


class Readiness(BaseModel):
    status: Literal["ready", "unavailable"]
    database: bool
    redis: bool
    policy: str
    upstreams: dict[str, str]


@router.get("/healthz", summary="Liveness")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", summary="Readiness: database, Redis and policy", response_model=Readiness)
async def readyz(runtime: RuntimeDep) -> JSONResponse:
    try:
        database = await runtime.db.fetchval("SELECT 1") == 1
    except Exception:
        database = False
    try:
        redis = bool(await runtime.redis.ping())
    except Exception:
        redis = False
    ready = database and redis
    body = Readiness(
        status="ready" if ready else "unavailable",
        database=database,
        redis=redis,
        policy=runtime.policy.digest,
        upstreams=runtime.registry.health(),
    )
    return JSONResponse(body.model_dump(), status_code=200 if ready else 503)
