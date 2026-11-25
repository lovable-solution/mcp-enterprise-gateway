"""Agent runtime configuration: environment variables prefixed ``AGENT_``.

Run from the repository root, the CLI also reads ./.env, so the local stack's generated
secrets work without exporting anything: a few settings accept the stack's own variable
names (``ANTHROPIC_API_KEY``, ``GATEWAY_JWT_SECRET``, ``AGENT_DB_PASSWORD``...) as
fallbacks. Secrets are ``SecretStr`` and never appear in reprs or logs.
"""

from __future__ import annotations

from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENT_", env_file=".env", extra="ignore", frozen=True, populate_by_name=True
    )

    environment: str = "local"
    log_level: Literal["debug", "info", "warning", "error"] = "info"

    # --- The gateway: the agent's only way to reach tools ------------------------------
    gateway_url: str = "http://localhost:8000"
    gateway_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    """Read timeout for one tool call. The gateway holds approval-gated calls for its
    inline wait (20 s by default) before answering PENDING_APPROVAL."""

    # --- Identity presented to the gateway ----------------------------------------------
    tenant_id: str = "acme"
    subject: str = "crm-copilot"
    """The agent's identity (JWT ``sub``). Approvals are tied to it: keep it stable."""
    role: str = "admin"
    gateway_token: SecretStr | None = None
    """A token issued by the IdP for one session (its ``sid`` must match the thread)."""
    dev_jwt_secret: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("AGENT_DEV_JWT_SECRET", "GATEWAY_JWT_SECRET")
    )
    """Local development only: mint a per-thread HS256 token with the gateway's secret."""
    dev_jwt_issuer: str = Field(
        default="http://localhost/dev-idp",
        validation_alias=AliasChoices("AGENT_DEV_JWT_ISSUER", "GATEWAY_JWT_ISSUER"),
    )
    dev_jwt_audience: str = "mcp-gateway"
    dev_token_ttl_seconds: int = Field(default=3600, ge=60, le=86_400)

    # --- Model ----------------------------------------------------------------------------
    anthropic_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("AGENT_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    )
    model: str = "claude-opus-5-5"
    effort: Effort = "medium"
    """Claude Opus 5.5's default. Thinking is always adaptive on this model."""
    max_tokens: int = Field(default=16_000, ge=1_024, le=128_000)
    server_side_fallbacks: bool = True
    """Send ``fallbacks: "default"``: a request a safety classifier declines is re-run on
    Anthropic's recommended fallback model instead of failing the run."""
    max_model_requests: int = Field(default=30, ge=1, le=500)
    """Local backstop per agent run (between approval pauses); budgets are the gateway's."""
    trace_content: bool = False
    """Record prompts, answers and tool payloads (CRM data) in trace spans."""

    # --- Checkpoints (LangGraph) ----------------------------------------------------------
    checkpointer: Literal["postgres", "memory"] = "postgres"
    database_host: str = "localhost"
    database_port: int = Field(
        default=15432, validation_alias=AliasChoices("AGENT_DATABASE_PORT", "POSTGRES_HOST_PORT")
    )
    database_name: str = Field(
        default="agent_runtime",
        validation_alias=AliasChoices("AGENT_DATABASE_NAME", "AGENT_DB_NAME"),
    )
    database_user: str = Field(
        default="agent_runtime",
        validation_alias=AliasChoices("AGENT_DATABASE_USER", "AGENT_DB_USER"),
    )
    database_password: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("AGENT_DATABASE_PASSWORD", "AGENT_DB_PASSWORD"),
    )
    database_ssl: Literal["disable", "prefer", "require", "verify-full"] = "prefer"

    # --- Approvals --------------------------------------------------------------------------
    approval_poll_seconds: float = Field(default=2.0, gt=0, le=60)

    @field_validator("gateway_url")
    @classmethod
    def _check_gateway_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            msg = "AGENT_GATEWAY_URL must be an absolute http(s) URL"
            raise ValueError(msg)
        return value.rstrip("/")

    @model_validator(mode="after")
    def _check_identity(self) -> Self:
        if self.gateway_token is None and self.dev_jwt_secret is None:
            msg = "set AGENT_GATEWAY_TOKEN, or AGENT_DEV_JWT_SECRET for local development"
            raise ValueError(msg)
        if self.checkpointer == "postgres" and self.database_password is None:
            msg = "the postgres checkpointer needs AGENT_DATABASE_PASSWORD"
            raise ValueError(msg)
        return self
