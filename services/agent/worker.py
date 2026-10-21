"""Agent worker: consumes the job queue and executes runs.

Runs in the API process by default (in-process queue). With a Redis queue the
same worker can run as a separate process (`python -m services.agent.worker`),
which is the path to isolated worker machines.
"""
from __future__ import annotations

import asyncio
import signal
from typing import Any

from core.logging import get_logger
from core.models import TaskRun, TaskState
from core.queue import JobQueue
from core.db import get_session_factory
from services.agent.engine import execute_run

log = get_logger("nexusforge.worker")


class AgentWorker:
    def __init__(self, concurrency: int = 2) -> None:
        self.concurrency = concurrency
        self._tasks: set[asyncio.Task] = set()
        self._stop = asyncio.Event()
        self._consumer: asyncio.Task | None = None

    async def start(self, queue: JobQueue) -> asyncio.Task:
        self._consumer = asyncio.create_task(self._consume(queue),
                                             name="agent-worker")
        log.info("agent worker started", extra={"concurrency": self.concurrency})
        return self._consumer

    async def stop(self) -> None:
        self._stop.set()
        if self._consumer:
            self._consumer.cancel()
            try:
                await self._consumer
            except asyncio.CancelledError:
                pass
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        log.info("agent worker stopped")

    async def _consume(self, queue: JobQueue) -> None:
        log.info("agent worker consumer loop started")
        while not self._stop.is_set():
            try:
                async for job in queue.consume(worker_id="api", timeout=1.0):
                    run_id = job.get("run_id")
                    if not run_id:
                        continue
                    log.info("agent worker picked up job", extra={"run_id": run_id})
                    # enforce concurrency
                    while len(self._tasks) >= self.concurrency:
                        done, _ = await asyncio.wait(
                            self._tasks, return_when=asyncio.FIRST_COMPLETED)
                        self._tasks -= done
                        for t in done:
                            if t.exception() and not isinstance(
                                t.exception(), asyncio.CancelledError):
                                log.error("run task crashed",
                                          exc_info=t.exception())
                    task = asyncio.create_task(
                        self._guarded(run_id), name=f"run-{run_id[:8]}")
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("queue consumer error; retrying in 1s")
                await asyncio.sleep(1)

    async def _guarded(self, run_id: str) -> None:
        try:
            await execute_run(run_id)
        except Exception:
            log.exception("execute_run failed", extra={"run_id": run_id})
            # mark failed so the UI never shows a stuck run
            from sqlalchemy import select, selectinload

            from core.models import TaskEvent

            factory = get_session_factory()
            async with factory() as db:
                run = (
                    await db.execute(
                        select(TaskRun).where(TaskRun.id == run_id).options(
                            selectinload(TaskRun.steps),
                            selectinload(TaskRun.events),
                        )
                    )
                ).scalar_one_or_none()
                if run is not None and run.state not in {
                    s.value for s in (TaskState.COMPLETED, TaskState.FAILED,
                                      TaskState.CANCELLED, TaskState.TIMEOUT,
                                      TaskState.SECURITY_BLOCKED)}:
                    run.state = TaskState.FAILED.value
                    run.error = "worker crashed while executing run"
                    seq = max((e.seq for e in run.events), default=0) + 1
                    db.add(TaskEvent(run_id=run.id, seq=seq, type="run_failed",
                                     payload={"error": run.error}))
                    await db.commit()
                    from core.events import event_bus

                    await event_bus.publish(
                        f"run:{run.id}",
                        {"type": "run_failed", "seq": seq, "error": run.error},
                    )


agent_worker = AgentWorker()


def main() -> None:
    """Standalone worker entrypoint (used with a Redis queue)."""
    from core.db import dispose_engine
    from core.logging import setup_logging
    from core.queue import get_queue

    setup_logging()

    async def _run() -> None:
        queue = get_queue()
        await queue.start()
        worker = AgentWorker()
        await worker.start(queue)
        stop = asyncio.Event()

        def _sig(*_):
            stop.set()

        loop = asyncio.get_running_loop()
        for s in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(s, _sig)
            except NotImplementedError:
                pass
        await stop.wait()
        await worker.stop()
        await queue.stop()
        await dispose_engine()

    asyncio.run(_run())


if __name__ == "__main__":
    main()