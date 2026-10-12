from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import Callable, Coroutine, Mapping
from typing import Any

from adapters.verification.ddddocr_provider import DdddOcrProvider
from broker.broker import SqliteTaskBroker
from broker.jobs import JobKind, JobRecord
from broker.outbox import OutboxWriter
from broker.schema import create_broker_schema
from storage.database import Database

from .handlers.hydrate import execute_event_hydrate
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
    JobKind.EVENT_HYDRATE: execute_event_hydrate,
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

        # 自訂 handlers 的嵌入式使用者不一定需要 OCR；正式 Worker 則提前備妥。
        self._warm_ocr_on_start = handlers is None
        self._active_jobs: dict[str, asyncio.Task[None]] = {}
        self._active_records: dict[str, JobRecord] = {}
        self._preempted: set[str] = set()
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
                    error="interrupted_for_purchase" if job.id in self._preempted else "cancelled",
                    retry=False,
                )
            raise
        except Exception as exc:
            with contextlib.suppress(Exception):
                await self._broker.fail(
                    job.id, worker_id=self._worker_id, error=str(exc), retry=False
                )
            error_task_id = job.task_id
            if error_task_id is None:
                error_task_id = job.id
            self._outbox.publish(
                task_id=error_task_id,
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
            self._active_jobs.pop(job.id, None)
            self._active_records.pop(job.id, None)
            self._preempted.discard(job.id)

    async def run_once(self) -> bool:
        """測試與單步執行用：嘗試領取一個 job，執行完成後回傳是否有領到。"""
        await self.initialize()
        job = await self._claim_next()
        if job is None:
            return False
        task = self._start_job(job)
        try:
            await task
        finally:
            await self._outbox.flush()
        return True

    def _start_job(self, job: JobRecord) -> asyncio.Task[None]:
        # Reserve before yielding: a second claim must see this profile as busy.
        self._active_records[job.id] = job
        task = asyncio.create_task(self._execute_job(job))
        self._active_jobs[job.id] = task
        return task

    async def _claim_next(self) -> JobRecord | None:
        purchases = [
            j for j in self._active_records.values() if j.kind == JobKind.PURCHASE
        ]
        if len(purchases) < self._settings.max_concurrent_jobs:
            job = await self._broker.claim(
                worker_id=self._worker_id,
                kinds=[JobKind.PURCHASE],
                busy_profiles=tuple(j.profile for j in purchases),
            )
            if job is not None:
                # Session jobs may borrow the same Chrome. Drain their cancellation
                # before purchase starts so late navigation cannot steal its page.
                background = [
                    self._active_jobs[j.id]
                    for j in self._active_records.values()
                    if j.kind != JobKind.PURCHASE
                ]
                for record in tuple(self._active_records.values()):
                    if record.kind != JobKind.PURCHASE:
                        self._preempted.add(record.id)
                        self._active_jobs[record.id].cancel()
                if background:
                    await asyncio.gather(*background, return_exceptions=True)
                return job
        if self._active_jobs:
            return None
        return await self._broker.claim(
            worker_id=self._worker_id,
            kinds=[kind for kind in JobKind if kind != JobKind.PURCHASE],
        )

    def stop(self) -> None:
        self._stop_event.set()

    async def _warm_ocr(self) -> None:
        # ibon 登入採 beta；其他圖片辨識共用標準模型。兩者沿用 provider 快取。
        for beta in (True, False):
            if not await DdddOcrProvider(beta=beta).warmup():
                logger.warning("OCR model preparation unavailable (beta=%s)", beta)

    async def run_forever(self) -> None:
        """正式運行迴圈：定期 claim、執行、heartbeat、reap_expired、優雅關閉。"""
        await self.initialize()
        outbox_task = asyncio.create_task(self._outbox.run())
        ocr_task = (
            asyncio.create_task(self._warm_ocr()) if self._warm_ocr_on_start else None
        )

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(sig, self.stop)

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

                job = await self._claim_next()
                if job is not None:
                    self._start_job(job)
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

            if ocr_task is not None:
                ocr_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await ocr_task

            outbox_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await outbox_task
            await self._outbox.aclose()
