"""JWT validation with python-jose.

Hardening beyond signature checks:

* the token's ``alg`` header must be in the configured allow-list *before* any key is
  chosen, so ``none`` and HMAC/RSA key-confusion tokens are rejected outright (settings
  already forbid mixing HS* with asymmetric algorithms);
* ``exp``, ``iat``, ``sub``, ``iss`` and ``aud`` are required, plus the ``tenant_id`` and
  ``role`` claims the gateway authorises on;
* asymmetric keys come from the IdP's JWKS, selected by ``kid``, cached, and refreshed
  (rate-limited) when an unknown ``kid`` appears, so key rotation needs no restart.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import anyio
import httpx2
from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTClaimsError, JWTError
from pydantic import ValidationError

from ..settings import GatewaySettings
from .principal import Principal

_REQUIRED_CLAIMS = ("tenant_id", "role")


class TokenError(Exception):
    """The bearer token is unusable; the message is safe to return to the caller."""


class JWKSCache:
    """Signing keys of the IdP, by ``kid``."""

    def __init__(
        self,
        url: str,
        http_client: httpx2.AsyncClient,
        *,
        ttl_seconds: float = 600.0,
        min_refresh_interval_seconds: float = 30.0,
    ) -> None:
        self._url = url
        self._http = http_client
        self._ttl = ttl_seconds
        self._min_refresh = min_refresh_interval_seconds
        self._keys: list[dict[str, Any]] = []
        self._fetched_at = float("-inf")
        self._lock = anyio.Lock()

    async def key(self, kid: str | None) -> dict[str, Any]:
        now = time.monotonic()
        if now - self._fetched_at > self._ttl:
            await self._refresh()
        found = self._select(kid)
        if found is None and time.monotonic() - self._fetched_at > self._min_refresh:
            await self._refresh()  # possibly a newly rotated key
            found = self._select(kid)
        if found is None:
            raise TokenError("unknown signing key")
        return found

    def _select(self, kid: str | None) -> dict[str, Any] | None:
        if kid is not None:
            return next((k for k in self._keys if k.get("kid") == kid), None)
        return self._keys[0] if len(self._keys) == 1 else None

    async def _refresh(self) -> None:
        async with self._lock:
            try:
                response = await self._http.get(self._url, timeout=5.0)
                response.raise_for_status()
                keys = response.json().get("keys", [])
            except (httpx2.HTTPError, ValueError) as exc:
                if not self._keys:
                    raise TokenError("signing keys unavailable") from exc
                return  # keep serving the last good key set
            self._keys = [k for k in keys if isinstance(k, dict) and k.get("use", "sig") == "sig"]
            self._fetched_at = time.monotonic()


class JWTValidator:
    def __init__(
        self, settings: GatewaySettings, http_client: httpx2.AsyncClient | None = None
    ) -> None:
        self._algorithms = tuple(settings.jwt_algorithms)
        self._issuer = settings.jwt_issuer
        self._audience = settings.jwt_audience
        self._leeway = settings.jwt_leeway_seconds
        self._secret: str | None = None
        self._jwks: JWKSCache | None = None
        if settings.jwt_uses_hmac:
            assert settings.jwt_secret is not None  # noqa: S101 - guaranteed by settings validation
            self._secret = settings.jwt_secret.get_secret_value()
        else:
            assert settings.jwt_jwks_url is not None  # noqa: S101 - guaranteed by settings validation
            self._jwks = JWKSCache(settings.jwt_jwks_url, http_client or httpx2.AsyncClient())

    async def validate(self, token: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
        except JWTError as exc:
            raise TokenError("malformed token") from exc
        algorithm = header.get("alg")
        if algorithm not in self._algorithms:
            raise TokenError(f"signing algorithm {algorithm!r} is not accepted")
        key: str | dict[str, Any]
        if self._secret is not None:
            key = self._secret
        else:
            assert self._jwks is not None  # noqa: S101
            key = await self._jwks.key(header.get("kid"))
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=[algorithm],
                audience=self._audience,
                issuer=self._issuer,
                options={
                    "require_exp": True,
                    "require_iat": True,
                    "require_sub": True,
                    "require_iss": True,
                    "require_aud": True,
                    "leeway": self._leeway,
                },
            )
        except ExpiredSignatureError as exc:
            raise TokenError("token expired") from exc
        except JWTClaimsError as exc:
            raise TokenError(f"invalid claims: {exc}") from exc
        except JWTError as exc:
            raise TokenError("invalid token signature") from exc
        return _principal(claims)


def _principal(claims: dict[str, Any]) -> Principal:
    missing = [name for name in _REQUIRED_CLAIMS if not isinstance(claims.get(name), str)]
    if missing:
        raise TokenError(f"missing claims: {', '.join(missing)}")
    try:
        return Principal(
            subject=claims["sub"],
            tenant_id=claims["tenant_id"],
            role=claims["role"],
            session_id=claims.get("sid"),
            token_id=claims.get("jti"),
            expires_at=datetime.fromtimestamp(int(claims["exp"]), UTC),
        )
    except (ValidationError, TypeError, ValueError) as exc:
        raise TokenError("malformed identity claims") from exc
