from __future__ import annotations

import httpx
import pytest

from api.main import create_app
from api.settings import ApiSettings
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


@pytest.fixture
def app_with_backend_scope(db: Database, kktix_transport: httpx.MockTransport):
    """主辦範圍來自後端設定——使用者永遠不必輸入主辦代號。"""
    mock_client = httpx.AsyncClient(transport=kktix_transport)
    settings = ApiSettings(db_path=db.db_path, resolver_orgs=("atarayo",))
    return create_app(settings, db=db, http_client=mock_client)


async def test_search_events_resolves_scope_on_the_backend(
    app_with_backend_scope,
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with_backend_scope),
        base_url="http://test",
    ) as client:
        resp = await client.get("/api/v1/events/search", params={"q": "Atarayo"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["query"] == "Atarayo"
        assert body["results"], "後端設定的主辦來源應該要能查到活動"

        top = body["results"][0]
        assert "atarayo" in top["title"].lower()
        assert top["description"]
        assert top["organizer"] == "Atarayo Live"
        assert top["id"].startswith("ev_")
        providers = top["ticketing_providers"]
        assert isinstance(providers, list) and len(providers) >= 1
        assert providers[0]["id"] == "kktix"
        assert providers[0]["name"] == "KKTIX"
        assert providers[0]["event_url"] == top["canonical_url"]


async def test_search_events_accepts_event_url(app_with_backend_scope) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with_backend_scope),
        base_url="http://test",
    ) as client:
        resp = await client.get("/api/v1/events/search", params={"q": ATARAYO_URL})
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert len(results) == 1
        assert results[0]["canonical_url"] == ATARAYO_URL


async def test_search_result_id_is_fetchable(app_with_backend_scope) -> None:
    """搜尋回的識別碼必須能直接餵給活動詳情——重新整理任務表單頁要靠它。"""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with_backend_scope),
        base_url="http://test",
    ) as client:
        search = await client.get("/api/v1/events/search", params={"q": "Atarayo"})
        event_id = search.json()["results"][0]["id"]

        detail = await client.get(f"/api/v1/events/{event_id}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["id"] == event_id
        assert body["detail_loaded"] is True
        assert body["ticket_types"]
        assert body["organizer_name"]


async def test_get_event_unknown_id_returns_404(app_with_backend_scope) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with_backend_scope),
        base_url="http://test",
    ) as client:
        resp = await client.get("/api/v1/events/ev_does_not_exist")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"


async def test_search_events_rejects_blank_query(app_with_backend_scope) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with_backend_scope),
        base_url="http://test",
    ) as client:
        resp = await client.get("/api/v1/events/search", params={"q": "   "})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_request"


def test_resolver_orgs_come_from_the_environment() -> None:
    """主辦來源是後端設定，不是使用者輸入。"""
    settings = ApiSettings.from_env(
        {"AUTO_TICKET_RESOLVER_ORGS": " believe , pycontw , "}
    )
    assert settings.resolver_orgs == ("believe", "pycontw")
    assert ApiSettings.from_env({}).resolver_orgs == ()
