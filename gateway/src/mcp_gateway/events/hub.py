"""In-process fan-out of tenant event streams.

One background reader per tenant (a single blocking ``XREAD``) feeds every subscriber in
this process. SSE clients and approval waiters therefore hold no Redis connection of their
own, and a burst of open consoles or held calls cannot exhaust the pool that budget and
loop checks depend on.

Guarantees:
* no lost wake-ups: a subscription takes its starting position *before* it registers,
  so anything published after ``subscribe`` returns is delivered;
* resumable without gaps or duplicates: ``subscribe(after=id)`` pages through the stored
  backlog first, however long, then continues with live events of a strictly greater id;
* bounded memory: a subscriber that falls ``queue_size`` events behind is dropped, and its
  next read raises :class:`SubscriptionLaggedError` (SSE clients reconnect with Last-Event-ID).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Final

from .bus import EventBus, GatewayEvent, stream_position

logger = logging.getLogger(__name__)

_LAGGED: Final = object()


class SubscriptionLaggedError(Exception):
    """The subscriber fell too far behind and was dropped; resume from the stream."""


BacklogPage = Callable[[str], Awaitable[list[GatewayEvent]]]
"""Stored events strictly after the given id, one page at a time."""


class Subscription:
    def __init__(
        self,
        queue: asyncio.Queue[object],
        *,
        start: str,
        backlog: BacklogPage | None = None,
        page_size: int = 0,
    ) -> None:
        self._queue = queue
        self._last_id = start
        self._floor = stream_position(start)
        self._next_page = backlog
        self._page_size = page_size
        self._backlog: deque[GatewayEvent] = deque()

    async def next(self, timeout: float | None = None) -> GatewayEvent | None:
        """The next event, or None when ``timeout`` seconds pass without one."""
        if not self._backlog and self._next_page is not None:
            page = await self._next_page(self._last_id)
            if len(page) < self._page_size:
                self._next_page = None  # caught up with the stream: live events from here on
            self._backlog.extend(page)
        if self._backlog:
            return self._advance(self._backlog.popleft())
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            try:
                item = await asyncio.wait_for(self._queue.get(), remaining)
            except TimeoutError:
                return None
            if item is _LAGGED:
                raise SubscriptionLaggedError
            assert isinstance(item, GatewayEvent)  # noqa: S101
            if stream_position(item.id or "0-0") <= self._floor:
                continue  # already delivered from the backlog, or older than the subscription
            return self._advance(item)

    def _advance(self, event: GatewayEvent) -> GatewayEvent:
        self._last_id = event.id or self._last_id
        self._floor = stream_position(self._last_id)
        return event


class EventHub:
    def __init__(
        self,
        bus: EventBus,
        *,
        block_ms: int = 5_000,
        queue_size: int = 1_000,
        backlog_page_size: int = 500,
    ) -> None:
        self._bus = bus
        self._block_ms = block_ms
        self._queue_size = queue_size
        self._page_size = backlog_page_size
        self._subscribers: dict[str, set[asyncio.Queue[object]]] = {}
        self._readers: dict[str, asyncio.Task[None]] = {}

    @asynccontextmanager
    async def subscribe(
        self, tenant_id: str, *, after: str | None = None
    ) -> AsyncIterator[Subscription]:
        """Events of ``tenant_id``: those after ``after`` (backlog, then live) or, without
        it, those published from now on."""
        live_from = await self._bus.last_id(tenant_id)
        queue: asyncio.Queue[object] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.setdefault(tenant_id, set()).add(queue)
        reader = self._readers.get(tenant_id)
        if reader is None or reader.done():
            self._readers[tenant_id] = asyncio.create_task(
                self._read(tenant_id, live_from), name=f"events:{tenant_id}"
            )
        try:
            if after is None or stream_position(after) >= stream_position(live_from):
                # Nothing stored after `after` (a future id is treated as "now").
                subscription = Subscription(queue, start=live_from)
            else:

                async def page(position: str) -> list[GatewayEvent]:
                    return await self._bus.range_after(tenant_id, position, count=self._page_size)

                subscription = Subscription(
                    queue, start=after, backlog=page, page_size=self._page_size
                )
            yield subscription
        finally:
            self._unsubscribe(tenant_id, queue)

    async def aclose(self) -> None:
        readers = list(self._readers.values())
        self._readers.clear()
        self._subscribers.clear()
        for reader in readers:
            reader.cancel()
        for reader in readers:
            with contextlib.suppress(asyncio.CancelledError):
                await reader

    def subscriber_count(self, tenant_id: str) -> int:
        return len(self._subscribers.get(tenant_id, ()))

    def _unsubscribe(self, tenant_id: str, queue: asyncio.Queue[object]) -> None:
        subscribers = self._subscribers.get(tenant_id)
        if subscribers is None:
            return
        subscribers.discard(queue)
        if not subscribers:
            del self._subscribers[tenant_id]
            reader = self._readers.pop(tenant_id, None)
            if reader is not None:
                reader.cancel()

    async def _read(self, tenant_id: str, position: str) -> None:
        while True:
            try:
                events = await self._bus.read(tenant_id, position, block_ms=self._block_ms)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "event stream read failed for tenant %s; retrying", tenant_id, exc_info=True
                )
                await asyncio.sleep(1.0)
                continue
            for event in events:
                position = event.id or position
                for queue in list(self._subscribers.get(tenant_id, ())):
                    try:
                        queue.put_nowait(event)
                    except asyncio.QueueFull:
                        self._drop(tenant_id, queue)

    def _drop(self, tenant_id: str, queue: asyncio.Queue[object]) -> None:
        """Replace a slow subscriber's backlog with a lag marker and stop feeding it."""
        while not queue.empty():
            queue.get_nowait()
        queue.put_nowait(_LAGGED)
        subscribers = self._subscribers.get(tenant_id)
        if subscribers is not None:
            subscribers.discard(queue)
