"""KKTIX 圖片驗證碼專用選擇器。"""

from __future__ import annotations


class CaptchaSelectors:
    """圖片驗證碼專用選擇器。

    `selectors.py` 為凍結模組（G1），新選擇器一律落在此處。
    命名與候選順序慣例與 `KKTIXSelectors` 相同：宣告順序即嘗試順序。

    REFRESH_BUTTON 只能相對於 IMAGE_CONTAINER 取用（`container.locator(...)`），
    絕不從 page 根節點取——那會讓「換一張主辦方的驗證碼圖」有機會誤觸
    Cloudflare 的元件，而後者依 D1 是絕對禁區。
    """

    IMAGE = (
        ".custom-captcha-inner img",
        ".custom-captcha-inner canvas",
        "img[src*='captcha']",
        "canvas[id*='captcha']",
    )
    IMAGE_CONTAINER = (
        ".custom-captcha-inner",
        "div[ng-if*='captcha']",
    )
    REFRESH_BUTTON = (
        "a[ng-click*='refresh']",
        "button[ng-click*='refresh']",
        "[ng-click*='captcha']",
        "img",  # KKTIX 的驗證碼圖本身常綁 click 換圖；已被容器限縮，不會外溢
    )
