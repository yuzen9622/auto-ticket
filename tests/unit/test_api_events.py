from __future__ import annotations

import re

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
        assert re.fullmatch(r"[0-9a-f]{16}", top["id"])
        providers = top["ticketing_providers"]
        assert isinstance(providers, list) and len(providers) >= 1
        assert providers[0]["id"] == "kktix"
        assert providers[0]["name"] == "KKTIX"
        assert providers[0]["event_url"] == top["canonical_url"]
        assert top["detail_loaded"] is True
        assert top["status"] != "UNKNOWN"


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
        assert results[0]["detail_loaded"] is True


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


EMBA_ATOM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>2026年9月場　EMBA雜誌【策略破框】2天實戰工作坊>>從問題到行動，學習打造贏的選擇</title>
    <link rel="alternate" type="text/html" href="https://embamagazine.kktix.cc/events/emba20260918"/>
    <author><name>EMBA雜誌</name></author>
    <published>2026-09-18T09:00:00+08:00</published>
    <summary>從問題到行動，學習打造贏的選擇</summary>
  </entry>
</feed>
"""

EMBA_HTML = """<!DOCTYPE html>
<html>
<head><title>2026年9月場　EMBA雜誌【策略破框】2天實戰工作坊</title></head>
<body>
<div class="header-title"><h1>2026年9月場　EMBA雜誌【策略破框】2天實戰工作坊>>從問題到行動，學習打造贏的選擇</h1></div>
<div class="organizers"><a class="organizer-name">EMBA雜誌</a></div>
<div class="tickets">
  <table>
    <tbody>
      <tr>
        <td class="name">實戰工作坊一般票</td>
        <td class="price">NT$ 15,900</td>
        <td class="period"><span class="time">2026/07/01 00:00</span></td>
        <td class="status">開賣中</td>
      </tr>
    </tbody>
  </table>
</div>
</body>
</html>
"""


async def test_search_events_via_global_atom_feed_when_no_scope(db: Database) -> None:
    """即使未設定主辦 scope，也能透過全站 Atom feed 搜尋活動並完成票況確認。"""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "kktix.com" and request.url.path == "/events.atom":
            return httpx.Response(200, text=EMBA_ATOM_XML)
        if request.url.host == "embamagazine.kktix.cc" and request.url.path == "/events/emba20260918":
            return httpx.Response(200, text=EMBA_HTML)
        return httpx.Response(404)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    # 刻意不給 resolver_orgs（模擬預設環境）
    settings = ApiSettings(db_path=db.db_path, resolver_orgs=())
    app = create_app(settings, db=db, http_client=mock_client)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/api/v1/events/search", params={"q": "策略破框"})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["results"]) == 1
        top = body["results"][0]
        assert "策略破框" in top["title"]
        assert top["organizer"] == "EMBA雜誌"
        assert top["canonical_url"] == "https://embamagazine.kktix.cc/events/emba20260918"
        # 關鍵驗證：票況已即時確認，不再是未確認（detail_loaded 為 True）！
        assert top["detail_loaded"] is True
        assert top["status"] == "ON_SALE"


CLOSED_HTML = """<!DOCTYPE html>
<html>
<head><title>已結束的活動</title></head>
<body>
<div class="header-title"><h1>已結束的活動</h1></div>
<div class="organizers"><a class="organizer-name">測試主辦</a></div>
<div class="tickets">
  <table>
    <tbody>
      <tr>
        <td class="name">一般票</td>
        <td class="price">NT$ 500</td>
        <td class="period"><span class="time">2026/01/01 00:00</span></td>
        <td class="status">已結束</td>
      </tr>
    </tbody>
  </table>
