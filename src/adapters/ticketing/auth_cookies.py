"""共用登入 cookie 契約。

三平台 adapter 與 worker 層共用同一份「哪些 cookie 代表已登入」的定義；
唯一真相在此，不得在 adapter 層各自定義。
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
    """檢查是否有對應平台的登入認證 Cookie。"""
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
