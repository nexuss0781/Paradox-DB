"""SSE endpoint for real-time version change notifications."""

import asyncio
import json
import time
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import DatabaseVersion, UserChannel
from app.telegram_logger import log_operation

router = APIRouter()

# In-memory event queue per user
_user_events: dict[str, asyncio.Queue] = {}


def notify_user(user_id: str, database_name: str, version: int, message_id: str):
    """Push a version change event to all listeners of this user."""
    if user_id in _user_events:
        event = json.dumps({
            "type": "version_change",
            "database_name": database_name,
            "version": version,
            "message_id": message_id,
            "timestamp": time.time(),
        })
        try:
            _user_events[user_id].put_nowait(event)
        except asyncio.QueueFull:
            pass


@router.get("/notifications")
async def notifications(
    request: Request,
    user: UserChannel = Depends(get_current_user),
):
    """SSE stream of version change events for this user."""
    uid = user.user_id
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    _user_events.setdefault(uid, queue)

    async def event_generator():
        try:
            # Send initial connection event
            yield f"data: {json.dumps({'type': 'connected', 'user_id': uid})}\n\n"
            
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break
                
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {event}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield f": keepalive {time.time()}\n\n"
        finally:
            # Cleanup
            if uid in _user_events and _user_events[uid] is queue:
                del _user_events[uid]

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
