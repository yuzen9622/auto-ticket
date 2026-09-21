"""拓元 (Tixcraft) Resolver 單元測試。

fixture 是從實際頁面剪下來的，不是照想像寫的：先前那份手寫 fixture 讓解析器
對著不存在的 DOM 結構通過測試，線上卻一筆都抓不到。
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from adapters.ticketing.tixcraft.resolver import (
    TixcraftEventResolver,
    TixcraftResolveError,
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
    # 列表頁就給得出場地、日期與販售狀態，不必再打一次詳情頁。
    assert top.raw["venue"] == "台北國際會議中心TICC"
    assert top.raw["status"] == EventStatus.ON_SALE.value
    assert top.published is not None


@pytest.mark.asyncio
async def test_tixcraft_search_marks_non_selling_activity_as_announced() -> None:
    index_html = load_fixture("tixcraft_activity_index.html")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=index_html))
    ) as client:
        resolver = TixcraftEventResolver(client=client)
        candidates = await resolver.search("北海道日本火腿")

    assert UPCOMING_SLUG in candidates[0].url
    assert candidates[0].raw["status"] == EventStatus.ANNOUNCED.value


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
    assert event.status == EventStatus.ON_SALE
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
