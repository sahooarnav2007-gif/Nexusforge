"""Job queue abstraction.

- InProcessQueue: asyncio queue consumed by worker tasks in the API process
  (default for local dev — zero extra services).
- RedisQueue: LPUSH/BRPOP across processes (production / multi-worker).

Both expose the same `enqueue` / `consume` contract so the agent worker is
backend-agnostic and can later move to isolated worker machines.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from core.config import get_settings
from core.logging import get_logger

log = get_logger("nexusforge.queue")


class JobQueue:
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def enqueue(self, job: dict[str, Any]) -> None: ...

    def consume(self, worker_id: str, timeout: float = 5.0) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError


class InProcessQueue(JobQueue):
    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._wakeup: dict[str, asyncio.Event] = {}

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def enqueue(self, job: dict[str, Any]) -> None:
        await self._queue.put(job)
        log.debug("job enqueued (in-process)", extra={"run_id": job.get("run_id")})

    async def consume(self, worker_id: str, timeout: float = 5.0) -> AsyncIterator[dict[str, Any]]:
        while True:
            try:
                job = await asyncio.wait_for(self._queue.get(), timeout=timeout)
                yield job
            except asyncio.TimeoutError:
                return


class RedisQueue(JobQueue):  # pragma: no cover - requires redis
    def __init__(self) -> None:
        self._redis = None

    async def start(self) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(get_settings().redis_url, decode_responses=True)
        await self._redis.ping()

    async def stop(self) -> None:
        if self._redis:
            await self._redis.aclose()

    async def enqueue(self, job: dict[str, Any]) -> None:
        assert self._redis is not None
        await self._redis.lpush(get_settings().queue_name, json.dumps(job, default=str))

    async def consume(self, worker_id: str, timeout: float = 5.0) -> AsyncIterator[dict[str, Any]]:
        assert self._redis is not None
        name = get_settings().queue_name
        while True:
            item = await self._redis.brpop(name, timeout=int(timeout) or 1)
            if item is None:
                return
            _, raw = item
            yield json.loads(raw)


_queue: JobQueue | None = None


def get_queue() -> JobQueue:
    """Process-wide singleton so producers and the worker share one queue."""
    global _queue
    if _queue is None:
        s = get_settings()
        if s.redis_url:
            try:
                _queue = RedisQueue()
            except Exception:  # pragma: no cover
                log.warning("redis queue unavailable; using in-process queue")
                _queue = InProcessQueue()
        else:
            _queue = InProcessQueue()
    return _queue


def reset_queue() -> None:
    """Test helper."""
    global _queue
    _queue = None