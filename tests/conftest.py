from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from storage.database import Database

FIXTURES = Path(__file__).parent / "fixtures"

EXPECTED_EVENT_START_UTC = datetime(2026, 3, 14, 11, 30, tzinfo=timezone.utc)
EXPECTED_SALE_START_UTC = datetime(2026, 1, 10, 4, 0, tzinfo=timezone.utc)


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def load_json_fixture(name: str) -> dict[str, Any]:
    return json.loads(load_fixture(name))


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
async def db(tmp_db_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_db_path)
    await database.create_all()
    try:
        yield database
    finally:
        await database.dispose()


@pytest.fixture
def kktix_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        if host == "atarayo.kktix.cc" and path == "/events.json":
            return httpx.Response(200, json=load_json_fixture("kktix_events_feed.json"))
        if host == "atarayo.kktix.cc" and path == "/events/atarayo-taipei-2026":
            return httpx.Response(200, text=load_fixture("kktix_event_page.html"))
        if (
            host == "atarayo.kktix.cc"
            and path == "/events/atarayo-taipei-2026-fullwidth"
        ):
            return httpx.Response(
                200, text=load_fixture("kktix_event_page_no_jsonld.html")
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.fixture
async def kktix_client(
    kktix_transport: httpx.MockTransport,
) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=kktix_transport) as client:
        yield client
