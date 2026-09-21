"""瀏覽器 Cookie 管理與注入。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext

    from accounts.models import CredentialRecord

PLATFORM_COOKIE_DOMAINS: dict[str, str] = {
    "tixcraft": ".tixcraft.com",
    "ibon": ".ibon.com.tw",
    "kktix": ".kktix.com",
}


async def inject_platform_cookies(
    context: BrowserContext | Any,
    record: CredentialRecord,
) -> int:
    """將 CredentialRecord 中的 cookies 注入至瀏覽器 context。

    依平台設定對應的 Cookie 網域，絕不記錄 Cookie 敏感內容至日誌或例外。
    回傳成功注入的 Cookie 筆數。
    """
    if not record.cookies:
        return 0

    plat = record.platform.lower()
    domain = PLATFORM_COOKIE_DOMAINS.get(plat)
    if not domain:
        return 0

    cookie_list: list[dict[str, Any]] = []
    for name, value in record.cookies.items():
        if not name or not value:
            continue
        cookie_list.append(
            {
                "name": name,
                "value": value,
                "domain": domain,
                "path": "/",
            }
        )

    if cookie_list:
        await context.add_cookies(cast(Any, cookie_list))

    return len(cookie_list)
