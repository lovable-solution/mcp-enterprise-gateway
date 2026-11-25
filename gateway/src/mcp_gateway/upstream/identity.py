"""Identity assertions the gateway presents to upstream MCP servers.

Upstreams never see agent tokens. For each call the gateway signs a short-lived JWT whose
``aud`` is the upstream's logical name (an assertion minted for one server is useless at
another) and whose claims carry the authorised caller: tenant, subject, session, role and,
for an approval-gated call, the approval id.

One HS256 key is shared with the upstreams. With several upstreams of different trust
levels, switch to per-upstream keys or asymmetric (ES256) assertions, so that a
compromised upstream cannot mint assertions for its peers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Final
from uuid import uuid4

from jose import jwt

from ..auth.principal import Principal

ISSUER: Final = "mcp-gateway"
ALGORITHM: Final = "HS256"


@dataclass(frozen=True, slots=True)
class CallerIdentity:
    subject: str
    tenant_id: str | None = None
    session_id: str | None = None
    role: str | None = None
    approval_id: str | None = None

    @classmethod
    def of(cls, principal: Principal, approval_id: str | None = None) -> CallerIdentity:
        return cls(
            subject=principal.subject,
            tenant_id=principal.tenant_id,
            session_id=principal.session_id,
            role=principal.role,
            approval_id=approval_id,
        )


#: Identity used for discovery (tools/list): no tenant, so tenant-scoped tools refuse it.
GATEWAY_IDENTITY: Final = CallerIdentity(subject=ISSUER)


class AssertionSigner:
    def __init__(self, key: str, *, ttl_seconds: int) -> None:
        self._key = key
        self._ttl = ttl_seconds

    def sign(self, audience: str, identity: CallerIdentity) -> str:
        now = int(time.time())
        claims: dict[str, object] = {
            "iss": ISSUER,
            "aud": audience,
            "sub": identity.subject,
            "iat": now,
            "exp": now + self._ttl,
            "jti": uuid4().hex,
        }
        optional = {
            "tenant_id": identity.tenant_id,
            "sid": identity.session_id,
            "role": identity.role,
            "approval_id": identity.approval_id,
        }
        claims.update({name: value for name, value in optional.items() if value is not None})
        return jwt.encode(claims, self._key, algorithm=ALGORITHM)
