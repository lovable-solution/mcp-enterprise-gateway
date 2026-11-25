"""JWT validation: HS256 for local development, RS256 via JWKS (with rotation), and the
hostile cases (alg=none, key confusion, missing claims)."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any

import httpx2
import pytest
from anyio import fail_after as anyio_fail_after
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk, jwt

from mcp_gateway.auth.jwt import JWTValidator, TokenError
from mcp_gateway.settings import GatewaySettings

pytestmark = pytest.mark.anyio

SECRET = "s" * 48
ISSUER = "https://idp.example.test/"
JWKS_URL = "https://idp.example.test/.well-known/jwks.json"


def settings(**overrides: Any) -> GatewaySettings:
    base: dict[str, Any] = {
        "database_password": "x",
        "upstream_signing_key": "u" * 40,
        "jwt_issuer": ISSUER,
        "jwt_algorithms": ["HS256"],
        "jwt_secret": SECRET,
    }
    return GatewaySettings(**(base | overrides))


def claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    base = {
        "iss": ISSUER,
        "aud": "mcp-gateway",
        "sub": "agent-7",
        "tenant_id": "acme",
        "role": "analyst",
        "sid": "s-123",
        "iat": now,
        "exp": now + 300,
        "jti": "t-1",
    }
    return {k: v for k, v in (base | overrides).items() if v is not None}


async def test_valid_hs256_token_yields_the_principal() -> None:
    principal = await JWTValidator(settings()).validate(
        jwt.encode(claims(), SECRET, algorithm="HS256")
    )
    assert (principal.subject, principal.tenant_id, principal.role, principal.session_id) == (
        "agent-7",
        "acme",
        "analyst",
        "s-123",
    )


@pytest.mark.parametrize(
    ("token_claims", "message"),
    [
        (claims(exp=int(time.time()) - 600), "token expired"),
        (claims(aud="other"), "invalid claims"),
        (claims(iss="https://evil.test/"), "invalid claims"),
        (claims(tenant_id=None), "missing claims: tenant_id"),
        (claims(role=None), "missing claims: role"),
        (claims(tenant_id="ACME/{x}"), "malformed identity claims"),
        (claims(sid="s-1}{"), "malformed identity claims"),
    ],
)
async def test_bad_claims_are_rejected(token_claims: dict[str, Any], message: str) -> None:
    with pytest.raises(TokenError, match=message):
        await JWTValidator(settings()).validate(jwt.encode(token_claims, SECRET, algorithm="HS256"))


async def test_unsigned_tokens_are_rejected() -> None:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(claims()).encode()).rstrip(b"=").decode()
    with pytest.raises(TokenError, match="'none' is not accepted"):
        await JWTValidator(settings()).validate(f"{header}.{body}.")


async def test_algorithms_outside_the_allow_list_are_rejected() -> None:
    token = jwt.encode(claims(), SECRET, algorithm="HS512")
    with pytest.raises(TokenError, match="'HS512' is not accepted"):
        await JWTValidator(settings()).validate(token)


async def test_wrong_secret_is_rejected() -> None:
    with pytest.raises(TokenError, match="invalid token signature"):
        await JWTValidator(settings()).validate(jwt.encode(claims(), "o" * 48, algorithm="HS256"))


def _rsa_key(kid: str) -> tuple[bytes, dict[str, Any]]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    public_jwk: dict[str, Any] = jwk.construct(public_pem.decode(), "RS256").to_dict()
    return pem, public_jwk | {"kid": kid, "use": "sig", "alg": "RS256"}


async def test_rs256_via_jwks_with_key_rotation() -> None:
    old_pem, old_jwk = _rsa_key("2026-10")
    new_pem, new_jwk = _rsa_key("2026-11")
    published = {"keys": [old_jwk]}
    fetches: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        fetches.append(str(request.url))
        return httpx2.Response(200, json=published)

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    validator = JWTValidator(
        settings(jwt_algorithms=["RS256"], jwt_secret=None, jwt_jwks_url=JWKS_URL), http
    )
    old_token = jwt.encode(
        claims(), old_pem.decode(), algorithm="RS256", headers={"kid": "2026-10"}
    )
    assert (await validator.validate(old_token)).subject == "agent-7"

    # The IdP rotates its key: an unknown kid triggers a (rate-limited) JWKS refresh.
    published = {"keys": [old_jwk, new_jwk]}
    validator._jwks._last_attempt -= 60  # type: ignore[union-attr]  # past the refresh rate limit
    new_token = jwt.encode(
        claims(), new_pem.decode(), algorithm="RS256", headers={"kid": "2026-11"}
    )
    assert (await validator.validate(new_token)).subject == "agent-7"
    assert len(fetches) == 2

    # HS256 tokens signed with the *public* key (key-confusion attack) never get a key.
    confused = jwt.encode(
        claims(), json.dumps(new_jwk), algorithm="HS256", headers={"kid": "2026-11"}
    )
    with pytest.raises(TokenError, match="'HS256' is not accepted"):
        await validator.validate(confused)
    await http.aclose()


async def test_jwks_outage_costs_one_fetch_per_interval_and_keeps_cached_keys() -> None:
    pem, public_jwk = _rsa_key("2026-10")
    responses = [httpx2.Response(200, json={"keys": [public_jwk]})]
    fetches = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal fetches
        fetches += 1
        return responses.pop(0) if responses else httpx2.Response(503)

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    validator = JWTValidator(
        settings(jwt_algorithms=["RS256"], jwt_secret=None, jwt_jwks_url=JWKS_URL), http
    )
    known = jwt.encode(claims(), pem.decode(), algorithm="RS256", headers={"kid": "2026-10"})
    assert (await validator.validate(known)).subject == "agent-7"

    # The IdP goes down. Tokens with an unknown kid fail, but only the first one fetches.
    validator._jwks._last_attempt -= 60  # type: ignore[union-attr]
    other_pem, _ = _rsa_key("2026-11")
    unknown = jwt.encode(claims(), other_pem.decode(), algorithm="RS256", headers={"kid": "x"})
    for _ in range(5):
        with pytest.raises(TokenError, match="unknown signing key"):
            await validator.validate(unknown)
    assert fetches == 2

    # Known keys keep working from the cache throughout the outage.
    assert (await validator.validate(known)).subject == "agent-7"
    assert fetches == 2
    await http.aclose()


async def test_a_stale_cache_serves_known_keys_and_refreshes_in_the_background() -> None:
    pem, public_jwk = _rsa_key("2026-10")
    fetched = asyncio.Event()

    def handler(request: httpx2.Request) -> httpx2.Response:
        fetched.set()
        return httpx2.Response(200, json={"keys": [public_jwk]})

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    validator = JWTValidator(
        settings(jwt_algorithms=["RS256"], jwt_secret=None, jwt_jwks_url=JWKS_URL), http
    )
    token = jwt.encode(claims(), pem.decode(), algorithm="RS256", headers={"kid": "2026-10"})
    await validator.validate(token)
    fetched.clear()

    cache = validator._jwks
    assert cache is not None
    cache._fetched_at -= 3600  # older than the TTL
    cache._last_attempt -= 3600
    assert (await validator.validate(token)).subject == "agent-7"  # served from the cache
    with anyio_fail_after(5):
        await fetched.wait()  # ...while a refresh runs in the background
    assert cache._background is not None
    await cache._background
    await http.aclose()


async def test_der_encoded_public_keys_cannot_forge_hs256_tokens() -> None:
    """python-jose accepts a DER-encoded public key as an HMAC secret (PYSEC-2026-4298).
    The gateway never offers jose that choice: the alg allow-list is checked first."""
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    der = private.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    forged = jwt.encode(claims(), der, algorithm="HS256", headers={"kid": "2026-10"})
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _: httpx2.Response(500)))
    validator = JWTValidator(
        settings(jwt_algorithms=["RS256"], jwt_secret=None, jwt_jwks_url=JWKS_URL), http
    )
    with pytest.raises(TokenError, match="'HS256' is not accepted"):
        await validator.validate(forged)
    await http.aclose()


def test_jose_uses_the_cryptography_backends() -> None:
    """The pure-Python fallbacks (python-rsa, ecdsa) carry unfixed timing advisories."""
    from jose.backends import ECKey, RSAKey  # noqa: PLC0415
    from jose.backends.cryptography_backend import (  # noqa: PLC0415
        CryptographyECKey,
        CryptographyRSAKey,
    )

    assert RSAKey is CryptographyRSAKey
    assert ECKey is CryptographyECKey
