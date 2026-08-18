from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from app.models.entities import LineageEvent
from app.realtime.events import event_bus

router = APIRouter(tags=["realtime"])
logger = logging.getLogger(__name__)


def _serialize(event: LineageEvent) -> str:
    return json.dumps(
        {"type": event.event_type, "payload": event.payload, "ts": event.timestamp}
    )


@router.websocket("/ws/lineage")
async def lineage_ws(websocket: WebSocket) -> None:
    """Stream realtime lineage events to a connected client."""
    await websocket.accept()
    queue = await event_bus.subscribe()
    try:
        while True:
            event = await queue.get()
            await websocket.send_text(_serialize(event))
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    finally:
        await event_bus.unsubscribe(queue)


@router.get("/events")
async def lineage_sse() -> StreamingResponse:
    """Server-Sent Events fallback for browsers/clients that prefer SSE."""
    queue = await event_bus.subscribe()

    async def gen():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"event: {event.event_type}\ndata: {_serialize(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            await event_bus.unsubscribe(queue)

    return StreamingResponse(gen(), media_type="text/event-stream")
