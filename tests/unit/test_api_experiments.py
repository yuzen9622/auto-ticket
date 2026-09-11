from __future__ import annotations

import httpx
import pytest

from api.main import create_app
from purchase.orchestrator import PurchaseReport
from storage.database import Database
from tests.netguard import netguard_autouse  # noqa: F401
from worker.persistence import save_experiment


@pytest.fixture
async def app_instance(db: Database):
    app = create_app(db=db)
    return app


async def test_experiments_list_and_detail(app_instance, db: Database) -> None:
    app = app_instance

    # 1. 寫入一筆假實驗資料
    exp_id = "exp_test_001"
    report = PurchaseReport(
        task_id="task_test_001",
        final_state="COMPLETED",
        ticket_trace=("TICKET_A",),
        sale_time_error_ms=12.5,
        payment=None,
        screenshots=("screen1.png",),
        screenshots_expected=1,
    )
    await save_experiment(
        db,
        experiment_id=exp_id,
        task_id=None,
        report=report,
        events=[],
        clock_sync_mode="ntp",
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        # 2. 查詢實驗清單
        list_resp = await client.get("/api/v1/experiments")
        assert list_resp.status_code == 200
        data = list_resp.json()
        assert data["total"] >= 1
        assert any(e["id"] == exp_id for e in data["items"])

        # 3. 查詢實驗詳情
        detail_resp = await client.get(f"/api/v1/experiments/{exp_id}")
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert detail["experiment"]["id"] == exp_id
        assert detail["experiment"]["final_state"] == "COMPLETED"
        assert detail["experiment"]["success"] is True

        # 4. 查詢不存在之實驗 -> 404
        missing_resp = await client.get("/api/v1/experiments/exp_not_exist")
        assert missing_resp.status_code == 404
