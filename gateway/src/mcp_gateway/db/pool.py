"""asyncpg connection pool for the gateway's database."""

from __future__ import annotations

import json
import ssl as ssl_lib
from typing import Literal

import asyncpg

from ..settings import GatewaySettings


async def _init_connection(connection: asyncpg.Connection[asyncpg.Record]) -> None:
    for json_type in ("json", "jsonb"):
        await connection.set_type_codec(
            json_type, encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )


SslMode = Literal["disable", "prefer", "require"]


def _ssl(
    mode: Literal["disable", "prefer", "require", "verify-full"],
) -> SslMode | ssl_lib.SSLContext:
    if mode == "verify-full":
        return ssl_lib.create_default_context()  # verifies chain and hostname
    return mode  # same meaning as libpq's sslmode


async def create_pool(settings: GatewaySettings) -> asyncpg.Pool[asyncpg.Record]:
    pool = await asyncpg.create_pool(
        host=settings.database_host,
        port=settings.database_port,
        user=settings.database_user,
        password=settings.database_password.get_secret_value(),
        database=settings.database_name,
        ssl=_ssl(settings.database_ssl),
        min_size=1,
        max_size=settings.database_pool_size,
        command_timeout=30,
        init=_init_connection,
    )
    assert pool is not None  # noqa: S101 - create_pool returns a pool once awaited
    return pool
