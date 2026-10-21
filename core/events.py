"""In-process event bus with an optional Redis pub/sub backend.

Task events are ALWAYS persisted to the DB (TaskEvent rows). The bus is only
the live fan-out channel for SSE; subscribers can always replay from the DB.
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any

from core.config import get_settings
from core.logging import get_logger

log = get_logger("nexusforge.events")


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._lock = asyncio.Lock()
        self._redis = None
        self._redis_subscriber_task: asyncio.Task | None = None

    async def start(self) -> None:
        url = get_settings().redis_url
        if url:
            try:
                import redis.asyncio as aioredis

                self._redis = aioredis.from_url(url, decode_responses=True)
                await self._redis.ping()
                self._redis_subscriber_task = asyncio.create_task(
                    self._redis_subscriber(), name="eventbus-redis-sub"
                )
                log.info("event bus using redis pub/sub")
            except Exception:  # pragma: no cover - degraded to local
                log.exception("redis unavailable; falling back to in-process bus")
                self._redis = None

    async def stop(self) -> None:
        if self._redis_subscriber_task:
            self._redis_subscriber_task.cancel()
        if self._redis:
            await self._redis.aclose()

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        if self._redis is not None:
            try:
                await self._redis.publish(channel, json.dumps(message, default=str))
                return
            except Exception:
                log.exception("redis publish failed; falling back to local")
        async with self._lock:
            queues = list(self._subscribers.get(channel, ()))
        for q in queues:
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:  # pragma: no cover
                log.warning("subscriber queue full; dropping event", extra={"channel": channel})

    def subscribe(self, channel: str, maxsize: int = 1000) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subscribers[channel].add(q)
        return q

    def unsubscribe(self, channel: str, q: asyncio.Queue) -> None:
        self._subscribers.get(channel, set()).discard(q)

    async def _redis_subscriber(self) -> None:  # pragma: no cover
        assert self._redis is not None
        pubsub = self._redis.pubsub()
        await pubsub.psubscribe("*")
        try:
            async for msg in pubsub.listen():
                if msg.get("type") != "pmessage":
                    continue
                channel = msg["channel"]
                try:
                    payload = json.loads(msg["data"])
                except (TypeError, json.JSONDecodeError):
                    continue
                async with self._lock:
                    queues = list(self._subscribers.get(channel, ()))
                for q in queues:
                    try:
                        q.put_nowait(payload)
                    except asyncio.QueueFull:
                        pass
        finally:
            await pubsub.aclose()


event_bus = EventBus()