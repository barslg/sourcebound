"""In-process event bus fed by Postgres LISTEN/NOTIFY.

execute_operation() persists a notification row, issues pg_notify on
the muse_events channel, and publishes to this bus. The SSE route
subscribes per user; a lifespan listener forwards cross-process
NOTIFY payloads into the same bus. No Redis anywhere.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any
from uuid import UUID

CHANNEL = "muse_events"

_Subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
_lock = asyncio.Lock()


async def publish_event(user_id: UUID, payload: dict[str, Any]) -> None:
    """Deliver a payload to every subscriber of this user."""
    key = str(user_id)
    async with _lock:
        queues = list(_Subscribers.get(key, ()))
    for queue in queues:
        queue.put_nowait(dict(payload))


async def subscribe(user_id: UUID) -> Any:
    """Yield events for a user until cancelled; unsubscribe in finally."""
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    key = str(user_id)
    async with _lock:
        _Subscribers[key].add(queue)
    try:
        while True:
            yield await queue.get()
    finally:
        async with _lock:
            _Subscribers[key].discard(queue)
            if not _Subscribers[key]:
                _Subscribers.pop(key, None)


def subscriber_count(user_id: UUID) -> int:
    """How many live subscriptions a user has (tests and health)."""
    return len(_Subscribers.get(str(user_id), ()))


async def listen_forever(database_url: str) -> None:
    """Bridge Postgres NOTIFY into the bus so every worker process reaches SSE.

    Runs until cancelled; the lifespan starts it and stops it on shutdown.
    """
    import json

    import asyncpg  # type: ignore[import-untyped]

    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    loop = asyncio.get_running_loop()
    conn = await asyncpg.connect(dsn)
    try:

        def _forward(_conn: object, _pid: int, _channel: str, payload: str) -> None:
            try:
                data = json.loads(payload)
                user_id = data.get("user_id")
            except ValueError:
                return
            if not user_id:
                return
            key = str(user_id)
            for queue in list(_Subscribers.get(key, ())):
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"operation_id": data.get("operation_id"), "status": data.get("status")},
                )

        await conn.add_listener(CHANNEL, _forward)
        await asyncio.Future()
    finally:
        await conn.close()
