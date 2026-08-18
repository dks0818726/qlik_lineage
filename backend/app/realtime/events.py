from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any

from app.models.entities import LineageEvent

logger = logging.getLogger(__name__)


class EventBus:
    """Async fan-out hub for realtime lineage events.

    Multiple WebSocket/SSE subscribers can register; producers (the scanner)
    publish events that are broadcast to every live subscriber queue.
    """

    def __init__(self, max_queue: int = 1024) -> None:
        self._subscribers: set[asyncio.Queue[LineageEvent]] = set()
        self._max_queue = max_queue
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[LineageEvent]:
        queue: asyncio.Queue[LineageEvent] = asyncio.Queue(maxsize=self._max_queue)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[LineageEvent]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    async def publish(self, event: LineageEvent) -> None:
        async with self._lock:
            stale: list[asyncio.Queue[LineageEvent]] = []
            for queue in self._subscribers:
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    logger.warning("Dropping subscriber due to full queue")
                    stale.append(queue)
            for queue in stale:
                self._subscribers.discard(queue)

    def publish_sync(self, event: LineageEvent) -> None:
        """Fire-and-forget publish from sync code (e.g. scanner)."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(self.publish(event), loop)
                return
        except RuntimeError:
            pass
        # Fallback: create a one-shot loop
        asyncio.run(self.publish(event))


# Module-level singleton used by FastAPI routers + scanner.
event_bus = EventBus()


def lineage_event(event_type: str, payload: dict[str, Any]) -> LineageEvent:
    return LineageEvent(
        event_type=event_type,
        payload=payload,
        timestamp=dt.datetime.utcnow().isoformat() + "Z",
    )
