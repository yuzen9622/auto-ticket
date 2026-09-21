"""瀏覽器 Cookie 管理與注入。"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext

    from accounts.models import CredentialRecord

#: 注入時要寫到哪個網域。**必須與平台自己發的那顆 cookie 同一個 scope**：
#: 名稱相同但 scope 不同的兩顆 cookie 不會互相覆蓋，瀏覽器會把它們**一起**送出，
#: 先建立的那顆排在前面，於是伺服器讀到的是舊的那顆。拓元的 TIXUISID 是 host-only
#: 的 `tixcraft.com`，寫成 `.tixcraft.com` 會生出一顆永遠蓋在真 session 前面的影子
#: cookie，讓登入看似成功卻立刻被踢回登入頁。
#: ibon 的 session 要跨 ticket/orders 兩個子網域，所以那裡的前綴點是對的。
PLATFORM_COOKIE_DOMAINS: dict[str, str] = {
    "tixcraft": "tixcraft.com",
    "ibon": ".ibon.com.tw",
    "kktix": ".kktix.com",
}


async def _existing_cookie_names(context: BrowserContext | Any) -> set[str]:
    """瀏覽器目前已經持有、且有值的 cookie 名稱。"""
    with contextlib.suppress(Exception):
        return {
            str(c.get("name", ""))
            for c in await context.cookies()
            if str(c.get("value", "")).strip()
        }
    return set()


async def inject_platform_cookies(
    context: BrowserContext | Any,
    record: CredentialRecord,
) -> int:
    """將 CredentialRecord 中的 cookies 注入至瀏覽器 context。

    依平台設定對應的 Cookie 網域，絕不記錄 Cookie 敏感內容至日誌或例外。
    回傳成功注入的 Cookie 筆數。

    瀏覽器自己已經有同名 cookie 時**跳過不覆蓋**：接的是使用者本機那顆真 Chrome，
    它當下的登入狀態一定比 vault 裡存的那份新，拿舊值蓋上去只會把好好的 session 弄壞。
    """
    if not record.cookies:
        return 0

    plat = record.platform.lower()
    domain = PLATFORM_COOKIE_DOMAINS.get(plat)
    if not domain:
        return 0

    live_names = await _existing_cookie_names(context)

    cookie_list: list[dict[str, Any]] = []
    for name, value in record.cookies.items():
        if not name or not value:
            continue
        if name in live_names:
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
