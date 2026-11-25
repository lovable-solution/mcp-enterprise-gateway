"""What happens after a session ledger changes: live-activity, warning and exhaustion
events plus the USD cost metric. Shared by direct tool calls, approved executions and
usage reports, so every source of spend is reported the same way."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..events.notifier import Notifier
from ..telemetry import Attr, Instruments
from .budget import BudgetLedger, BudgetSnapshot


def budget_data(snapshot: BudgetSnapshot) -> dict[str, Any]:
    return snapshot.model_dump(mode="json") | {"utilisation": round(snapshot.utilisation, 4)}


class SpendEvents:
    def __init__(
        self,
        *,
        budget: BudgetLedger,
        notifier: Notifier,
        instruments: Instruments,
        warn_at_ratio: float,
    ) -> None:
        self._budget = budget
        self._notifier = notifier
        self._instruments = instruments
        self._warn_at = warn_at_ratio

    def record_tool_cost(self, *, tenant_id: str, tool: str, cost_usd: Decimal) -> None:
        if cost_usd > 0:
            self._instruments.cost_usd.add(
                float(cost_usd), {Attr.COST_SOURCE: "tool", Attr.TOOL: tool, Attr.TENANT: tenant_id}
            )

    async def after_spend(
        self,
        *,
        tenant_id: str,
        session_id: str,
        subject: str,
        role: str,
        snapshot: BudgetSnapshot,
        **context: Any,
    ) -> None:
        budget = budget_data(snapshot)
        await self._notifier.emit(
            "session.activity",
            tenant_id=tenant_id,
            session_id=session_id,
            data={"subject": subject, "role": role, "budget": budget, **context},
        )
        if snapshot.utilisation >= self._warn_at and await self._budget.first_time(
            tenant_id, session_id, "warned"
        ):
            await self._notifier.emit(
                "budget.warning",
                tenant_id=tenant_id,
                session_id=session_id,
                data={"subject": subject, "warn_at_ratio": self._warn_at, "budget": budget},
            )
        if snapshot.exhausted_limit is not None:
            await self.exhausted(
                tenant_id=tenant_id,
                session_id=session_id,
                subject=subject,
                limit=snapshot.exhausted_limit,
                snapshot=snapshot,
            )

    async def exhausted(
        self,
        *,
        tenant_id: str,
        session_id: str,
        subject: str,
        limit: str,
        snapshot: BudgetSnapshot,
    ) -> None:
        """Announce exhaustion once per session ledger, however many calls it refuses."""
        if await self._budget.first_time(tenant_id, session_id, "exhausted"):
            await self._notifier.emit(
                "budget.exhausted",
                tenant_id=tenant_id,
                session_id=session_id,
                data={"subject": subject, "limit": limit, "budget": budget_data(snapshot)},
            )
