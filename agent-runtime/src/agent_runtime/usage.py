"""Report every model response's token usage to the gateway's session budget.

The gateway enforces one budget per session over tool calls *and* model spend; it can
only count the tokens the runtime reports. This capability posts each response's usage
right after it arrives, so a session that exhausts its budget is refused its next tool
call. If usage cannot be reported, the run stops (fails closed): an agent whose spend is
no longer accounted for must not keep spending.

PydanticAI normalises ``input_tokens`` to include cache reads and writes; the gateway's
report follows Anthropic's ``usage`` object, where they are counted separately, so they
are subtracted here (otherwise cached tokens would be charged twice).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import KW_ONLY, dataclass
from typing import Any, Final

from pydantic_ai import ModelResponse, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import ModelRequestContext

from .gateway import GatewayError
from .toolset import AgentDeps, RuntimeEvent

logger = logging.getLogger(__name__)

_ATTEMPTS: Final = 3
_SERVER_ERROR: Final = 500


class UsageReportingError(Exception):
    """The gateway did not accept a usage report; the run's spend is unaccounted for."""


def usage_report(response: ModelResponse) -> dict[str, Any]:
    """The gateway ``UsageReport`` for one model response."""
    usage = response.usage
    cache_writes = usage.cache_write_tokens
    writes_1h = min(usage.cache_write_1h_tokens, cache_writes)
    report: dict[str, Any] = {
        "model": response.model_name or "unknown",
        "input_tokens": max(0, usage.input_tokens - usage.cache_read_tokens - cache_writes),
        "output_tokens": usage.output_tokens,
        "cache_creation_input_tokens": cache_writes,
        "cache_read_input_tokens": usage.cache_read_tokens,
    }
    if cache_writes:
        report["cache_creation"] = {
            "ephemeral_5m_input_tokens": cache_writes - writes_1h,
            "ephemeral_1h_input_tokens": writes_1h,
        }
    return report


@dataclass
class GatewayUsageReporter(AbstractCapability[AgentDeps]):
    """Posts each model response's usage to ``/v1/sessions/{sid}/usage``."""

    _: KW_ONLY
    id: str | None = "gateway_usage_reporter"
    retry_delay_seconds: float = 0.5

    async def after_model_request(
        self,
        ctx: RunContext[AgentDeps],
        *,
        request_context: ModelRequestContext,
        response: ModelResponse,
    ) -> ModelResponse:
        report = usage_report(response)
        if not (
            report["input_tokens"] or report["output_tokens"] or report["cache_read_input_tokens"]
        ):
            return response  # nothing was billed (e.g. a request that failed before output)
        accepted = await self._post(ctx.deps, report)
        ctx.deps.budget = accepted.get("budget")
        await ctx.deps.emit(
            RuntimeEvent(
                "usage",
                f"{report['model']}: ${accepted.get('cost_usd', '?')}",
                {"report": report, "budget": ctx.deps.budget},
            )
        )
        return response

    async def _post(self, deps: AgentDeps, report: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(1, _ATTEMPTS + 1):
            try:
                return await deps.gateway.report_usage(report)
            except GatewayError as exc:
                if exc.status_code is not None and exc.status_code < _SERVER_ERROR:
                    raise UsageReportingError(str(exc)) from exc  # will not succeed on retry
                logger.warning("usage report failed (attempt %d/%d): %s", attempt, _ATTEMPTS, exc)
                if attempt == _ATTEMPTS:
                    raise UsageReportingError(str(exc)) from exc
                await asyncio.sleep(self.retry_delay_seconds * attempt)
        raise AssertionError("unreachable")  # pragma: no cover
