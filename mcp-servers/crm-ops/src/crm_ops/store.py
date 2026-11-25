"""Seeded, in-memory, multi-tenant CRM store.

Every lookup is keyed by ``(tenant_id, account_id)``: another tenant's accounts are
indistinguishable from accounts that do not exist.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal
from uuid import uuid4

from .models import (
    AccountMetrics,
    AccountStatus,
    AccountSummary,
    CreditLimitChange,
    ReceivablesAgeing,
    SearchAccountsResult,
    Segment,
)

MAX_CREDIT_LIMIT_USD: Final = 5_000_000

RiskRating = Literal["low", "medium", "high"]


class AccountNotFoundError(LookupError):
    """No such account for the calling tenant."""


class CreditPolicyError(ValueError):
    """The requested change violates crm-ops' own credit policy."""


@dataclass(slots=True)
class Account:
    tenant_id: str
    account_id: str
    name: str
    segment: Segment
    industry: str
    owner: str
    status: AccountStatus
    credit_limit_usd: int
    annual_revenue_usd: int
    ageing: tuple[int, int, int, int, int]
    """Open receivables: current, 1-30, 31-60, 61-90 and over 90 days."""
    risk_rating: RiskRating


class CrmStore:
    def __init__(
        self, accounts: Iterable[Account], clock: Callable[[], datetime] | None = None
    ) -> None:
        self._accounts = {(a.tenant_id, a.account_id): a for a in accounts}
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = asyncio.Lock()
        self.changes: list[tuple[str, CreditLimitChange]] = []
        """Applied credit-limit changes as ``(tenant_id, change)``, oldest first."""

    def search(
        self, tenant_id: str, query: str, segment: Segment | None, limit: int
    ) -> SearchAccountsResult:
        needle = query.casefold()
        matches = sorted(
            (
                account
                for account in self._accounts.values()
                if account.tenant_id == tenant_id
                and (segment is None or account.segment is segment)
                and any(
                    needle in field.casefold()
                    for field in (account.account_id, account.name, account.industry, account.owner)
                )
            ),
            key=lambda account: account.name,
        )
        return SearchAccountsResult(
            accounts=[_summary(account) for account in matches[:limit]],
            total_matches=len(matches),
            truncated=len(matches) > limit,
        )

    def metrics(self, tenant_id: str, account_id: str) -> AccountMetrics:
        account = self._get(tenant_id, account_id)
        receivables = sum(account.ageing)
        current, d30, d60, d90, over90 = account.ageing
        return AccountMetrics(
            account_id=account.account_id,
            name=account.name,
            annual_revenue_usd=account.annual_revenue_usd,
            open_receivables_usd=receivables,
            ageing=ReceivablesAgeing(
                current_usd=current,
                days_1_30_usd=d30,
                days_31_60_usd=d60,
                days_61_90_usd=d90,
                days_over_90_usd=over90,
            ),
            credit_limit_usd=account.credit_limit_usd,
            credit_utilisation=round(receivables / max(account.credit_limit_usd, 1), 4),
            days_sales_outstanding=round(receivables / account.annual_revenue_usd * 365),
            risk_rating=account.risk_rating,
            as_of=self._clock(),
        )

    async def update_credit_limit(
        self,
        tenant_id: str,
        account_id: str,
        new_limit_usd: int,
        *,
        reason: str,
        changed_by: str,
        approval_id: str | None,
    ) -> CreditLimitChange:
        async with self._lock:
            account = self._get(tenant_id, account_id)
            if account.status is AccountStatus.ON_HOLD:
                msg = f"account {account_id} is on hold; its credit limit cannot change"
                raise CreditPolicyError(msg)
            if new_limit_usd > MAX_CREDIT_LIMIT_USD:
                msg = f"credit limits above ${MAX_CREDIT_LIMIT_USD:,} need a manual underwriting review"
                raise CreditPolicyError(msg)
            change = CreditLimitChange(
                change_id=f"CLC-{uuid4().hex[:12].upper()}",
                account_id=account_id,
                previous_limit_usd=account.credit_limit_usd,
                new_limit_usd=new_limit_usd,
                reason=reason,
                changed_by=changed_by,
                approval_id=approval_id,
                changed_at=self._clock(),
            )
            account.credit_limit_usd = new_limit_usd
            self.changes.append((tenant_id, change))
            return change

    def credit_limit(self, tenant_id: str, account_id: str) -> int:
        return self._get(tenant_id, account_id).credit_limit_usd

    def _get(self, tenant_id: str, account_id: str) -> Account:
        account = self._accounts.get((tenant_id, account_id))
        if account is None:
            msg = f"no account {account_id}"
            raise AccountNotFoundError(msg)
        return account

    @classmethod
    def seeded(cls, clock: Callable[[], datetime] | None = None) -> CrmStore:
        return cls(_seed_accounts(), clock)


def _summary(account: Account) -> AccountSummary:
    return AccountSummary(
        account_id=account.account_id,
        name=account.name,
        segment=account.segment,
        industry=account.industry,
        owner=account.owner,
        status=account.status,
        credit_limit_usd=account.credit_limit_usd,
    )


def _seed_accounts() -> list[Account]:
    ent, mid, smb = Segment.ENTERPRISE, Segment.MID_MARKET, Segment.SMB
    active, hold = AccountStatus.ACTIVE, AccountStatus.ON_HOLD
    rows: list[tuple[str, str, str, Segment, str, str, AccountStatus, int, int, tuple[int, int, int, int, int], RiskRating]] = [
        ("acme", "ACC-1001", "Alder Freight", ent, "logistics", "m.chen", active, 500_000, 12_400_000, (310_000, 120_000, 45_000, 12_000, 3_000), "low"),
        ("acme", "ACC-1002", "Birchwood Clinics", mid, "healthcare", "s.okafor", active, 250_000, 4_800_000, (140_000, 60_000, 25_000, 9_000, 0), "low"),
        ("acme", "ACC-1003", "Cedar Analytics", smb, "software", "m.chen", active, 75_000, 1_150_000, (38_000, 22_000, 9_500, 4_000, 6_500), "medium"),
        ("acme", "ACC-1004", "Dunmore Foods", ent, "food and beverage", "r.alvarez", hold, 900_000, 21_000_000, (450_000, 260_000, 190_000, 140_000, 95_000), "high"),
        ("acme", "ACC-1005", "Elmstead Energy", ent, "energy", "s.okafor", active, 1_200_000, 38_500_000, (820_000, 300_000, 70_000, 15_000, 0), "low"),
        ("zenith", "ACC-2001", "Fernhill Retail", mid, "retail", "k.ito", active, 180_000, 3_900_000, (95_000, 41_000, 18_000, 6_000, 2_000), "medium"),
        ("zenith", "ACC-2002", "Glenrock Mining", ent, "mining", "k.ito", active, 2_000_000, 64_000_000, (1_400_000, 380_000, 120_000, 40_000, 10_000), "low"),
        ("zenith", "ACC-2003", "Hollins Media", smb, "media", "d.berg", active, 40_000, 620_000, (22_000, 9_000, 6_000, 2_500, 1_000), "medium"),
    ]  # fmt: skip
    return [Account(*row) for row in rows]
