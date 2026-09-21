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
    context.cookies = AsyncMock(return_value=[])

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
    assert call_args[0]["domain"] == "tixcraft.com"
    assert call_args[0]["path"] == "/"


@pytest.mark.asyncio
async def test_inject_ibon_cookies() -> None:
    context = MagicMock()
    context.add_cookies = AsyncMock()
    context.cookies = AsyncMock(return_value=[])

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
    context.cookies = AsyncMock(return_value=[])

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


@pytest.mark.asyncio
async def test_tixcraft_cookie_is_host_only_so_it_cannot_shadow_the_real_session() -> (
    None
):
    """拓元的 TIXUISID 必須寫成 host-only。

    寫成 `.tixcraft.com` 不會覆蓋網站自己發的那顆 host-only cookie，而是多生一顆同名
    的影子；兩顆會一起送出，伺服器讀到先建立的舊值，於是 OAuth 明明成功卻立刻被踢回
    登入頁。
    """
    context = MagicMock()
    context.add_cookies = AsyncMock()
    context.cookies = AsyncMock(return_value=[])

    record = CredentialRecord(
        platform="tixcraft",
        kind=CredentialKind.COOKIE,
        account="TIXUISID",
        access_key="",
        cookies={"TIXUISID": "secret_tix_token_123"},
    )

    await inject_platform_cookies(context, record)

    assert context.add_cookies.call_args[0][0][0]["domain"] == "tixcraft.com"


@pytest.mark.asyncio
async def test_live_browser_session_is_never_overwritten_by_the_vault() -> None:
    """瀏覽器已經登入時不准拿 vault 的舊值蓋上去。"""
    context = MagicMock()
    context.add_cookies = AsyncMock()
    context.cookies = AsyncMock(
        return_value=[
            {
                "name": "TIXUISID",
                "value": "live_session_from_real_login",
                "domain": "tixcraft.com",
            }
        ]
    )

    record = CredentialRecord(
        platform="tixcraft",
        kind=CredentialKind.COOKIE,
        account="TIXUISID",
        access_key="",
        cookies={"TIXUISID": "stale_value_from_vault"},
    )

    assert await inject_platform_cookies(context, record) == 0
    assert context.add_cookies.call_count == 0


@pytest.mark.asyncio
async def test_empty_live_cookie_does_not_block_injection() -> None:
    """瀏覽器那顆是空的就不算數，還是要注入。"""
    context = MagicMock()
    context.add_cookies = AsyncMock()
    context.cookies = AsyncMock(
        return_value=[{"name": "TIXUISID", "value": "   ", "domain": "tixcraft.com"}]
    )

    record = CredentialRecord(
        platform="tixcraft",
        kind=CredentialKind.COOKIE,
        account="TIXUISID",
        access_key="",
        cookies={"TIXUISID": "secret_tix_token_123"},
    )

    assert await inject_platform_cookies(context, record) == 1
