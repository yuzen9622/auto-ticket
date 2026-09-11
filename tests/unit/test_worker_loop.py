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
