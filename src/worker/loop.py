from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import Callable, Coroutine, Mapping
from typing import Any

from broker.broker import SqliteTaskBroker
from broker.jobs import JobKind, JobRecord
from broker.outbox import OutboxWriter
from broker.schema import create_broker_schema
from storage.database import Database

from .handlers.purchase import execute_purchase
from .handlers.session import execute_session_job
from .settings import WorkerSettings

logger = logging.getLogger(__name__)

JobHandler = Callable[..., Coroutine[Any, Any, None]]

DEFAULT_HANDLERS: dict[JobKind, JobHandler] = {
    JobKind.PURCHASE: execute_purchase,
    JobKind.SESSION_CHECK: execute_session_job,
    JobKind.AUTO_LOGIN: execute_session_job,
    JobKind.MANUAL_LOGIN: execute_session_job,
}


class WorkerLoop:
    """獨立 Worker 領取與排程迴圈。"""

    def __init__(
        self,
        db: Database,
        broker: SqliteTaskBroker,
        outbox: OutboxWriter,
        *,
        worker_id: str,
        settings: WorkerSettings,
        handlers: Mapping[JobKind, JobHandler] | None = None,
    ) -> None:
        self._db = db
        self._broker = broker
        self._outbox = outbox
        self._worker_id = worker_id
        self._settings = settings
        self._handlers: dict[JobKind, JobHandler] = (
            dict(handlers) if handlers is not None else dict(DEFAULT_HANDLERS)
        )

        self._busy_profiles: set[str] = set()
        self._active_jobs: dict[str, asyncio.Task[None]] = {}
        self._stop_event = asyncio.Event()
        self._schema_initialized = False

    async def initialize(self) -> None:
        if not self._schema_initialized:
            await create_broker_schema(self._db.engine)
            self._schema_initialized = True

    async def _heartbeat_worker(
        self, job_id: str, target_task: asyncio.Task[None]
    ) -> None:
        interval = max(0.2, self._settings.lease_ttl_s / 3.0)
        while not target_task.done():
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            if target_task.done():
                break
            try:
                ok = await self._broker.heartbeat(job_id, worker_id=self._worker_id)
                if not ok:
                    target_task.cancel()
                    break
            except Exception:
                logger.exception("Heartbeat failed for job %s", job_id)

    async def _execute_job(self, job: JobRecord) -> None:
        self._busy_profiles.add(job.profile)
        handler = self._handlers.get(job.kind)
        target_task: asyncio.Task[None] | None = None
        hb_task: asyncio.Task[None] | None = None

        try:
            if handler is None:
                await self._broker.fail(
                    job.id,
                    worker_id=self._worker_id,
                    error=f"unsupported_job_kind_{job.kind.value}",
                    retry=False,
                )
                return

            loop = asyncio.get_running_loop()
            target_task = loop.create_task(
                handler(
                    job,
                    worker_id=self._worker_id,
                    db=self._db,
                    broker=self._broker,
                    outbox=self._outbox,
                    settings=self._settings,
                )
            )
            hb_task = loop.create_task(self._heartbeat_worker(job.id, target_task))

            await target_task
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await self._broker.fail(
                    job.id,
                    worker_id=self._worker_id,
                    error="cancelled",
                    retry=False,
                )
            raise
        except Exception as exc:
            with contextlib.suppress(Exception):
                await self._broker.fail(
                    job.id, worker_id=self._worker_id, error=str(exc), retry=False
                )
            self._outbox.publish(
                task_id=job.task_id or job.id,
                type="ERROR",
                payload={
                    "name": "job_execution_failed",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                ephemeral=False,
            )
        finally:
            if hb_task is not None:
                hb_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await hb_task
            self._busy_profiles.discard(job.profile)
            self._active_jobs.pop(job.id, None)

    async def run_once(self) -> bool:
        """測試與單步執行用：嘗試領取一個 job，執行完成後回傳是否有領到。"""
        await self.initialize()
        if len(self._active_jobs) >= self._settings.max_concurrent_jobs:
            return False

        job = await self._broker.claim(
            worker_id=self._worker_id,
            busy_profiles=tuple(self._busy_profiles),
        )
        if job is None:
            return False

        task = asyncio.create_task(self._execute_job(job))
        self._active_jobs[job.id] = task
        try:
            await task
        finally:
            await self._outbox.flush()
        return True

    def stop(self) -> None:
        self._stop_event.set()

    async def run_forever(self) -> None:
        """正式運行迴圈：定期 claim、執行、heartbeat、reap_expired、優雅關閉。"""
        await self.initialize()
        outbox_task = asyncio.create_task(self._outbox.run())

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.stop)
            except (NotImplementedError, RuntimeError):
                # e.g. on non-main thread or Windows
                pass

        poll_interval = max(0.01, self._settings.poll_ms / 1000.0)
        reap_interval = max(1.0, self._settings.lease_ttl_s)
        last_reap = 0.0

        try:
            while not self._stop_event.is_set():
                now = loop.time()
                if now - last_reap >= reap_interval:
                    with contextlib.suppress(Exception):
                        await self._broker.reap_expired()
                    last_reap = now

                # 檢查目前併發上限
                if len(self._active_jobs) < self._settings.max_concurrent_jobs:
                    job = await self._broker.claim(
                        worker_id=self._worker_id,
                        busy_profiles=tuple(self._busy_profiles),
                    )
                    if job is not None:
                        t = asyncio.create_task(self._execute_job(job))
                        self._active_jobs[job.id] = t
                        continue

                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=poll_interval
                    )

        finally:
            # 優雅關閉
            for t in list(self._active_jobs.values()):
                t.cancel()
            if self._active_jobs:
                await asyncio.gather(
                    *self._active_jobs.values(), return_exceptions=True
                )
            self._active_jobs.clear()

            outbox_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await outbox_task
            await self._outbox.aclose()
