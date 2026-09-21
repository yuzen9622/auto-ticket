"""ibon Resolver 單元測試。"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from adapters.ticketing.ibon.resolver import (
    IbonEventResolver,
    IbonResolveError,
)
from domain.event import PlatformEnum

FIXTURES = Path(__file__).parent.parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_ibon_search_uses_post_json() -> None:
    index_json = load_fixture("ibon_activity_index.json")
    called_method: list[str] = []
    called_url: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called_method.append(request.method)
        called_url.append(str(request.url))
        assert request.method == "POST"
        assert "/api/ActivityInfo/GetIndexData" in request.url.path
        return httpx.Response(200, text=index_json)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        resolver = IbonEventResolver(client=client)
        candidates = await resolver.search("音樂盛宴")
        assert len(candidates) >= 1
        assert "音樂盛宴" in candidates[0].title
        assert candidates[0].organizer == "ibon"
        assert "38001" in candidates[0].url

    assert called_method == ["POST"]
    assert any("/api/ActivityInfo/GetIndexData" in u for u in called_url)


@pytest.mark.asyncio
async def test_ibon_fetch_metadata() -> None:
    detail_html = load_fixture("ibon_activity_details.html")

    def handler(request: httpx.Request) -> httpx.Response:
        assert "/ActivityInfo/Details/38001" in request.url.path
        return httpx.Response(200, text=detail_html)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        resolver = IbonEventResolver(client=client)
        event = await resolver.fetch_event_metadata("https://ticket.ibon.com.tw/ActivityInfo/Details/38001")
        assert event.title == "2026 ibon 音樂盛宴"
        assert event.platform == PlatformEnum.IBON
        assert event.event_slug == "38001"


@pytest.mark.asyncio
async def test_ibon_resolve_direct_url() -> None:
    detail_html = load_fixture("ibon_activity_details.html")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=detail_html)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        resolver = IbonEventResolver(client=client)
        result = await resolver.resolve("https://ticket.ibon.com.tw/ActivityInfo/Details/38001")
        assert result.auto_selected is True
        assert result.event is not None
        assert result.event.title == "2026 ibon 音樂盛宴"


@pytest.mark.asyncio
async def test_ibon_resolve_error_mapping() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        resolver = IbonEventResolver(client=client)
        with pytest.raises(IbonResolveError):
            await resolver.search("any")
