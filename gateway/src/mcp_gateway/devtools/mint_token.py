"""``mcp-gateway-token``: mint a *local development* JWT for the gateway.

Signs with GATEWAY_JWT_SECRET (HS256), read from the environment or ./.env. Production
tokens come from the enterprise IdP; this tool refuses to run without an HMAC secret.

    mcp-gateway-token --role analyst                      # agent token, fresh session id
    mcp-gateway-token --role operator --sub ops-alice --no-session
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from collections.abc import Sequence

from jose import jwt
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class _TokenSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GATEWAY_", env_file=".env", extra="ignore")

    jwt_secret: SecretStr
    jwt_issuer: str = "http://localhost/dev-idp"
    jwt_audience: str = "mcp-gateway"


def mint(
    *,
    secret: str,
    issuer: str,
    audience: str,
    role: str,
    tenant: str,
    subject: str,
    session_id: str | None,
    ttl_seconds: int,
) -> str:
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "tenant_id": tenant,
        "role": role,
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": uuid.uuid4().hex,
    }
    if session_id is not None:
        claims["sid"] = session_id
    return jwt.encode(claims, secret, algorithm="HS256")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mcp-gateway-token", description=__doc__.split("\n")[0])
    parser.add_argument("--role", required=True)
    parser.add_argument("--tenant", default="acme")
    parser.add_argument("--sub", default=None, help="subject (default: <role>-dev)")
    parser.add_argument("--sid", default=None, help="session id (default: random)")
    parser.add_argument("--no-session", action="store_true", help="omit sid (operator tokens)")
    parser.add_argument("--ttl", type=int, default=3600, help="lifetime in seconds")
    args = parser.parse_args(argv)
    try:
        settings = _TokenSettings()
    except ValueError:
        print("GATEWAY_JWT_SECRET is not set (environment or ./.env)", file=sys.stderr)
        return 1
    token = mint(
        secret=settings.jwt_secret.get_secret_value(),
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        role=args.role,
        tenant=args.tenant,
        subject=args.sub or f"{args.role}-dev",
        session_id=None if args.no_session else (args.sid or f"s-{uuid.uuid4().hex[:12]}"),
        ttl_seconds=args.ttl,
    )
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
