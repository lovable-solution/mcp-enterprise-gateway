"""Runtime configuration (environment variables prefixed ``CRM_OPS_``)."""

from __future__ import annotations

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_MIN_KEY_LENGTH = 32


class CrmOpsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CRM_OPS_", extra="ignore", frozen=True)

    environment: str = "local"
    host: str = "0.0.0.0"  # noqa: S104 - containers listen on all interfaces; ingress is network-restricted
    port: int = Field(default=8001, ge=1, le=65535)

    gateway_assertion_key: SecretStr
    """HS256 key shared with the gateway; verifies the identity assertion on every request."""

    gateway_issuer: str = "mcp-gateway"
    audience: str = "crm-ops"
    assertion_leeway_seconds: int = Field(default=10, ge=0, le=60)

    allowed_hosts: list[str] = [
        "crm-ops",
        "crm-ops:*",
        "localhost",
        "localhost:*",
        "127.0.0.1",
        "127.0.0.1:*",
    ]
    """Host headers accepted on /mcp (DNS-rebinding protection). ``name:*`` matches any
    explicit port; a Host header on a default port carries no port, so list both forms."""

    @field_validator("gateway_assertion_key")
    @classmethod
    def _key_strength(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < _MIN_KEY_LENGTH:
            msg = f"CRM_OPS_GATEWAY_ASSERTION_KEY must be at least {_MIN_KEY_LENGTH} characters"
            raise ValueError(msg)
        return value
