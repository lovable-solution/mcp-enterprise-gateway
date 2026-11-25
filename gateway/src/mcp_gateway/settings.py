"""Gateway runtime configuration: environment variables prefixed ``GATEWAY_``.

The names are a deployment contract shared with docker-compose.yml and the Terraform task
definitions. Secrets are ``SecretStr`` so they never appear in reprs or logs. A setting
combination that would weaken authentication is rejected at start-up.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .policy.models import SERVER_NAME_REGEX

JwtAlgorithm = Literal[
    "HS256",
    "HS384",
    "HS512",
    "RS256",
    "RS384",
    "RS512",
    "PS256",
    "PS384",
    "PS512",
    "ES256",
    "ES384",
]

_DEFAULT_ALLOWED_HOSTS = [
    "gateway",
    "gateway:*",
    "localhost",
    "localhost:*",
    "127.0.0.1",
    "127.0.0.1:*",
]
_MIN_SECRET_LENGTH = 32


def _default_algorithms() -> list[JwtAlgorithm]:
    return ["RS256"]


def _check_http_url(value: str, what: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        msg = f"{what} must be an absolute http(s) URL"
        raise ValueError(msg)
    return value


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GATEWAY_", extra="ignore", frozen=True)

    environment: str = "local"
    host: str = "0.0.0.0"  # noqa: S104 - containers listen on all interfaces; ingress is network-restricted
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["debug", "info", "warning", "error"] = "info"
    policy_path: Path = Path("config/policies.yaml")
    allowed_hosts: list[str] = Field(default_factory=lambda: list(_DEFAULT_ALLOWED_HOSTS))
    """Host headers accepted on /mcp (DNS-rebinding protection); empty disables the check."""
    cors_origins: list[str] = Field(default_factory=list)
    """Browser origins allowed to call the control-plane API (the operator console)."""

    # --- PostgreSQL: approval store --------------------------------------------------
    database_host: str = "localhost"
    database_port: int = 5432
    database_name: str = "mcp_gateway"
    database_user: str = "mcp_gateway"
    database_password: SecretStr
    database_ssl: Literal["disable", "prefer", "require", "verify-full"] = "prefer"
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_auto_migrate: bool = True
    """Apply pending migrations at start-up (local). Production runs a migration task."""

    # --- Redis: budgets, loop detection, breaker state, events -------------------------
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = Field(default=0, ge=0)
    redis_password: SecretStr | None = None
    redis_tls: bool = False
    redis_key_prefix: str = Field(default="mcpgw", pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")
    redis_pool_size: int = Field(default=64, ge=4, le=1024)

    # --- Upstream MCP servers --------------------------------------------------------
    upstreams: dict[str, str] = Field(default_factory=dict)
    """Logical server name -> Streamable HTTP endpoint, e.g. {"crm-ops": "http://crm-ops:8001/mcp"}."""
    upstream_signing_key: SecretStr
    """HS256 key for the identity assertions sent to upstream servers."""
    upstream_assertion_ttl_seconds: int = Field(default=60, ge=10, le=600)
    upstream_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    tool_catalog_ttl_seconds: float = Field(default=30.0, ge=1)
    strict_tool_arguments: bool = True
    """Reject arguments an upstream schema does not declare, unless the schema explicitly
    allows additional properties. Undeclared fields would otherwise bypass review."""

    # --- Agent / operator authentication (JWT) -----------------------------------------
    jwt_issuer: str
    jwt_audience: str = "mcp-gateway"
    jwt_algorithms: list[JwtAlgorithm] = Field(default_factory=_default_algorithms, min_length=1)
    jwt_secret: SecretStr | None = None
    """HS* only (local development). Production verifies IdP signatures via JWKS."""
    jwt_jwks_url: str | None = None
    jwt_leeway_seconds: int = Field(default=30, ge=0, le=300)

    # --- Approval webhooks -----------------------------------------------------------
    webhook_url: str | None = None
    webhook_hmac_key: SecretStr | None = None
    webhook_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    webhook_max_attempts: int = Field(default=3, ge=1, le=10)

    # --- Events and background work -------------------------------------------------
    event_stream_maxlen: int = Field(default=10_000, ge=100)
    sse_keepalive_seconds: float = Field(default=15.0, gt=0, le=120)
    approval_sweep_interval_seconds: float = Field(default=5.0, gt=0, le=300)

    @field_validator("upstreams")
    @classmethod
    def _check_upstreams(cls, value: dict[str, str]) -> dict[str, str]:
        for name, url in value.items():
            if not re.fullmatch(SERVER_NAME_REGEX, name):
                msg = f"upstream name {name!r} is not a valid server name"
                raise ValueError(msg)
            _check_http_url(url, f"upstream {name!r}")
        return value

    @field_validator("jwt_jwks_url", "webhook_url")
    @classmethod
    def _check_urls(cls, value: str | None) -> str | None:
        # An empty value (e.g. `GATEWAY_WEBHOOK_URL=` in compose) means "not configured".
        return _check_http_url(value, "URL") if value else None

    @field_validator("upstream_signing_key")
    @classmethod
    def _check_signing_key(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < _MIN_SECRET_LENGTH:
            msg = f"GATEWAY_UPSTREAM_SIGNING_KEY must be at least {_MIN_SECRET_LENGTH} characters"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _check_jwt_and_webhooks(self) -> Self:
        hmac = {a for a in self.jwt_algorithms if a.startswith("HS")}
        if hmac and len(hmac) != len(self.jwt_algorithms):
            msg = (
                "GATEWAY_JWT_ALGORITHMS must not mix HMAC and asymmetric algorithms (key confusion)"
            )
            raise ValueError(msg)
        if hmac:
            secret = self.jwt_secret.get_secret_value() if self.jwt_secret else ""
            if len(secret) < _MIN_SECRET_LENGTH:
                msg = (
                    "HS* algorithms need a GATEWAY_JWT_SECRET of at least "
                    f"{_MIN_SECRET_LENGTH} characters"
                )
                raise ValueError(msg)
        elif self.jwt_jwks_url is None:
            msg = "asymmetric JWT algorithms need GATEWAY_JWT_JWKS_URL"
            raise ValueError(msg)
        if self.webhook_url and self.webhook_hmac_key is None:
            msg = "GATEWAY_WEBHOOK_URL needs GATEWAY_WEBHOOK_HMAC_KEY (deliveries are signed)"
            raise ValueError(msg)
        return self

    @property
    def jwt_uses_hmac(self) -> bool:
        return self.jwt_algorithms[0].startswith("HS")
