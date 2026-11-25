"""Per-session budget ledger (hard caps on tool calls, tokens and USD).

Money is kept in integer micro-dollars so concurrent increments from any number of gateway
replicas stay exact; every conversion from a fractional price rounds *up*, so the ledger
can over-count by at most a micro-dollar per entry but never under-count.

Sources of spend:
* tool calls forwarded upstream: one ``tool_calls`` unit plus the tool's flat
  ``cost_usd_per_call`` (charged atomically with the cap check: refused calls cost nothing);
* model usage reported by the agent runtime after each LLM call, priced with the policy's
  table (unknown models at the conservative fallback rate).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from redis.asyncio import Redis

from ..policy.models import MODEL_ID_REGEX, PricingPolicy, SessionBudget
from ..telemetry import Attr, tracer
from .keys import RedisKeys
from .scripts import BUDGET_CHARGE_TOOL_CALL, BUDGET_RECORD_USAGE

MICROS_PER_USD: Final = Decimal(1_000_000)
_MICRO_USD: Final = Decimal("0.000001")

LimitName = Literal["max_tool_calls", "max_total_tokens", "max_cost_usd"]


def usd_to_micros(amount: Decimal) -> int:
    return int((amount * MICROS_PER_USD).to_integral_value(rounding=ROUND_CEILING))


def micros_to_usd(micros: int) -> Decimal:
    return (Decimal(micros) / MICROS_PER_USD).quantize(_MICRO_USD)


class UsageReport(BaseModel):
    """One model call's token usage, field-compatible with Anthropic's ``usage`` object."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: Annotated[str, StringConstraints(pattern=MODEL_ID_REGEX)]
    """Model id as reported in the provider's response."""
    input_tokens: int = Field(ge=0, le=100_000_000)
    output_tokens: int = Field(ge=0, le=100_000_000)
    cache_creation_input_tokens: int = Field(default=0, ge=0, le=100_000_000)
    cache_read_input_tokens: int = Field(default=0, ge=0, le=100_000_000)
    cache_ttl: Literal["5m", "1h"] = "5m"
    """TTL of the cache writes in this call (priced differently)."""

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )


@dataclass(frozen=True, slots=True)
class PricedUsage:
    cost_usd: Decimal
    priced_at_fallback: bool


def price_usage(report: UsageReport, pricing: PricingPolicy) -> PricedUsage:
    price, fallback = pricing.price_for(report.model)
    cache_write_rate = (
        price.cache_write_1h_per_mtok if report.cache_ttl == "1h" else price.cache_write_5m_per_mtok
    )
    per_mtok = (
        report.input_tokens * price.input_per_mtok
        + report.output_tokens * price.output_per_mtok
        + report.cache_creation_input_tokens * cache_write_rate
        + report.cache_read_input_tokens * price.cache_read_per_mtok
    )
    return PricedUsage(cost_usd=per_mtok / Decimal(1_000_000), priced_at_fallback=fallback)


class BudgetSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool_calls: int
    total_tokens: int
    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    cost_usd: Decimal
    llm_cost_usd: Decimal
    tool_cost_usd: Decimal
    max_tool_calls: int
    max_total_tokens: int
    max_cost_usd: Decimal
    exhausted_limit: LimitName | None
    """The first limit reached; while set, no further tool call is forwarded."""

    @property
    def exhausted(self) -> bool:
        return self.exhausted_limit is not None

    @property
    def utilisation(self) -> float:
        """Highest fraction of any limit consumed (0..1+)."""
        return max(
            self.tool_calls / self.max_tool_calls,
            self.total_tokens / self.max_total_tokens,
            float(self.cost_usd / self.max_cost_usd),
        )

    @classmethod
    def from_ledger(cls, data: Mapping[str, str], limits: SessionBudget) -> BudgetSnapshot:
        def num(field: str) -> int:
            return int(data.get(field) or 0)

        calls, tokens, cost = num("tool_calls"), num("total_tokens"), num("cost_micros")
        exhausted: LimitName | None = None
        if calls >= limits.max_tool_calls:
            exhausted = "max_tool_calls"
        elif tokens >= limits.max_total_tokens:
            exhausted = "max_total_tokens"
        elif cost >= usd_to_micros(limits.max_cost_usd):
            exhausted = "max_cost_usd"
        return cls(
            tool_calls=calls,
            total_tokens=tokens,
            input_tokens=num("input_tokens"),
            output_tokens=num("output_tokens"),
            cache_write_tokens=num("cache_write_tokens"),
            cache_read_tokens=num("cache_read_tokens"),
            cost_usd=micros_to_usd(cost),
            llm_cost_usd=micros_to_usd(num("llm_cost_micros")),
            tool_cost_usd=micros_to_usd(num("tool_cost_micros")),
            max_tool_calls=limits.max_tool_calls,
            max_total_tokens=limits.max_total_tokens,
            max_cost_usd=limits.max_cost_usd,
            exhausted_limit=exhausted,
        )


