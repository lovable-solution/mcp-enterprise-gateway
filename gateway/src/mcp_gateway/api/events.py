"""Live event stream for the operator console (server-sent events).

``GET /v1/events`` streams the caller's tenant events. Approval events need
``approvals:read``; session, budget and breaker events need ``sessions:read``. Each SSE
``id`` is the Redis stream id, so a reconnecting client sends ``Last-Event-ID`` and
resumes exactly where it stopped. Streams are served from the process-wide event hub, so
open consoles hold no Redis connection of their own.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Annotated, Final

from fastapi import APIRouter, Header, Request
from sse_starlette import EventSourceResponse

from ..errors import Forbidden
from ..events.bus import APPROVAL_EVENTS, GatewayEvent
from ..events.hub import SubscriptionLaggedError
from ..policy.models import ControlAction
from .deps import PrincipalDep, RuntimeDep

router = APIRouter(tags=["events"])

_STREAM_ID: Final = re.compile(r"^\d+-\d+$")
_POLL_SECONDS: Final = 5.0


@router.get("/v1/events", summary="Server-sent events for the caller's tenant")
async def stream_events(
    request: Request,
    runtime: RuntimeDep,
    principal: PrincipalDep,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> EventSourceResponse:
    can_read_approvals = runtime.engine.allows_action(principal.role, ControlAction.APPROVALS_READ)
    can_read_sessions = runtime.engine.allows_action(principal.role, ControlAction.SESSIONS_READ)
    if not (can_read_approvals or can_read_sessions):
        raise Forbidden(f"role {principal.role!r} may not read events")
    resume_after = last_event_id if last_event_id and _STREAM_ID.fullmatch(last_event_id) else None

    def visible(event: GatewayEvent) -> bool:
        return can_read_approvals if event.type in APPROVAL_EVENTS else can_read_sessions

    async def events() -> AsyncIterator[dict[str, str]]:
        try:
            async with runtime.hub.subscribe(principal.tenant_id, after=resume_after) as feed:
                while not await request.is_disconnected():
                    event = await feed.next(timeout=_POLL_SECONDS)
                    if event is not None and event.id is not None and visible(event):
                        yield {"id": event.id, "event": event.type, "data": event.model_dump_json()}
        except SubscriptionLaggedError:
            return  # too slow: end the stream; the client resumes with Last-Event-ID

    return EventSourceResponse(events(), ping=int(runtime.settings.sse_keepalive_seconds))
