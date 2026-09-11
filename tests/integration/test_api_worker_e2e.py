from __future__ import annotations

import httpx
import pytest
from starlette.testclient import TestClient

from api.main import create_app
from broker.broker import SqliteTaskBroker
from broker.jobs import JobKind, JobState
from broker.outbox import OutboxWriter
from broker.schema import create_broker_schema
from purchase.orchestrator import PurchaseReport
from storage.database import Database
from tests.netguard import netguard_autouse  # noqa: F401
from worker.loop import WorkerLoop
from worker.persistence import save_experiment
from worker.settings import WorkerSettings


@pytest.fixture
async def e2e_env(db: Database):
    await db.create_all()
    await create_broker_schema(db.engine)
    broker = SqliteTaskBroker(db)
    outbox = OutboxWriter(db)
    app = create_app(db=db, broker=broker)
    return db, broker, outbox, app


async def test_e2e_task_lifecycle_api_and_worker(e2e_env) -> None:
    db, broker, outbox, app = e2e_env

    # 1. 透過 REST API 建立任務
    task_payload = {
        "event_title": "E2E Concert 2026",
        "event_url": "https://example.test/events/e2e-slug",
        "sale_start_at": "2026-10-01T12:00:00Z",
        "ticket_preference": {
            "quantity": 2,
            "priorities": [{"price": 3800, "priority": 1}],
        },
        "contact_profile": {
            "name": "E2E User",
            "phone": "0988123456",
            "email": "e2e@example.test",
        },
        "payment_method": "mock",
    }

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        create_resp = await client.post("/api/v1/tasks", json=task_payload)
        assert create_resp.status_code == 201
        task_id = create_resp.json()["id"]

        # 立即觸發開始
        start_resp = await client.post(f"/api/v1/tasks/{task_id}/start")
        assert start_resp.status_code == 202

        # 2. Worker 設定與自訂執行器 (模擬購票成功並發布事件)
        async def mock_purchase_handler(job, **kwargs):
            outbox.publish(
                task_id=job.task_id or job.id,
                type="STATE_CHANGED",
                payload={
                    "from_state": "SALE_OPEN",
                    "to_state": "COMPLETED",
                    "event": "order_completed",
                    "elapsed_ms": 120.0,
                },
                ephemeral=False,
            )
            # 存入實驗紀錄
            exp_id = f"exp_{job.task_id}"
            report = PurchaseReport(
                task_id=job.task_id or "",
                final_state="COMPLETED",
                ticket_trace=("TICKET_A",),
                sale_time_error_ms=10.0,
                payment=None,
                screenshots=(),
                screenshots_expected=0,
            )
            await save_experiment(
                db,
                experiment_id=exp_id,
                task_id=job.task_id,
                report=report,
                events=[],
            )
            await broker.complete(job.id, worker_id="e2e_worker", result={"ok": True})

        worker_settings = WorkerSettings()
        worker_loop = WorkerLoop(
            db,
            broker,
            outbox,
            worker_id="e2e_worker",
            settings=worker_settings,
            handlers={JobKind.PURCHASE: mock_purchase_handler},
        )

        # 3. Worker 領取並執行該任務
        claimed = await worker_loop.run_once()
        assert claimed is True

        # 4. 驗證 broker 中的 job 狀態已為 DONE
        job = (
            await broker.get_by_task_id(task_id)
            if hasattr(broker, "get_by_task_id")
            else None
        )
        if job is None:
            jobs = await broker.list_jobs(task_id=task_id)
            job = jobs[0]
        assert job.state == JobState.DONE

        # 5. 透過 REST API 查詢實驗記錄已產生
        exp_resp = await client.get("/api/v1/experiments")
        assert exp_resp.status_code == 200
        exp_items = exp_resp.json()["items"]
        assert any(e["task_id"] == task_id for e in exp_items)

    # 6. 透過 WebSocket 端點驗證可收到 snapshot 與 replay 事件
    with (
        TestClient(app) as test_client,
        test_client.websocket_connect(f"/ws/tasks/{task_id}") as ws,
    ):
        # 第一筆為快照
        snapshot = ws.receive_json()
        assert snapshot["type"] == "TASK_LOG"
        assert snapshot["payload"]["phase"] == "snapshot"

        # 第二筆為 replay 的 STATE_CHANGED
        msg = ws.receive_json()
        assert msg["type"] == "STATE_CHANGED"
        assert msg["payload"]["to_state"] == "COMPLETED"