</div>
</body>
</html>
"""


async def test_search_events_filters_out_closed_events(db: Database) -> None:
    """查詢活動時，已結束的活動不應出現在搜尋結果中。"""
    atom_xml = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>已結束的測試活動</title>
    <link rel="alternate" type="text/html" href="https://testorg.kktix.cc/events/closed-event"/>
    <author><name>測試主辦</name></author>
    <published>2026-01-01T00:00:00+08:00</published>
    <summary>這是一場已經結束的活動</summary>
  </entry>
</feed>
"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "kktix.com" and request.url.path == "/events.atom":
            return httpx.Response(200, text=atom_xml)
        if request.url.host == "testorg.kktix.cc" and request.url.path == "/events/closed-event":
            return httpx.Response(200, text=CLOSED_HTML)
        return httpx.Response(404)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = ApiSettings(db_path=db.db_path, resolver_orgs=())
    app = create_app(settings, db=db, http_client=mock_client)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        # 1. 透過關鍵字搜尋，已結束活動被過濾，results 應為空
        resp = await client.get("/api/v1/events/search", params={"q": "已結束"})
        assert resp.status_code == 200
        assert resp.json()["results"] == []

        # 2. 透過直接網址搜尋已結束活動，results 亦應為空
        direct_resp = await client.get(
            "/api/v1/events/search",
            params={"q": "https://testorg.kktix.cc/events/closed-event"},
        )
        assert direct_resp.status_code == 200
        assert direct_resp.json()["results"] == []


async def test_search_events_multi_platform(db: Database) -> None:
    """關鍵字搜尋並行呼叫各平台 resolver；亦支援單一平台篩選。"""
    import json

    tixcraft_index = """<!DOCTYPE html><html><body>
    <div class="thumbnail">
        <a href="/activity/detail/24_tix"><img alt="拓元好聲音演唱會"></a>
        <div class="caption"><h3><a href="/activity/detail/24_tix">拓元好聲音演唱會</a></h3></div>
    </div>
    </body></html>"""

    ibon_index = json.dumps({
        "Code": 0,
        "Message": "Success",
        "Data": [
            {
                "ActivityId": "38999",
                "ActivityName": "ibon 音樂嘉年華",
                "StartDate": "2026/11/20",
                "EndDate": "2026/11/20",
                "Location": "高雄巨蛋",
            }
        ],
    })

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "tixcraft.com" and request.url.path == "/activity":
            return httpx.Response(200, text=tixcraft_index)
        if (
            request.url.host == "ticket.ibon.com.tw"
            and request.url.path == "/api/ActivityInfo/GetIndexData"
        ):
            return httpx.Response(200, text=ibon_index)
        if request.url.host == "kktix.com" and request.url.path == "/events.atom":
            return httpx.Response(
                200,
                text="""<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>""",
            )
        return httpx.Response(404)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = ApiSettings(db_path=db.db_path, resolver_orgs=())
    app = create_app(settings, db=db, http_client=mock_client)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        # 1. 無 platform 參數：並行搜尋三平台，結果包含拓元與 ibon 活動
        resp = await client.get("/api/v1/events/search", params={"q": "音樂"})
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert len(results) >= 1
        assert any("ibon" in r["title"].lower() or "拓元" in r["title"] for r in results)

        # 2. 指定 platform=tixcraft
        tix_resp = await client.get(
            "/api/v1/events/search",
            params={"q": "演唱會", "platform": "tixcraft"},
        )
        assert tix_resp.status_code == 200
        tix_results = tix_resp.json()["results"]
        assert len(tix_results) >= 1
        assert "拓元" in tix_results[0]["title"]


async def test_get_event_hydrates_by_event_platform(db: Database) -> None:
    """get_event 依據 ev.platform 動態派發對應 resolver 進行 hydration。"""
    from domain.event import Event, EventStatus, PlatformEnum
    from storage.repositories.event_repository import EventRepository

    detail_html = """<!DOCTYPE html><html><head>
    <script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "Event", "name": "ibon 巨星演唱會", "offers": {"@type": "AggregateOffer", "lowPrice": "1200", "highPrice": "3600"}}
    </script>
    </head><body><h1>ibon 巨星演唱會</h1></body></html>"""

    def handler(request: httpx.Request) -> httpx.Response:
        if (
            request.url.host == "ticket.ibon.com.tw"
            and "/ActivityInfo/Details/38111" in request.url.path
        ):
            return httpx.Response(200, text=detail_html)
        return httpx.Response(404)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = ApiSettings(db_path=db.db_path, resolver_orgs=())
    app = create_app(settings, db=db, http_client=mock_client)

    # 在 DB 寫入一筆未補齊 (ticket_types=[]) 的 ibon 活動
    event_id = Event.make_id(PlatformEnum.IBON, "ibon", "38111")
    shallow_event = Event(
        id=event_id,
        platform=PlatformEnum.IBON,
        organizer="ibon",
        event_slug="38111",
        title="淺資料",
        canonical_url="https://ticket.ibon.com.tw/ActivityInfo/Details/38111",
        status=EventStatus.ON_SALE,
        ticket_types=[],
    )
    async with db.session() as session:
        await EventRepository(session).upsert_event(shallow_event)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.get(f"/api/v1/events/{event_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == event_id
        assert data["title"] == "ibon 巨星演唱會"
        assert data["platform"] == "ibon"



