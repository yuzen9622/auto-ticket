from __future__ import annotations

import httpx
import pytest

from api.main import create_app
from storage.database import Database
from tests.netguard import netguard_autouse  # noqa: F401

ATARAYO_URL = "https://atarayo.kktix.cc/events/atarayo-taipei-2026"
MISSING_URL = "https://atarayo.kktix.cc/events/missing-event"


@pytest.fixture
def app_with_mock_kktix(db: Database, kktix_transport: httpx.MockTransport):
    mock_client = httpx.AsyncClient(transport=kktix_transport)
    app = create_app(db=db, http_client=mock_client)
    return app


async def test_resolve_event_success_and_persist(
    app_with_mock_kktix,
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with_mock_kktix),
        base_url="http://test",
    ) as client:
        # 1. 解析並寫入 DB (persist=True)
        resp = await client.post(
            "/api/v1/events/resolve",
            json={"query": ATARAYO_URL, "persist": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["auto_selected"] is True
        assert data["event"] is not None
        event_id = data["event"]["id"]
        assert event_id is not None
        assert "atarayo" in data["event"]["title"].lower()

        # 2. 查詢事件詳情
        get_resp = await client.get(f"/api/v1/events/{event_id}")
        assert get_resp.status_code == 200
        ev_data = get_resp.json()
        assert ev_data["id"] == event_id
        assert len(ev_data["ticket_types"]) > 0

        # 3. 查詢列表
        list_resp = await client.get("/api/v1/events?platform=kktix")
        assert list_resp.status_code == 200
        items = list_resp.json()
        assert len(items) >= 1
        assert any(item["id"] == event_id for item in items)


async def test_resolve_event_404_upstream(app_with_mock_kktix) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with_mock_kktix),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/api/v1/events/resolve",
            json={"query": MISSING_URL, "persist": False},
        )
        # 上游 404 應回傳 502 upstream_failed
        assert resp.status_code == 502
        err = resp.json()
        assert err["error"]["code"] == "upstream_failed"


async def test_resolve_event_no_org_scope(app_with_mock_kktix) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with_mock_kktix),
        base_url="http://test",
    ) as client:
        # 純關鍵字且未指定 orgs 時應回傳 400 invalid_request
        resp = await client.post(
            "/api/v1/events/resolve",
            json={"query": "Atarayo Concert", "persist": False},
        )
        assert resp.status_code == 400
        err = resp.json()
        assert err["error"]["code"] == "invalid_request"
