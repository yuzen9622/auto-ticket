from __future__ import annotations

import pytest

from broker.broker import SqliteTaskBroker
from broker.jobs import JobKind, JobState
from broker.outbox import OutboxWriter
from broker.schema import create_broker_schema
from storage.database import Database
from tests.netguard import netguard_autouse  # noqa: F401
from worker.loop import WorkerLoop
from worker.settings import WorkerSettings


@pytest.fixture
async def worker_env(db: Database):
    await create_broker_schema(db.engine)
    broker = SqliteTaskBroker(db, lease_ttl_s=2.0)
    outbox = OutboxWriter(db)
    settings = WorkerSettings(
        poll_ms=50,
        lease_ttl_s=2.0,
    )
    return db, broker, outbox, settings


async def test_worker_loop_claims_and_executes_job(worker_env) -> None:
    db, broker, outbox, settings = worker_env

    executed_jobs = []

    async def mock_handler(job, **kwargs):
        executed_jobs.append(job.id)
        await broker.complete(job.id, worker_id="test_worker_01", result={"ok": True})

    custom_handlers = {
        JobKind.PURCHASE: mock_handler,
    }

    loop = WorkerLoop(
        db,
        broker,
        outbox,
        worker_id="test_worker_01",
        settings=settings,
        handlers=custom_handlers,
    )

    # 1. 放入一個任務
    job_id = await broker.enqueue(
        kind=JobKind.PURCHASE,
        profile="live",
        payload={"spec": {"event_title": "Test"}},
    )

    # 2. 透過 run_once 領取並執行
    claimed = await loop.run_once()
    assert claimed is True

    # 3. 驗證任務已被領取並執行完畢
    assert job_id in executed_jobs
    job = await broker.get(job_id)
    assert job is not None
    assert job.state == JobState.DONE
    assert job.result == {"ok": True}


async def test_worker_loop_handles_exception_and_marks_failed(worker_env) -> None:
    db, broker, outbox, settings = worker_env

    async def failing_handler(job, **kwargs):
        raise RuntimeError("simulated worker crash")

    custom_handlers = {
        JobKind.PURCHASE: failing_handler,
    }

    loop = WorkerLoop(
        db,
        broker,
        outbox,
        worker_id="test_worker_01",
        settings=settings,
        handlers=custom_handlers,
    )

    job_id = await broker.enqueue(
        kind=JobKind.PURCHASE,
        profile="live",
        payload={},
    )

    claimed = await loop.run_once()
    assert claimed is True

    job = await broker.get(job_id)
    assert job is not None
    assert job.state == JobState.FAILED
    assert "simulated worker crash" in (job.error or "")


@pytest.mark.parametrize("warmup_fails", [False, True])
async def test_ocr_warmup_does_not_block_jobs_and_is_drained(worker_env, monkeypatch, warmup_fails):
    import asyncio

    from worker.loop import DEFAULT_HANDLERS

    db, broker, outbox, settings = worker_env
    warming = asyncio.Event()
    release = asyncio.Event()
    executed = asyncio.Event()
    drained = asyncio.Event()
    variants = []

    async def warmup(provider):
        variants.append(provider.beta)
        warming.set()
        try:
            await release.wait()
            return not warmup_fails
        finally:
            drained.set()

    async def handler(job, **kwargs):
        await warming.wait()
        assert not release.is_set()
        await broker.complete(job.id, worker_id="warmup", result={})
        executed.set()

    monkeypatch.setattr("adapters.verification.ddddocr_provider.DdddOcrProvider.warmup", warmup)
    monkeypatch.setitem(DEFAULT_HANDLERS, JobKind.AUTO_LOGIN, handler)
    worker = WorkerLoop(db, broker, outbox, worker_id="warmup", settings=settings)
    await broker.enqueue(kind=JobKind.AUTO_LOGIN, profile="live", payload={})
    runner = asyncio.create_task(worker.run_forever())
    try:
        await asyncio.wait_for(executed.wait(), 1)
        release.set()
        await asyncio.wait_for(drained.wait(), 1)
        # Both production model variants should be prepared.
        for _ in range(10):
            if len(variants) == 2:
                break
            await asyncio.sleep(0)
        assert variants == [True, False]
    finally:
        worker.stop()
        await runner
    assert drained.is_set()


async def test_stop_cancels_pending_ocr_warmup(worker_env, monkeypatch):
    import asyncio

    db, broker, outbox, settings = worker_env
    warming, drained = asyncio.Event(), asyncio.Event()

    async def warmup(provider):
        warming.set()
        try:
            await asyncio.Event().wait()
        finally:
            drained.set()

    monkeypatch.setattr("adapters.verification.ddddocr_provider.DdddOcrProvider.warmup", warmup)
    worker = WorkerLoop(db, broker, outbox, worker_id="warmup-stop", settings=settings)
    runner = asyncio.create_task(worker.run_forever())
    try:
        await asyncio.wait_for(warming.wait(), 1)
    finally:
        worker.stop()
        await runner
    assert drained.is_set()


@pytest.mark.parametrize("background_kind", [JobKind.EVENT_HYDRATE, JobKind.MANUAL_LOGIN])
async def test_due_purchase_preempts_background(worker_env, background_kind):
    import asyncio
    db, broker, outbox, settings = worker_env
    background_started = asyncio.Event()
    purchase_started = asyncio.Event()
    background_stopped = asyncio.Event()

    async def background(job, **kwargs):
        background_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            background_stopped.set()

    async def purchase(job, **kwargs):
        assert background_stopped.is_set()
        purchase_started.set()
        await broker.complete(job.id, worker_id="priority", result={})

    loop = WorkerLoop(db, broker, outbox, worker_id="priority", settings=settings,
                      handlers={background_kind: background, JobKind.PURCHASE: purchase})
    await broker.enqueue(kind=background_kind, profile="live", payload={})
    runner = asyncio.create_task(loop.run_forever())
    try:
        await asyncio.wait_for(background_started.wait(), 1)
        await broker.enqueue(kind=JobKind.PURCHASE, profile="live", payload={})
        await asyncio.wait_for(purchase_started.wait(), 1)
    finally:
        loop.stop()
        await runner
