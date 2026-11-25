"""Redis key layout.

Every key of one agent session shares the hash tag ``{<tenant>/<sid>}``, so the Lua
scripts touching several of them stay atomic on Redis Cluster too. Tenant-wide keys use
``{<tenant>}``. Principal validation guarantees neither part contains braces or '/'.
"""

from __future__ import annotations


class RedisKeys:
    def __init__(self, prefix: str) -> None:
        self._prefix = prefix

    def _session(self, tenant_id: str, session_id: str) -> str:
        return f"{self._prefix}:{{{tenant_id}/{session_id}}}"

    def budget(self, tenant_id: str, session_id: str) -> str:
        return f"{self._session(tenant_id, session_id)}:budget"

    def breaker(self, tenant_id: str, session_id: str) -> str:
        return f"{self._session(tenant_id, session_id)}:breaker"

    def loop_prefix(self, tenant_id: str, session_id: str) -> str:
        return f"{self._session(tenant_id, session_id)}:loop:"

    def loop(self, tenant_id: str, session_id: str, fingerprint: str) -> str:
        return self.loop_prefix(tenant_id, session_id) + fingerprint

    def loop_index(self, tenant_id: str, session_id: str) -> str:
        return f"{self._session(tenant_id, session_id)}:loops"

    def session_meta(self, tenant_id: str, session_id: str) -> str:
        return f"{self._session(tenant_id, session_id)}:meta"

    def sessions(self, tenant_id: str) -> str:
        return f"{self._prefix}:{{{tenant_id}}}:sessions"

    def events(self, tenant_id: str) -> str:
        return f"{self._prefix}:{{{tenant_id}}}:events"
