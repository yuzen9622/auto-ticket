"""拓元 (Tixcraft) Resolver 單元測試。"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from adapters.ticketing.tixcraft.resolver import (
    TixcraftEventResolver,
    TixcraftResolveError,
)
from domain.event import EventStatus, PlatformEnum

FIXTURES = Path(__file__).parent.parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_tixcraft_search() -> None:
    index_html = load_fixture("tixcraft_activity_index.html")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/activity"
        return httpx.Response(200, text=index_html)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        resolver = TixcraftEventResolver(client=client)
        candidates = await resolver.search("演唱會")
        assert len(candidates) >= 1
        assert "演唱會" in candidates[0].title
        assert candidates[0].organizer == "tixcraft"
        assert "24_test" in candidates[0].url


@pytest.mark.asyncio
async def test_tixcraft_fetch_metadata() -> None:
    detail_html = load_fixture("tixcraft_activity_detail.html")

    def handler(request: httpx.Request) -> httpx.Response:
        assert "/activity/detail/24_test" in request.url.path
        return httpx.Response(200, text=detail_html)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        resolver = TixcraftEventResolver(client=client)
        event = await resolver.fetch_event_metadata("https://tixcraft.com/activity/detail/24_test")
        assert event.title == "2026 巡迴演唱會 台北場"
        assert event.platform == PlatformEnum.TIXCRAFT
        assert len(event.ticket_types) == 2
        assert event.ticket_types[0].price == 2800
        assert event.ticket_types[1].price == 4800


@pytest.mark.asyncio
async def test_tixcraft_fetch_metadata_401_fallback() -> None:
    index_html = load_fixture("tixcraft_activity_index.html")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/activity":
            return httpx.Response(200, text=index_html)
        if "/activity/detail/24_test" in request.url.path:
            return httpx.Response(401, text="Unauthorized")
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        resolver = TixcraftEventResolver(client=client)
        # 401 不得把整體解析變失敗，需從 activity list fallback 取得基本 Event
        event = await resolver.fetch_event_metadata("https://tixcraft.com/activity/detail/24_test")
        assert event.platform == PlatformEnum.TIXCRAFT
        assert "2026 巡迴演唱會" in event.title
        assert event.status == EventStatus.ANNOUNCED


@pytest.mark.asyncio
async def test_tixcraft_resolve_direct_url() -> None:
    detail_html = load_fixture("tixcraft_activity_detail.html")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=detail_html)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        resolver = TixcraftEventResolver(client=client)
        result = await resolver.resolve("https://tixcraft.com/activity/detail/24_test")
        assert result.auto_selected is True
        assert result.event is not None
        assert result.event.title == "2026 巡迴演唱會 台北場"


@pytest.mark.asyncio
async def test_tixcraft_resolve_error_mapping() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        resolver = TixcraftEventResolver(client=client)
        with pytest.raises(TixcraftResolveError):
            await resolver.search("any")
