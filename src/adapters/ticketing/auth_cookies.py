"""共用登入 cookie 契約。

三平台 adapter 與 worker 層共用同一份「哪些 cookie 與登入相關」的定義；
唯一真相在此，不得在 adapter 層各自定義。存在不代表伺服器仍接受：
尤其 TIXUISID 必須搭配頁面登入證據，不能單憑殘留 cookie 判定登入。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

PLATFORM_AUTH_COOKIE_NAMES: dict[str, set[str]] = {
    "kktix": {
        "user_id_v2",
        "user_display_name_v2",
        "user_avatar_url_v2",
        "user_path_v2",
        "user_time_zone_offset_v2",
        "user_time_zone",
        "user_time_zone_v2",
    },
    "tixcraft": {"TIXUISID"},
    "ibon": {"ibonqware", "mem_id", "mem_email", "huiwanTK"},
}


def has_platform_auth_cookies(
    cookies: Sequence[dict[str, Any]] | None, platform: str = "kktix"
) -> bool:
    """檢查對應平台的認證 Cookie 是否存在；不驗證伺服器端 session 效力。"""
    if not cookies:
        return False
    plat = platform.lower()
    expected = PLATFORM_AUTH_COOKIE_NAMES.get(plat, set())
    for c in cookies:
        name = str(c.get("name", ""))
        val = str(c.get("value", "")).strip()
        if name in expected and val:
            return True
    return False
