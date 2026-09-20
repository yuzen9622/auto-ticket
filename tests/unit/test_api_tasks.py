from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from accounts.models import AccountStatus
from api.main import create_app
from broker.broker import EMERGENCY_STOP_ACTION, SqliteTaskBroker
from broker.control import list_signals
from broker.jobs import JobKind, JobState
from broker.schema import create_broker_schema
from domain.task import TaskStatus
from storage.database import Database
from storage.repositories.task_repository import TaskRepository
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
    "execution_mode": "mock",
}


class _StubAccounts:
    """帳號狀態樁：正式模式的前置檢查只看 `configured`。"""

    def __init__(self, configured: bool) -> None:
        self._configured = configured

    def status(self, platform: str) -> AccountStatus:
        return AccountStatus(
            platform=platform,
            source="vault" if self._configured else "none",
            configured=self._configured,
            masked_account="us***er@example.test" if self._configured else None,
        )


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
        assert re.fullmatch(r"[0-9a-f]{16}", task_id)
        assert data["status"] == "SCHEDULED"

        # 驗證 broker 有建立對應的 PURCHASE job
        jobs = await broker.list_jobs(task_id=task_id)
        assert len(jobs) == 1
        job = jobs[0]
        assert job.kind == JobKind.PURCHASE
        assert job.state == JobState.PENDING

        # G31 契約檢驗：spec 嚴禁包含 payment_profile
        spec = job.payload.get("spec", {})
        assert "payment_profile" not in spec
        assert spec["execution_mode"] == "mock"
        assert data["execution_mode"] == "mock"

        # 查詢單一任務細節
        get_resp = await client.get(f"/api/v1/tasks/{task_id}")
        assert get_resp.status_code == 200
        detail = get_resp.json()
        assert detail["task"]["id"] == task_id
        assert detail["task"]["status"] == "SCHEDULED"
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


