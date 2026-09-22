"""ibon Resolver 單元測試。

三支 API 的形狀都照實際回應建 fixture：`GetGameInfoList` 尤其挑剔，body 必須是
JSON 且帶 `hasDeadline`，否則伺服器只回沒有 `Href` 的空殼。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from adapters.ticketing.ibon.resolver import (
    IbonEventResolver,
    IbonResolveError,
)
from domain.event import EventStatus, PlatformEnum

FIXTURES = Path(__file__).parent.parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def build_handler(
    *,
    seen: list[dict[str, Any]] | None = None,
    games: str | None = None,
) -> Any:
    index_json = load_fixture("ibon_activity_index.json")
    detail_json = load_fixture("ibon_activity_detail.json")
    games_json = games if games is not None else load_fixture("ibon_game_info_list.json")

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if seen is not None:
            seen.append(
                {
                    "path": path,
                    "method": request.method,
                    "content_type": request.headers.get("content-type", ""),
                    "body": request.content.decode(),
                }
            )
        if path == "/api/ActivityInfo/GetIndexData":
            return httpx.Response(200, text=index_json)
        if path == "/api/ActivityInfo/GetDetailData":
            return httpx.Response(200, text=detail_json)
        if path == "/api/ActivityInfo/GetGameInfoList":
            return httpx.Response(200, text=games_json)
        return httpx.Response(404)

    return handler


@pytest.mark.asyncio
async def test_ibon_search_uses_index_api() -> None:
    seen: list[dict[str, Any]] = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(build_handler(seen=seen))
    ) as client:
        resolver = IbonEventResolver(client=client)
        candidates = await resolver.search("音樂盛宴")

    top = candidates[0]
    assert top.title == "ibon 音樂盛宴 2026"
    assert "38001" in top.url
    # 清單自帶簡介與演出時間，卡片不必再打一次詳情。
    assert top.summary == "跨世代的聲音再次相遇"
    assert top.published is not None
    assert seen[0]["path"] == "/api/ActivityInfo/GetIndexData"
    assert seen[0]["method"] == "POST"


@pytest.mark.asyncio
async def test_ibon_fetch_metadata_fills_description_organizer_and_sessions() -> None:
    seen: list[dict[str, Any]] = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(build_handler(seen=seen))
    ) as client:
        resolver = IbonEventResolver(client=client)
        event = await resolver.fetch_event_metadata(
            "https://ticket.ibon.com.tw/ActivityInfo/Details/38001"
        )

    assert event.platform == PlatformEnum.IBON
    assert event.event_slug == "38001"
    assert event.title == "ibon 音樂盛宴 2026"

    # 活動說明必須跳過全站共用的公告段落。
    description = event.raw_metadata["description"]
    assert "集結三組跨世代樂團" in description
    assert "會員帳號連結" not in description
    assert "加購高鐵" not in description

    assert event.raw_metadata["organizer_display"] == "星光娛樂股份有限公司"
    assert event.raw_metadata["venue"] == "高雄巨蛋"
    assert event.raw_metadata["detail_source"] == "ibon_api"

    # 售票時間是台北時間，進 domain 前要換成 UTC。
    assert event.sale_start_at is not None
    assert event.sale_start_at.isoformat() == "2026-09-20T04:00:00+00:00"
    assert event.status == EventStatus.ON_SALE

    session = event.raw_metadata["sessions"][0]
    assert session["can_buy"] is True
    assert session["purchase_url"] == (
        "https://orders.ibon.com.tw/application/UTK02/UTK0201_000.aspx"
        "?PERFORMANCE_ID=B0AAA001&PRODUCT_ID=B0AAA000"
    )

    games_call = next(
        c for c in seen if c["path"] == "/api/ActivityInfo/GetGameInfoList"
    )
    assert "application/json" in games_call["content_type"]
    assert json.loads(games_call["body"])["hasDeadline"] is True


@pytest.mark.asyncio
async def test_ibon_resolve_direct_url() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(build_handler())
    ) as client:
        resolver = IbonEventResolver(client=client)
        result = await resolver.resolve(
            "https://ticket.ibon.com.tw/ActivityInfo/Details/38001"
        )

    assert result.auto_selected is True
    assert result.event is not None
    assert result.event.title == "ibon 音樂盛宴 2026"


@pytest.mark.asyncio
async def test_ibon_resolve_error_mapping() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500, text="boom"))
    ) as client:
        resolver = IbonEventResolver(client=client)
        with pytest.raises(IbonResolveError):
            await resolver.search("any")


@pytest.mark.asyncio
async def test_ibon_status_is_announced_before_the_session_sale_window() -> None:
    """尚未開賣看的是場次自己的售票起訖，不是活動層的售票時間。

    分階段開賣的活動，活動層的 `ActivityTicketSDate` 寫的是最早那一階段；時間一過
    就把整場標成販售中，但實際上每個場次都還沒開。場次列自己帶 `StartDT`/`EndDT`
    與伺服器現在時間 `NowDT`，那才是準的。
    """
    games = json.loads(load_fixture("ibon_game_info_list.json"))
    games["Item"]["GIHtmls"][0].update(
        {
            "CanBuy": False,
            "SoldOut": False,
            "Href": None,
            "StartDT": "2026-09-26T11:00:00",
            "NowDT": "2026-09-22T10:29:31.1795064",
        }
    )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            build_handler(games=json.dumps(games, ensure_ascii=False))
        )
    ) as client:
        resolver = IbonEventResolver(client=client)
        event = await resolver.fetch_event_metadata(
            "https://ticket.ibon.com.tw/ActivityInfo/Details/38001"
        )

    assert event.status == EventStatus.ANNOUNCED
    session = event.raw_metadata["sessions"][0]
    assert session["sale_start_at"] == "2026-09-26T11:00:00"
    assert session["server_now"] == "2026-09-22T10:29:31.1795064"


@pytest.mark.asyncio
async def test_ibon_status_is_closed_after_the_session_sale_window() -> None:
    games = json.loads(load_fixture("ibon_game_info_list.json"))
    games["Item"]["GIHtmls"][0].update(
        {
            "CanBuy": False,
            "SoldOut": False,
            "Href": None,
            "StartDT": "2026-09-12T12:00:00",
            "EndDT": "2026-09-15T23:59:00",
            "NowDT": "2026-09-22T10:41:21.2491311",
        }
    )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            build_handler(games=json.dumps(games, ensure_ascii=False))
        )
    ) as client:
        resolver = IbonEventResolver(client=client)
        event = await resolver.fetch_event_metadata(
            "https://ticket.ibon.com.tw/ActivityInfo/Details/38001"
        )

    assert event.status == EventStatus.CLOSED
