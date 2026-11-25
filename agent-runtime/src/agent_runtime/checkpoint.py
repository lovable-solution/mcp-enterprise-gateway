"""LangGraph checkpointers: PostgreSQL in every deployed environment, memory for tests.

Checkpoints live in the ``agent_runtime`` database, owned by the runtime's own role. The
gateway's approval records are in a different database the runtime cannot reach, so an
agent can never approve its own call by writing to storage.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.conninfo import make_conninfo

from .settings import AgentSettings


def postgres_conninfo(settings: AgentSettings) -> str:
    password = settings.database_password
    return make_conninfo(
        host=settings.database_host,
        port=settings.database_port,
        dbname=settings.database_name,
        user=settings.database_user,
        password=password.get_secret_value() if password else None,
        sslmode=settings.database_ssl,
        application_name="agent-runtime",
        connect_timeout=10,
    )


@asynccontextmanager
async def open_checkpointer(settings: AgentSettings) -> AsyncIterator[BaseCheckpointSaver[Any]]:
    if settings.checkpointer == "memory":
        yield InMemorySaver()
        return
    async with AsyncPostgresSaver.from_conn_string(postgres_conninfo(settings)) as saver:
        await saver.setup()  # idempotent: applies pending checkpoint-table migrations
        yield saver
