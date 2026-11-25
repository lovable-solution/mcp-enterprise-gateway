"""Forward-only SQL migrations (``migrations/NNNN_name.sql``), applied in order.

A session-level advisory lock serialises concurrent runners (several gateway replicas
starting at once). Each file runs in its own transaction together with its bookkeeping
row, and an already-applied file whose content changed aborts the run: history is never
silently rewritten.
"""

from __future__ import annotations

import hashlib
import logging
from importlib import resources
from typing import Final

import asyncpg

logger = logging.getLogger(__name__)

_ADVISORY_LOCK_KEY: Final = 0x4D43_5047_5753  # "MCPGWS"


class MigrationError(RuntimeError):
    pass


def _migration_files() -> list[tuple[str, str]]:
    folder = resources.files("mcp_gateway.db") / "migrations"
    files = sorted(
        (entry.name, entry.read_text(encoding="utf-8"))
        for entry in folder.iterdir()
        if entry.name.endswith(".sql")
    )
    return [(name.removesuffix(".sql"), sql) for name, sql in files]


async def apply_migrations(pool: asyncpg.Pool) -> list[str]:
    """Apply pending migrations; return the versions applied by this call."""
    applied_now: list[str] = []
    async with pool.acquire() as connection:
        await connection.execute("SELECT pg_advisory_lock($1)", _ADVISORY_LOCK_KEY)
        try:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version    text PRIMARY KEY,
                    checksum   text NOT NULL,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            rows = await connection.fetch("SELECT version, checksum FROM schema_migrations")
            applied = {row["version"]: row["checksum"] for row in rows}
            for version, sql in _migration_files():
                checksum = hashlib.sha256(sql.encode()).hexdigest()
                if version in applied:
                    if applied[version] != checksum:
                        msg = f"migration {version} changed after it was applied"
                        raise MigrationError(msg)
                    continue
                async with connection.transaction():
                    await connection.execute(sql)
                    await connection.execute(
                        "INSERT INTO schema_migrations (version, checksum) VALUES ($1, $2)",
                        version,
                        checksum,
                    )
                logger.info("applied migration %s", version)
                applied_now.append(version)
        finally:
            await connection.execute("SELECT pg_advisory_unlock($1)", _ADVISORY_LOCK_KEY)
    return applied_now
