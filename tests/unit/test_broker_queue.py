from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from broker.broker import LEASE_EXPIRED_ERROR, SqliteTaskBroker
from broker.jobs import JobKind, JobState
from broker.schema import create_broker_schema
from domain.preference import TicketPreference, TicketPriority
from domain.task import PaymentMethod, PurchaseTaskSpec, TaskStatus, UserContactProfile
from storage.database import Database
from storage.models import PurchaseTaskModel
from storage.repositories.task_repository import TaskRepository
from tests.netguard import netguard_autouse  # noqa: F401


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
async def broker(db: Database) -> SqliteTaskBroker:
    await create_broker_schema(db.engine)
    return SqliteTaskBroker(db, lease_ttl_s=2.0)


async def test_enqueue_and_get(broker: SqliteTaskBroker) -> None:
    job_id = await broker.enqueue(
        kind=JobKind.PURCHASE,
        profile="profile_a",
        payload={"foo": "bar"},
    )
    job = await broker.get(job_id)
    assert job is not None
    assert job.id == job_id
    assert job.kind == JobKind.PURCHASE
    assert job.profile == "profile_a"
    assert job.payload == {"foo": "bar"}
    assert job.state == JobState.PENDING
    assert job.attempt == 0


async def test_claim_cas_mutual_exclusion(broker: SqliteTaskBroker) -> None:
    job_id = await broker.enqueue(
        kind=JobKind.PURCHASE,
        profile="live",
        payload={},
    )
    # 兩個 worker 同時搶
    results = await asyncio.gather(
        broker.claim(worker_id="w1"),
        broker.claim(worker_id="w2"),
    )
    claimed = [r for r in results if r is not None]
    assert len(claimed) == 1
    assert claimed[0].id == job_id
    assert claimed[0].worker_id in ("w1", "w2")
    assert claimed[0].state == JobState.CLAIMED
    assert claimed[0].attempt == 1


async def test_claim_available_at_gate(broker: SqliteTaskBroker) -> None:
    now = _utcnow()
    future = now + timedelta(seconds=10)
    job_id = await broker.enqueue(
        kind=JobKind.PURCHASE,
        profile="live",
        payload={},
        available_at=future,
    )
    # 未到時間
    claimed = await broker.claim(worker_id="w1")
    assert claimed is None

    # trigger_now 之後可以領取
    triggered = await broker.trigger_now(job_id)
    assert triggered
    claimed = await broker.claim(worker_id="w1")
    assert claimed is not None
    assert claimed.id == job_id


async def test_claim_busy_profiles(broker: SqliteTaskBroker) -> None:
    # 建立兩個不同 profile 的任務
    job1 = await broker.enqueue(kind=JobKind.PURCHASE, profile="p1", payload={})
    job2 = await broker.enqueue(kind=JobKind.PURCHASE, profile="p2", payload={})

    # busy_profiles 包含 p1 時，只能領到 p2
    claimed = await broker.claim(worker_id="w1", busy_profiles=["p1"])
    assert claimed is not None
    assert claimed.id == job2

    # busy_profiles 為空時可以領取 p1
    claimed_p1 = await broker.claim(worker_id="w2", busy_profiles=[])
    assert claimed_p1 is not None
    assert claimed_p1.id == job1


async def test_heartbeat_and_mark_running(broker: SqliteTaskBroker) -> None:
    job_id = await broker.enqueue(kind=JobKind.PURCHASE, profile="live", payload={})
    job = await broker.claim(worker_id="w1")
    assert job is not None

    ok = await broker.mark_running(job_id, worker_id="w1")
    assert ok
    j_running = await broker.get(job_id)
    assert j_running is not None
    assert j_running.state == JobState.RUNNING

    # 非 owner heartbeat 失敗
    hb_fail = await broker.heartbeat(job_id, worker_id="other_worker")
    assert not hb_fail

    # owner heartbeat 成功
    hb_ok = await broker.heartbeat(job_id, worker_id="w1")
    assert hb_ok


async def test_complete_and_fail(broker: SqliteTaskBroker) -> None:
    job_id = await broker.enqueue(kind=JobKind.PURCHASE, profile="live", payload={})
    await broker.claim(worker_id="w1")
    await broker.complete(job_id, worker_id="w1", result={"done": True})

    done_job = await broker.get(job_id)
    assert done_job is not None
    assert done_job.state == JobState.DONE
    assert done_job.result == {"done": True}

    # fail
    job_id2 = await broker.enqueue(kind=JobKind.PURCHASE, profile="live", payload={})
    await broker.claim(worker_id="w1")
    await broker.fail(job_id2, worker_id="w1", error="test_error", retry=False)

    failed_job = await broker.get(job_id2)
    assert failed_job is not None
    assert failed_job.state == JobState.FAILED
    assert failed_job.error == "test_error"


async def test_reap_expired_with_task_sync(db: Database) -> None:
    # 自訂 clock 來模擬時間流逝
    current_time = _utcnow()

    def mock_clock() -> datetime:
        return current_time

    await create_broker_schema(db.engine)
    broker = SqliteTaskBroker(db, lease_ttl_s=1.0, clock=mock_clock)

    # 建立 task 記錄
    spec = PurchaseTaskSpec(
        task_id="task_test_reap",
        event_title="Demo",
        event_url="https://example.com/events/1",
        sale_start_at=current_time,
        ticket_preference=TicketPreference(
            priorities=[TicketPriority(price=100, priority=1)]
        ),
        contact_profile=UserContactProfile(
            name="n", phone="0912345678", email="a@example.com"
        ),
        payment_method=PaymentMethod.MOCK,
    )
    async with db.session() as session:
        repo = TaskRepository(session)
        await repo.create(spec)
        await repo.update_status("task_test_reap", TaskStatus.RUNNING)

    # 入列 max_attempts=1 的 job
    job_id = await broker.enqueue(
        kind=JobKind.PURCHASE,
        profile="live",
        payload={},
        task_id="task_test_reap",
        max_attempts=1,
    )
    claimed = await broker.claim(worker_id="w1")
    assert claimed is not None

    # 時間前進 2 秒（超過 lease_ttl 1 秒）
    current_time = current_time + timedelta(seconds=2.0)

    reaped_count = await broker.reap_expired()
    assert reaped_count == 1

    job = await broker.get(job_id)
    assert job is not None
    assert job.state == JobState.FAILED
    assert job.error == LEASE_EXPIRED_ERROR

    # 雙表狀態連動檢查：PurchaseTaskModel.status 也應該是 FAILED
    async with db.session() as session:
        task_row = await session.get(PurchaseTaskModel, "task_test_reap")
        assert task_row is not None
        assert task_row.status == TaskStatus.FAILED.value


async def test_cancel_pending_and_running(broker: SqliteTaskBroker) -> None:
    # 1. PENDING 直接被 CANCELLED
    job_id = await broker.enqueue(kind=JobKind.PURCHASE, profile="live", payload={})
    cancelled = await broker.cancel(job_id)
    assert cancelled
    j = await broker.get(job_id)
    assert j is not None
    assert j.state == JobState.CANCELLED

    # 2. 已領取／RUNNING → 發布 EMERGENCY_STOP control signal
    job_id2 = await broker.enqueue(
        kind=JobKind.PURCHASE,
        profile="live",
        payload={},
        task_id="task_cancel_running",
    )
    await broker.claim(worker_id="w1")
    cancelled2 = await broker.cancel(job_id2)
    assert cancelled2

    # job state 仍是 CLAIMED，但 control_signals 多了一筆
    j2 = await broker.get(job_id2)
    assert j2 is not None
    assert j2.state == JobState.CLAIMED
