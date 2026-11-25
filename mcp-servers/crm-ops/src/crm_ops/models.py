"""Strict Pydantic v2 models for crm-ops tool outputs.

Tool *inputs* are declared on the tool signatures (MCPServer derives their JSON Schema);
outputs are these models, which MCPServer publishes as each tool's ``outputSchema`` and
returns as ``structuredContent``.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ACCOUNT_ID_PATTERN = r"^ACC-\d{4}$"
AccountId = Annotated[str, StringConstraints(pattern=ACCOUNT_ID_PATTERN)]
Usd = Annotated[int, Field(ge=0)]


class Segment(StrEnum):
    ENTERPRISE = "enterprise"
    MID_MARKET = "mid_market"
    SMB = "smb"


class AccountStatus(StrEnum):
    ACTIVE = "active"
    ON_HOLD = "on_hold"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AccountSummary(_Model):
    account_id: AccountId
    name: str
    segment: Segment
    industry: str
    owner: str
    status: AccountStatus
    credit_limit_usd: Usd


class SearchAccountsResult(_Model):
    accounts: list[AccountSummary]
    total_matches: int = Field(ge=0)
    truncated: bool
    """True when more accounts matched than ``limit`` allowed to return."""


class ReceivablesAgeing(_Model):
    current_usd: Usd
    days_1_30_usd: Usd
    days_31_60_usd: Usd
    days_61_90_usd: Usd
    days_over_90_usd: Usd


class AccountMetrics(_Model):
    account_id: AccountId
    name: str
    annual_revenue_usd: Usd
    open_receivables_usd: Usd
    ageing: ReceivablesAgeing
    credit_limit_usd: Usd
    credit_utilisation: float = Field(ge=0)
    """Open receivables divided by the credit limit."""
    days_sales_outstanding: int = Field(ge=0)
    risk_rating: Literal["low", "medium", "high"]
    as_of: datetime


class CreditLimitChange(_Model):
    change_id: str
    account_id: AccountId
    previous_limit_usd: Usd
    new_limit_usd: Usd
    reason: str
    changed_by: str
    """Subject of the agent principal on whose behalf the gateway made the call."""
    approval_id: str | None
    """Gateway approval that authorised the change, when the call was approval-gated."""
    changed_at: datetime