class BudgetExceededError(Exception):
    def __init__(self, limit: LimitName, snapshot: BudgetSnapshot) -> None:
        super().__init__(f"session budget limit reached: {limit}")
        self.limit: LimitName = limit
        self.snapshot = snapshot


class BudgetLedger:
    def __init__(self, redis: Redis, keys: RedisKeys) -> None:
        self._redis = redis
        self._keys = keys
        self._charge = redis.register_script(BUDGET_CHARGE_TOOL_CALL)
        self._record = redis.register_script(BUDGET_RECORD_USAGE)

    async def snapshot(
        self, tenant_id: str, session_id: str, limits: SessionBudget
    ) -> BudgetSnapshot:
        data = await self._redis.hgetall(self._keys.budget(tenant_id, session_id))
        return BudgetSnapshot.from_ledger({str(k): str(v) for k, v in data.items()}, limits)

    async def charge_tool_call(
        self,
        *,
        tenant_id: str,
        session_id: str,
        limits: SessionBudget,
        cost_usd: Decimal,
        ttl_seconds: int,
    ) -> BudgetSnapshot:
        """Atomically check every cap and charge one forwarded call; raise if refused."""
        key = self._keys.budget(tenant_id, session_id)
        with tracer.start_as_current_span("gateway.budget.charge") as span:
            status, *_ = await self._charge(
                keys=[key],
                args=[
                    limits.max_tool_calls,
                    limits.max_total_tokens,
                    usd_to_micros(limits.max_cost_usd),
                    usd_to_micros(cost_usd),
                    ttl_seconds,
                ],
            )
            span.set_attribute(Attr.BUDGET_LIMIT, "" if status == "ok" else status)
        snapshot = await self.snapshot(tenant_id, session_id, limits)
        if status != "ok":
            raise BudgetExceededError(status, snapshot)
        return snapshot

    async def record_usage(
        self,
        *,
        tenant_id: str,
        session_id: str,
        limits: SessionBudget,
        report: UsageReport,
        cost_usd: Decimal,
        ttl_seconds: int,
    ) -> BudgetSnapshot:
        with tracer.start_as_current_span("gateway.budget.record_usage"):
            await self._record_script(tenant_id, session_id, report, cost_usd, ttl_seconds)
        return await self.snapshot(tenant_id, session_id, limits)

    async def _record_script(
        self,
        tenant_id: str,
        session_id: str,
        report: UsageReport,
        cost_usd: Decimal,
        ttl_seconds: int,
    ) -> None:
        await self._record(
            keys=[self._keys.budget(tenant_id, session_id)],
            args=[
                report.input_tokens,
                report.output_tokens,
                report.cache_creation_input_tokens,
                report.cache_read_input_tokens,
                usd_to_micros(cost_usd),
                ttl_seconds,
            ],
        )

    async def first_time(
        self, tenant_id: str, session_id: str, flag: Literal["warned", "exhausted"]
    ) -> bool:
        """True exactly once per session ledger and flag (de-duplicates notifications)."""
        return bool(
            await self._redis.hsetnx(self._keys.budget(tenant_id, session_id), f"flag_{flag}", "1")
        )
