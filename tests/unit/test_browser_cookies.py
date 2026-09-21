"""瀏覽器 Cookie 注入單元測試。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from accounts.models import CredentialKind, CredentialRecord
from browser.cookies import inject_platform_cookies


@pytest.mark.asyncio
async def test_inject_tixcraft_cookies() -> None:
    context = MagicMock()
    context.add_cookies = AsyncMock()

    record = CredentialRecord(
        platform="tixcraft",
        kind=CredentialKind.COOKIE,
        account="TIXUISID",
        access_key="",
        cookies={"TIXUISID": "secret_tix_token_123"},
    )

    count = await inject_platform_cookies(context, record)
    assert count == 1
    assert context.add_cookies.call_count == 1

    call_args = context.add_cookies.call_args[0][0]
    assert len(call_args) == 1
    assert call_args[0]["name"] == "TIXUISID"
    assert call_args[0]["value"] == "secret_tix_token_123"
    assert call_args[0]["domain"] == ".tixcraft.com"
    assert call_args[0]["path"] == "/"


@pytest.mark.asyncio
async def test_inject_ibon_cookies() -> None:
    context = MagicMock()
    context.add_cookies = AsyncMock()

    record = CredentialRecord(
        platform="ibon",
        kind=CredentialKind.COOKIE,
        account="ibonqware",
        access_key="",
        cookies={
            "ibonqware": "secret_ibon_token_456",
            "mem_id": "member_id_789",
        },
    )

    count = await inject_platform_cookies(context, record)
    assert count == 2
    assert context.add_cookies.call_count == 1

    call_args = context.add_cookies.call_args[0][0]
    assert len(call_args) == 2
    names = {c["name"] for c in call_args}
    assert names == {"ibonqware", "mem_id"}
    for c in call_args:
        assert c["domain"] == ".ibon.com.tw"


@pytest.mark.asyncio
async def test_inject_empty_or_unknown_platform() -> None:
    context = MagicMock()
    context.add_cookies = AsyncMock()

    # 空 cookies
    empty_record = CredentialRecord(
        platform="tixcraft",
        kind=CredentialKind.COOKIE,
        account="TIXUISID",
        access_key="",
        cookies={},
    )
    assert await inject_platform_cookies(context, empty_record) == 0
    assert context.add_cookies.call_count == 0

    # 未知平台
    unknown_record = CredentialRecord(
        platform="unknown",
        kind=CredentialKind.COOKIE,
        account="cookie",
        access_key="",
        cookies={"foo": "bar"},
    )
    assert await inject_platform_cookies(context, unknown_record) == 0
    assert context.add_cookies.call_count == 0
