from __future__ import annotations

import httpx
import pytest

from api.main import create_app
from broker.broker import SqliteTaskBroker
from broker.jobs import JobKind, JobState
from broker.schema import create_broker_schema
from storage.database import Database
from tests.netguard import netguard_autouse  # noqa: F401


@pytest.fixture
async def app_instance(db: Database):
    await create_broker_schema(db.engine)
    broker = SqliteTaskBroker(db)
    app = create_app(db=db, broker=broker)
    return app, broker


VALID_TASK_PAYLOAD = {
    "event_title": "Test Concert 2026",
    "event_url": "https://example.test/events/test-slug",
    "sale_start_at": "2026-10-01T12:00:00Z",
    "ticket_preference": {
        "quantity": 2,
        "priorities": [{"price": 2800, "priority": 1}],
    },
    "contact_profile": {
        "name": "Test User",
        "phone": "0912345678",
        "email": "user@example.test",
    },
    "payment_method": "mock",
}


async def test_create_task_success(app_instance, db: Database) -> None:
    app, broker = app_instance
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.post("/api/v1/tasks", json=VALID_TASK_PAYLOAD)
        assert resp.status_code == 201
        data = resp.json()
        task_id = data["id"]
        assert task_id.startswith("task_")
        assert data["status"] == "CREATED"

        # 驗證 broker 有建立對應的 PURCHASE job
        jobs = await broker.list_jobs(task_id=task_id)
        assert len(jobs) == 1
        job = jobs[0]
        assert job.kind == JobKind.PURCHASE
        assert job.state == JobState.PENDING

        # G31 契約檢驗：spec 嚴禁包含 payment_profile
        spec = job.payload.get("spec", {})
        assert "payment_profile" not in spec

        # 查詢單一任務細節
        get_resp = await client.get(f"/api/v1/tasks/{task_id}")
        assert get_resp.status_code == 200
        detail = get_resp.json()
        assert detail["task"]["id"] == task_id
        assert detail["task"]["status"] == "CREATED"
        assert detail["task"]["spec"]["event_title"] == "Test Concert 2026"

        # 查詢任務列表
        list_resp = await client.get("/api/v1/tasks")
        assert list_resp.status_code == 200
        items = list_resp.json()["items"]
        assert any(t["id"] == task_id for t in items)


async def test_create_task_credit_card_unsupported(app_instance) -> None:
    app, _ = app_instance
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        payload = dict(VALID_TASK_PAYLOAD, payment_method="credit_card")
        resp = await client.post("/api/v1/tasks", json=payload)
        assert resp.status_code == 400
        err = resp.json()
        assert err["error"]["code"] == "unsupported"


async def test_create_task_invalid_preference(app_instance) -> None:
    app, _ = app_instance
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        # 空 priorities 應觸發 422 驗證錯誤
        payload = dict(
            VALID_TASK_PAYLOAD,
            ticket_preference={"quantity": 2, "priorities": []},
        )
        resp = await client.post("/api/v1/tasks", json=payload)
        assert resp.status_code == 422


async def test_task_start_and_cancel(app_instance) -> None:
    app, broker = app_instance
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        # 建立任務
        create_resp = await client.post("/api/v1/tasks", json=VALID_TASK_PAYLOAD)
        assert create_resp.status_code == 201
        task_id = create_resp.json()["id"]

        # 觸發立即執行
        start_resp = await client.post(f"/api/v1/tasks/{task_id}/start")
        assert start_resp.status_code == 202

        # 取消任務
        cancel_resp = await client.post(f"/api/v1/tasks/{task_id}/cancel")
        assert cancel_resp.status_code == 202
        assert cancel_resp.json()["status"] == "CANCELLED"

        # 再次查詢確認狀態為 CANCELLED
        get_resp = await client.get(f"/api/v1/tasks/{task_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["task"]["status"] == "CANCELLED"
