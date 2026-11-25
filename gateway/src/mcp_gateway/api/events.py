"""Live event stream for the operator console (server-sent events).

``GET /v1/events`` streams the caller's tenant events. Approval events need
``approvals:read``; session, budget and breaker events need ``sessions:read``. Each SSE
``id`` is the Redis stream id, so a reconnecting client sends ``Last-Event-ID`` and
resumes exactly where it stopped.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Annotated, Final

from fastapi import APIRouter, Header, Request
from sse_starlette import EventSourceResponse

from ..errors import Forbidden
from ..events.bus import APPROVAL_EVENTS, GatewayEvent
from ..policy.models import ControlAction
from .deps import PrincipalDep, RuntimeDep

router = APIRouter(tags=["events"])

_STREAM_ID: Final = re.compile(r"^\d+-\d+$")
_BLOCK_MS: Final = 5_000


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
    if last_event_id is not None and _STREAM_ID.fullmatch(last_event_id):
        start = last_event_id
    else:
        start = await runtime.bus.last_id(principal.tenant_id)

    def visible(event: GatewayEvent) -> bool:
        return can_read_approvals if event.type in APPROVAL_EVENTS else can_read_sessions

    async def events() -> AsyncIterator[dict[str, str]]:
        position = start
        while not await request.is_disconnected():
            for event in await runtime.bus.read(principal.tenant_id, position, block_ms=_BLOCK_MS):
                position = event.id or position
                if visible(event):
                    yield {"id": position, "event": event.type, "data": event.model_dump_json()}

    return EventSourceResponse(events(), ping=int(runtime.settings.sse_keepalive_seconds))
