"""Bearer tokens the agent presents to the gateway, one session (``sid``) per thread.

The gateway keys budgets, loop detection and approvals on the token's ``sid`` and
``sub``, so a LangGraph thread keeps both for its whole life, across pauses and restarts:
an approval can only be read back (to resume) by the session that requested it.

* ``DevTokenMinter`` (local stack only) signs short-lived HS256 tokens with the gateway's
  development secret, minting a fresh one whenever a thread resumes.
* ``StaticToken`` uses a token the enterprise IdP issued for exactly one session; the
  thread id must then be that token's ``sid``.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from typing import Final, Protocol

from jose import jwt
from jose.exceptions import JWTError
from pydantic import SecretStr

from .settings import AgentSettings

SESSION_ID_REGEX: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class IdentityError(Exception):
    """No usable gateway token for the requested session."""


class TokenProvider(Protocol):
    def token_for(self, session_id: str) -> str: ...

    def new_session_id(self) -> str: ...


def check_session_id(session_id: str) -> str:
    if not SESSION_ID_REGEX.fullmatch(session_id):
        msg = f"invalid thread id {session_id!r}: use letters, digits and . _ : - (max 128)"
        raise IdentityError(msg)
    return session_id


@dataclass(frozen=True, slots=True)
class DevTokenMinter:
    secret: SecretStr
    issuer: str
    audience: str
    tenant_id: str
    subject: str
    role: str
    ttl_seconds: int = 3600

    def token_for(self, session_id: str) -> str:
        now = int(time.time())
        claims = {
            "iss": self.issuer,
            "aud": self.audience,
            "sub": self.subject,
            "tenant_id": self.tenant_id,
            "role": self.role,
            "sid": check_session_id(session_id),
            "iat": now,
            "exp": now + self.ttl_seconds,
            "jti": uuid.uuid4().hex,
        }
        return jwt.encode(claims, self.secret.get_secret_value(), algorithm="HS256")

    def new_session_id(self) -> str:
        return f"t-{uuid.uuid4().hex[:16]}"


@dataclass(frozen=True, slots=True)
class StaticToken:
    token: SecretStr

    @property
    def session_id(self) -> str:
        try:
            sid = jwt.get_unverified_claims(self.token.get_secret_value()).get("sid")
        except JWTError as exc:
            raise IdentityError("AGENT_GATEWAY_TOKEN is not a JWT") from exc
        if not isinstance(sid, str):
            raise IdentityError("AGENT_GATEWAY_TOKEN has no sid claim")
        return check_session_id(sid)

    def token_for(self, session_id: str) -> str:
        if session_id != self.session_id:
            msg = (
                f"AGENT_GATEWAY_TOKEN belongs to session {self.session_id!r}, "
                f"not thread {session_id!r}"
            )
            raise IdentityError(msg)
        return self.token.get_secret_value()

    def new_session_id(self) -> str:
        return self.session_id


def token_provider(settings: AgentSettings) -> TokenProvider:
    if settings.gateway_token is not None:
        return StaticToken(settings.gateway_token)
    if settings.dev_jwt_secret is None:  # excluded by settings validation
        raise IdentityError("no gateway credentials configured")
    return DevTokenMinter(
        secret=settings.dev_jwt_secret,
        issuer=settings.dev_jwt_issuer,
        audience=settings.dev_jwt_audience,
        tenant_id=settings.tenant_id,
        subject=settings.subject,
        role=settings.role,
        ttl_seconds=settings.dev_token_ttl_seconds,
    )