async def test_create_task_live_mode_requires_configured_account(
    app_instance,
) -> None:
    app, _ = app_instance
    app.state.accounts = _StubAccounts(configured=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        payload = dict(VALID_TASK_PAYLOAD, execution_mode="live")
        resp = await client.post("/api/v1/tasks", json=payload)
        assert resp.status_code == 400
        err = resp.json()["error"]
        assert err["code"] == "invalid_request"
        assert err["details"]["reason"] == "account_not_configured"


async def test_create_task_live_mode_is_not_downgraded_to_mock(
    app_instance,
) -> None:
    app, broker = app_instance
    app.state.accounts = _StubAccounts(configured=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        payload = dict(VALID_TASK_PAYLOAD, execution_mode="live")
        resp = await client.post("/api/v1/tasks", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["execution_mode"] == "live"

        jobs = await broker.list_jobs(task_id=data["id"])
        spec = jobs[0].payload["spec"]
        assert spec["execution_mode"] == "live"
        # 正式模式一樣不得夾帶任何付款機密。
        assert "payment_profile" not in spec


async def test_create_task_defaults_to_live_mode(app_instance) -> None:
    app, _ = app_instance
    app.state.accounts = _StubAccounts(configured=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        payload = {k: v for k, v in VALID_TASK_PAYLOAD.items() if k != "execution_mode"}
        resp = await client.post("/api/v1/tasks", json=payload)
        assert resp.status_code == 201
        assert resp.json()["execution_mode"] == "live"


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
    app, _broker = app_instance
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


async def test_scheduled_task_waits_for_the_sale_and_leaves_time_to_warm_up(
    app_instance,
) -> None:
    """還沒開賣：搶票時間照送進來的值，job 提前放出來做預熱。"""
    app, broker = app_instance
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        sale_start = datetime.now(UTC) + timedelta(days=3)
        resp = await client.post(
            "/api/v1/tasks",
            json=dict(VALID_TASK_PAYLOAD, sale_start_at=sale_start.isoformat()),
        )
        assert resp.status_code == 201
        task_id = resp.json()["id"]

        jobs = await broker.list_jobs(task_id=task_id)
        spec = jobs[0].payload["spec"]
        assert spec["start_timing"] == "scheduled"
        assert datetime.fromisoformat(spec["sale_start_at"]) == sale_start
        assert jobs[0].available_at < sale_start


async def test_task_without_a_sale_time_runs_immediately(app_instance) -> None:
    """活動已經在販售：沒有搶票時間可填，任務一建立就該被領走。

    這裡守的是實際踩過的 bug——表單硬要一個「搶票時間」，後端把它當成新的開賣時間，
    於是已開賣的活動被塞進預約搶票流程，開賣前的收工線把預熱預算砍成零。
    """
    app, broker = app_instance
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        payload = {k: v for k, v in VALID_TASK_PAYLOAD.items() if k != "sale_start_at"}
        before = datetime.now(UTC)
        resp = await client.post("/api/v1/tasks", json=payload)
        assert resp.status_code == 201
        after = datetime.now(UTC)
        task_id = resp.json()["id"]

        jobs = await broker.list_jobs(task_id=task_id)
        spec = jobs[0].payload["spec"]
        assert spec["start_timing"] == "immediate"
        # T=0 就是建立當下，不是活動的官方開賣時間。
        assert before <= datetime.fromisoformat(spec["sale_start_at"]) <= after
        # 沒有預熱提前量：晚一秒領取就是晚一秒進登記頁。
        assert jobs[0].available_at <= after


async def test_a_sale_time_already_past_is_treated_as_immediate(app_instance) -> None:
    """開賣時間已經過去也一樣沒東西可等，不得再排進預約搶票流程。"""
    app, broker = app_instance
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        sale_start = datetime.now(UTC) - timedelta(days=1)
        resp = await client.post(
            "/api/v1/tasks",
            json=dict(VALID_TASK_PAYLOAD, sale_start_at=sale_start.isoformat()),
        )
        assert resp.status_code == 201
        task_id = resp.json()["id"]

        jobs = await broker.list_jobs(task_id=task_id)
        spec = jobs[0].payload["spec"]
        assert spec["start_timing"] == "immediate"
        assert datetime.fromisoformat(spec["sale_start_at"]) > sale_start


async def test_sale_time_without_a_timezone_is_rejected(app_instance) -> None:
    """沒有時區的時間會整整差掉八小時；在這裡就擋下來，不要讓它走到排程。"""
    app, _ = app_instance
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/api/v1/tasks",
            json=dict(VALID_TASK_PAYLOAD, sale_start_at="2026-10-01T12:00:00"),
        )
        assert resp.status_code == 400


async def _create_task(client: httpx.AsyncClient) -> str:
    resp = await client.post("/api/v1/tasks", json=VALID_TASK_PAYLOAD)
    assert resp.status_code == 201
    return resp.json()["id"]


async def test_completed_task_can_be_deleted(app_instance, db: Database) -> None:
    """已完成的任務也得能刪；刪除不再看狀態白名單。"""
    app, _ = app_instance
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        task_id = await _create_task(client)
        async with db.session() as session:
            await TaskRepository(session).update_status(task_id, TaskStatus.COMPLETED)

        del_resp = await client.delete(f"/api/v1/tasks/{task_id}")
        assert del_resp.status_code == 204

        get_resp = await client.get(f"/api/v1/tasks/{task_id}")
        assert get_resp.status_code == 404


async def test_running_task_is_stopped_before_it_is_deleted(
    app_instance, db: Database
) -> None:
    """非終態先交給 broker 收斂，不留下開著瀏覽器的孤兒 job。"""
    app, broker = app_instance
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        task_id = await _create_task(client)
        async with db.session() as session:
            await TaskRepository(session).update_status(task_id, TaskStatus.RUNNING)

        del_resp = await client.delete(f"/api/v1/tasks/{task_id}")
        assert del_resp.status_code == 204

        jobs = await broker.list_jobs(task_id=task_id)
        signals = await list_signals(db, task_id=task_id)
        assert any(job.state is JobState.CANCELLED for job in jobs) or any(
            signal.action == EMERGENCY_STOP_ACTION for signal in signals
        )

        get_resp = await client.get(f"/api/v1/tasks/{task_id}")
        assert get_resp.status_code == 404


async def test_delete_is_idempotent_only_once(app_instance) -> None:
    """同一筆只能刪一次，第二次是 404 而不是靜默成功。"""
    app, _ = app_instance
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        task_id = await _create_task(client)
        assert (await client.delete(f"/api/v1/tasks/{task_id}")).status_code == 204
        assert (await client.delete(f"/api/v1/tasks/{task_id}")).status_code == 404


async def test_create_task_defaults_the_automation_fields(app_instance) -> None:
    app, _ = app_instance
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.post("/api/v1/tasks", json=VALID_TASK_PAYLOAD)
        assert resp.status_code == 201
        spec = resp.json()["spec"]
        assert spec["auto_cloudflare"] is True
        assert spec["auto_ocr"] is True
        assert spec["auto_submit_verification"] is True
        assert spec["ocr_model_path"] is None
        assert spec["ocr_max_retries"] == 5
        assert spec["cloudflare_max_retries"] == 3
        assert spec["debug_screenshots_and_logs"] is False


async def test_create_task_passes_through_the_automation_fields(app_instance) -> None:
    app, _ = app_instance
    custom_payload = dict(
        VALID_TASK_PAYLOAD,
        auto_cloudflare=False,
        auto_ocr=False,
        auto_submit_verification=False,
        ocr_model_path="/custom/model.onnx",
        ocr_max_retries=8,
        cloudflare_max_retries=1,
        debug_screenshots_and_logs=True,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.post("/api/v1/tasks", json=custom_payload)
        assert resp.status_code == 201
        spec = resp.json()["spec"]
        assert spec["auto_cloudflare"] is False
        assert spec["auto_ocr"] is False
        assert spec["auto_submit_verification"] is False
        assert spec["ocr_model_path"] == "/custom/model.onnx"
        assert spec["ocr_max_retries"] == 8
        assert spec["cloudflare_max_retries"] == 1
        assert spec["debug_screenshots_and_logs"] is True
