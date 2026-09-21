"""選擇器對得上真實頁面嗎？

Adapter 的測試用 mock locator 跑，選擇器字串寫錯也照樣全綠——先前 ibon 的
`ctl00_ContentPlaceHolder1_DataGrid1` 之類的選擇器就是這樣活下來的。這支測試
拿實際抓下來的頁面直接套選擇器，只問一件事：這個選擇器在真頁面上找不找得到東西。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from adapters.ticketing.ibon.selectors import IbonSelectors
from adapters.ticketing.tixcraft.selectors import TixcraftSelectors

FIXTURES = Path(__file__).parent.parent / "fixtures"


def soup(name: str) -> BeautifulSoup:
    return BeautifulSoup((FIXTURES / name).read_text(encoding="utf-8"), "html.parser")


@pytest.mark.parametrize(
    ("fixture", "selector", "label"),
    [
        ("tixcraft_game_list.html", TixcraftSelectors.GAME_LIST_ROWS, "場次列"),
        ("tixcraft_game_list.html", TixcraftSelectors.SESSION_BUY_BUTTON, "場次購票鈕"),
        ("tixcraft_area.html", TixcraftSelectors.ZONE_LINKS, "票區連結"),
        ("tixcraft_area.html", TixcraftSelectors.ZONE_LIST_CONTAINER, "票區容器"),
        ("tixcraft_ticket.html", TixcraftSelectors.TICKET_PRICE_SELECTS, "張數下拉"),
        ("tixcraft_ticket.html", TixcraftSelectors.AGREE_CHECKBOX, "同意條款"),
        ("tixcraft_ticket.html", TixcraftSelectors.CAPTCHA_IMAGE, "驗證碼圖片"),
        ("tixcraft_ticket.html", TixcraftSelectors.CAPTCHA_INPUT, "驗證碼輸入"),
        ("tixcraft_ticket.html", TixcraftSelectors.SUBMIT_BUTTON, "確認張數"),
        ("ibon_utk0201_000.html", IbonSelectors.ZONE_ROWS, "票區 area"),
        ("ibon_utk0201_000.html", IbonSelectors.ZONE_CONTAINER, "座位圖容器"),
        ("ibon_utk0202.html", IbonSelectors.TICKET_SELECTS, "張數下拉"),
        ("ibon_utk0202.html", IbonSelectors.SUBMIT_BUTTON, "下一步"),
    ],
)
def test_selector_matches_real_page(fixture: str, selector: str, label: str) -> None:
    assert soup(fixture).select(selector), f"{label} 的選擇器在 {fixture} 上找不到任何元素"


def test_tixcraft_session_buy_button_carries_navigation_url() -> None:
    """場次購票鈕沒有 href，網址在 data-href；抓錯屬性就等於沒有下一步。"""
    button = soup("tixcraft_game_list.html").select_one(
        TixcraftSelectors.SESSION_BUY_BUTTON
    )
    assert button is not None
    assert "/ticket/area/" in (button.get("data-href") or button.get("href") or "")


def test_ibon_zone_area_carries_send_call() -> None:
    """ibon 票區的下一步藏在 `javascript:Send(...)` 裡，不是 href 網址。"""
    area = soup("ibon_utk0201_000.html").select_one(IbonSelectors.ZONE_ROWS)
    assert area is not None
    assert "票區" in (area.get("title") or "")
    assert (area.get("href") or "").startswith("javascript:Send(")
