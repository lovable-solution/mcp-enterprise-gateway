"""Single entry point for emitting gateway events: stream first, webhooks second."""

from __future__ import annotations

from typing import Any

from .bus import EventBus, EventType, GatewayEvent
from .webhooks import WebhookDispatcher


class Notifier:
    def __init__(self, bus: EventBus, webhooks: WebhookDispatcher | None) -> None:
        self._bus = bus
        self._webhooks = webhooks

    async def emit(
        self,
        event_type: EventType,
        *,
        tenant_id: str,
        session_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> GatewayEvent:
        event = await self._bus.publish(
            GatewayEvent(
                type=event_type, tenant_id=tenant_id, session_id=session_id, data=data or {}
            )
        )
        if self._webhooks is not None:
            self._webhooks.submit(event)
        return event
