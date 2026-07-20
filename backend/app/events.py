"""Redis pub/sub event hub fanned out to WebSocket clients.

Every event is a JSON object with at least {"type": ...}; events that concern a
specific drive carry "chat_id" and are delivered only to members of that chat
(admins receive everything).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from redis.asyncio import Redis

logger = logging.getLogger("tup.events")

CHANNEL = "tup:events"


class EventHub:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def publish(self, event: dict[str, Any]) -> None:
        try:
            await self._redis.publish(CHANNEL, json.dumps(event, ensure_ascii=False))
        except Exception:
            logger.exception("Failed to publish event %s", event.get("type"))

    async def subscribe(self) -> "EventSubscription":
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(CHANNEL)
        return EventSubscription(pubsub)


class EventSubscription:
    def __init__(self, pubsub: Any) -> None:
        self._pubsub = pubsub

    async def next_event(self) -> dict[str, Any] | None:
        message = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=30.0)
        if message is None or message.get("type") != "message":
            return None
        try:
            return json.loads(message["data"])
        except (ValueError, TypeError):
            return None

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self._pubsub.unsubscribe(CHANNEL)
            await self._pubsub.aclose()


async def forward_events(
    subscription: EventSubscription,
    send_json: Any,
    allowed_chats: set[str],
    is_admin: bool,
) -> None:
    """Pump events to one WebSocket, filtered by drive membership."""
    while True:
        event = await subscription.next_event()
        if event is None:
            continue
        chat_id = event.get("chat_id")
        if chat_id is not None and not is_admin and str(chat_id) not in allowed_chats:
            continue
        await send_json(event)


def cancel_task(task: asyncio.Task[Any] | None) -> None:
    if task is not None and not task.done():
        task.cancel()
