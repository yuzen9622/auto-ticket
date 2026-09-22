"""ibon 訂購頁解析：吃 HTML 字串吐結構化資料。

`GetGameInfoList` 的 `SoldOut` 旗標實測全站沒有人設（2026-09-22 掃 392 個活動的
場次，沒有一列是 true），所以「已售罄」只能從訂購頁
`orders.ibon.com.tw/application/UTK02/UTK0201_000.aspx` 讀回來。那一頁純 HTTP 會被
擋成 403，要真瀏覽器；但**不需要登入**，看得到完整的票區座位圖。

票區不是表格，是座位圖上的 `<area title="票區:… 票價：… 尚餘：…">`。`尚餘` 有三種
值：數字、`熱賣中`（多到不想印）、以及 `0`。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

ZONE_AREA_SELECTOR = "area[title]"
#: `熱賣中` 是「還很多」的意思，不是售完。
PLENTY_TEXTS = ("熱賣中", "熱賣")

_ZONE_TITLE_RE = re.compile(
    r"票區\s*[:：]\s*(?P<name>.*?)\s*票價\s*[:：]\s*(?P<price>\d+)"
    r"(?:\s*尚餘\s*[:：]\s*(?P<remaining>\S+))?"
)


@dataclass(frozen=True, slots=True)
class ZoneAvailability:
    """座位圖上的一個票區。"""

    name: str
    price: int
    remaining: int | None
    """解析得出的剩餘張數；`熱賣中` 或格式不認得時是 `None`。"""
    remaining_text: str
    """`尚餘` 後面的原字串，供事後追查。"""

    @property
    def sold_out(self) -> bool:
        return self.remaining == 0


def parse_zone_availability(html: str) -> list[ZoneAvailability]:
    """解析訂購頁座位圖上的票區。同一區可能有多個 `<area>`（形狀切片），一併去重。"""
    soup = BeautifulSoup(html, "html.parser")
    zones: dict[tuple[str, int], ZoneAvailability] = {}
    for area in soup.select(ZONE_AREA_SELECTOR):
        match = _ZONE_TITLE_RE.search(str(area.get("title") or ""))
        if match is None:
            continue
        remaining_text = (match.group("remaining") or "").strip()
        remaining: int | None = None
        if remaining_text.isdigit():
            remaining = int(remaining_text)
        zone = ZoneAvailability(
            name=match.group("name").strip(),
            price=int(match.group("price")),
            remaining=remaining,
            remaining_text=remaining_text,
        )
        zones.setdefault((zone.name, zone.price), zone)
    return list(zones.values())


def zones_are_sold_out(zones: list[ZoneAvailability]) -> bool | None:
    """每一區都寫 `尚餘：0` 才算售完；一區都沒解析到就回 `None`（不知道）。

    `熱賣中` 與解析不出來的區都當成「還有票」，因為它們明確不是 0——把不知道
    算成售完，會讓還買得到的活動從搜尋結果裡消失。
    """
    if not zones:
        return None
    return all(zone.sold_out for zone in zones)
