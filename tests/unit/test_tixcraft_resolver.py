"""拓元 (Tixcraft) Resolver 單元測試。

fixture 是從實際頁面剪下來的，不是照想像寫的：先前那份手寫 fixture 讓解析器
對著不存在的 DOM 結構通過測試，線上卻一筆都抓不到。
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from adapters.ticketing.tixcraft.pages import (
    SessionSaleState,
    has_game_list,
    parse_activity_detail,
    parse_game_list,
)
from adapters.ticketing.tixcraft.resolver import (
    TixcraftEventResolver,
    TixcraftResolveError,
    build_event,
    clear_listing_cache,
)
from domain.event import EventStatus, PlatformEnum

FIXTURES = Path(__file__).parent.parent / "fixtures"

#: 列表 fixture 裡真實存在的活動。
ON_SALE_SLUG = "26_todd"
ON_SALE_TITLE = "Malcolm Todd： Do That Again Tour in Taipei"
UPCOMING_SLUG = "26_hnf"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    clear_listing_cache()


@pytest.mark.asyncio
async def test_tixcraft_search_reads_listing_cards() -> None:
    index_html = load_fixture("tixcraft_activity_index.html")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/activity"
        return httpx.Response(200, text=index_html)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        resolver = TixcraftEventResolver(client=client)
        candidates = await resolver.search("Malcolm Todd")

    top = candidates[0]
    assert top.title == ON_SALE_TITLE
    assert top.organizer == "tixcraft"
    assert ON_SALE_SLUG in top.url
    # 列表頁給得出場地與日期，但給不出售票狀態：三個頁籤只是陳列方式，
    # 「最新開賣」是依上架時間排的推薦區。狀態要等場次頁才算數。
    assert top.raw["venue"] == "台北國際會議中心TICC"
    assert top.raw["status"] == EventStatus.UNKNOWN.value
    assert "latest-selling" in top.raw["listing_tabs"]
    assert top.published is not None


@pytest.mark.asyncio
async def test_tixcraft_search_does_not_guess_status_from_listing_tabs() -> None:
    """不在「最新開賣」頁籤裡不等於還沒開賣。

    2026-09-22 抓全站 73 個活動比對場次頁：拿頁籤當販售狀態有 18 個判錯，其中 14 個
    按得下「立即訂購」卻被標成尚未開賣——正是使用者看到的那種「寫尚未開賣、點進去
    卻買得到」。搜尋層改成不宣稱狀態。
    """
    index_html = load_fixture("tixcraft_activity_index.html")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=index_html))
    ) as client:
        resolver = TixcraftEventResolver(client=client)
        candidates = await resolver.search("北海道日本火腿")

    assert UPCOMING_SLUG in candidates[0].url
    assert candidates[0].raw["status"] == EventStatus.UNKNOWN.value
    assert "latest-selling" not in candidates[0].raw["listing_tabs"]


@pytest.mark.asyncio
async def test_tixcraft_fetch_metadata_reads_detail_page() -> None:
    index_html = load_fixture("tixcraft_activity_index.html")
    detail_html = load_fixture("tixcraft_activity_detail.html")
    game_html = load_fixture("tixcraft_game_list.html")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/activity":
            return httpx.Response(200, text=index_html)
        if request.url.path.startswith("/activity/detail/"):
            return httpx.Response(200, text=detail_html)
        if request.url.path.startswith("/activity/game/"):
            return httpx.Response(200, text=game_html)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        resolver = TixcraftEventResolver(client=client)
        event = await resolver.fetch_event_metadata(
            "https://tixcraft.com/activity/detail/26_renetp"
        )

    assert event.platform == PlatformEnum.TIXCRAFT
    assert event.title.startswith("劉若英")
    # 活動說明與主辦單位只有節目介紹頁有；抓到了就不該再回報「需要瀏覽器」。
    assert event.raw_metadata["organizer_display"] == "相信音樂"
    assert "旋轉舞台" in (event.raw_metadata.get("description") or "")
    assert event.raw_metadata["needs_browser_detail"] is False
    assert event.status == EventStatus.ON_SALE
    assert len(event.raw_metadata["sessions"]) == 2
    assert event.raw_metadata["sessions"][0]["purchase_url"].endswith(
        "/ticket/area/27_brunomars/23082"
    )


@pytest.mark.asyncio
async def test_tixcraft_challenge_falls_back_to_listing_without_placeholder_title() -> None:
    """節目介紹頁被人機驗證擋下時，標題必須來自活動列表，不得是代號拼出來的假標題。"""
    index_html = load_fixture("tixcraft_activity_index.html")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/activity":
            return httpx.Response(200, text=index_html)
        return httpx.Response(401, text='{"response":"identify"}')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        resolver = TixcraftEventResolver(client=client)
        event = await resolver.fetch_event_metadata(
            f"https://tixcraft.com/activity/detail/{ON_SALE_SLUG}"
        )

    assert event.title == ON_SALE_TITLE
    assert "拓元活動" not in event.title
    assert ON_SALE_SLUG not in event.title
    # 場次頁沒抓到就不知道賣不賣得到票；用列表頁的頁籤補一個狀態只是猜。
    assert event.status == EventStatus.UNKNOWN
    assert event.raw_metadata["venue"] == "台北國際會議中心TICC"
    # 還缺活動說明，要讓上層知道得改用瀏覽器補。
    assert event.raw_metadata["needs_browser_detail"] is True


@pytest.mark.asyncio
async def test_tixcraft_unknown_activity_raises_instead_of_inventing_title() -> None:
    index_html = load_fixture("tixcraft_activity_index.html")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/activity":
            return httpx.Response(200, text=index_html)
        return httpx.Response(401, text='{"response":"identify"}')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        resolver = TixcraftEventResolver(client=client)
        with pytest.raises(TixcraftResolveError):
            await resolver.fetch_event_metadata(
                "https://tixcraft.com/activity/detail/99_not_listed"
            )


@pytest.mark.asyncio
async def test_tixcraft_resolve_direct_url() -> None:
    index_html = load_fixture("tixcraft_activity_index.html")
    detail_html = load_fixture("tixcraft_activity_detail.html")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/activity":
            return httpx.Response(200, text=index_html)
        return httpx.Response(200, text=detail_html)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        resolver = TixcraftEventResolver(client=client)
        result = await resolver.resolve(
            "https://tixcraft.com/activity/detail/26_renetp"
        )

    assert result.auto_selected is True
    assert result.event is not None
    assert result.event.title.startswith("劉若英")


@pytest.mark.asyncio
async def test_tixcraft_listing_failure_raises() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500, text="boom"))
    ) as client:
        resolver = TixcraftEventResolver(client=client)
        with pytest.raises(TixcraftResolveError):
            await resolver.search("any")


@pytest.mark.parametrize(
    ("game_fixture", "expected"),
    [
        # 兩場都按得下「立即訂購」。
        ("tixcraft_game_list.html", EventStatus.ON_SALE),
        # 兩場都是「已售完」，連購票鈕都沒有。
        ("tixcraft_game_list_sold_out.html", EventStatus.SOLD_OUT),
        # 主場次購票鈕還在，但掛著「選購一空」；其餘專區已截止。買不到就是售完。
        ("tixcraft_game_list_zone_empty.html", EventStatus.SOLD_OUT),
        # 場次表寫「目前無場次資訊」：活動公告了、場次還沒排，也就還不能買。
        ("tixcraft_game_list_no_session.html", EventStatus.ANNOUNCED),
    ],
)
def test_tixcraft_status_comes_from_the_session_table(
    game_fixture: str, expected: EventStatus
) -> None:
    detail = parse_activity_detail(
        load_fixture("tixcraft_activity_detail.html"),
        game_html=load_fixture(game_fixture),
    )
    assert build_event("26_x", detail=detail).status == expected


def test_tixcraft_status_is_unknown_when_the_session_table_was_never_seen() -> None:
    """只抓到節目介紹頁時不得推論狀態。

    節目介紹頁沒有 `#gameList`，把「沒解析到場次」當成「沒有場次」會讓每一場
    只補得到說明的活動都被標成尚未開賣。
    """
    detail = parse_activity_detail(load_fixture("tixcraft_activity_detail.html"))
    assert detail.sessions_seen is False
    assert build_event("26_x", detail=detail).status == EventStatus.UNKNOWN


def test_tixcraft_buyable_session_is_not_masked_by_the_sold_out_label() -> None:
    """同一格同時有「立即訂購」按鈕與「選購一空」標籤時，兩者不得互相抵銷。

    舊版把整格文字接起來比對關鍵字：售完字樣命中就判 `sold_out`，於是還買得到的
    場次被說成售完；反過來有按鈕又讓售完的場次看起來能買。
    """
    sessions = parse_game_list(load_fixture("tixcraft_game_list_zone_empty.html"))
    assert [s.state for s in sessions] == [
        SessionSaleState.ZONE_EMPTY,
        SessionSaleState.DEADLINE_PASSED,
        SessionSaleState.DEADLINE_PASSED,
    ]
    assert sessions[0].purchase_url is not None
    assert sessions[0].buyable is False
    assert sessions[0].sold_out is True


def test_tixcraft_no_session_row_is_not_a_session() -> None:
    assert parse_game_list(load_fixture("tixcraft_game_list_no_session.html")) == []
    assert has_game_list(load_fixture("tixcraft_game_list_no_session.html")) is True
