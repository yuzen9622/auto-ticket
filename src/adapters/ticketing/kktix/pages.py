"""KKTIX 購票登記頁解析：吃 HTML 字串吐結構化資料。

活動主頁只說得出「尚未開賣／結束販售／販售中」，**看不到售完**（掃 487 個活動主頁
沒有一頁把售完標在票種上）。售完只寫在購票登記頁
`https://kktix.com/events/<slug>/registrations/new`，而且那一頁純 HTTP 會被擋成
403，得用真瀏覽器打開；頁面本身是 AngularJS，不跑 JS 也拿不到票種列。

拆成純函式是為了讓「怎麼判斷售完」這件事跟「誰去開瀏覽器」分開，測試才能餵真頁面。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

#: 登記頁的一個票種區塊。
TICKET_UNIT_SELECTOR = ".ticket-unit, tr[id^='ticket_']"
#: 有張數輸入框就代表這個票種現在選得到。售完的票種整個輸入框都不會渲染出來，
#: 這比字樣可靠——字樣主辦可以自己改，輸入框在不在是 KKTIX 自己決定的。
QUANTITY_INPUT_SELECTOR = (
    "input[ng-model='ticketModel.quantity'], input.ticket-quantity"
)
TICKET_NAME_SELECTOR = ".ticket-name, td.name"
SOLD_OUT_TEXTS = ("已售完", "售完", "額滿", "sold out")

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class RegistrationTicket:
    """登記頁上的一個票種。"""

    name: str
    selectable: bool
    """張數輸入框在不在。"""
    status_text: str
    """整塊的文字，供事後追查。"""

    @property
    def sold_out(self) -> bool:
        lowered = self.status_text.lower()
        return not self.selectable and any(
            word.lower() in lowered for word in SOLD_OUT_TEXTS
        )


def parse_registration_tickets(html: str) -> list[RegistrationTicket]:
    """解析登記頁的票種清單。"""
    soup = BeautifulSoup(html, "html.parser")
    tickets: list[RegistrationTicket] = []
    for unit in soup.select(TICKET_UNIT_SELECTOR):
        name_node = unit.select_one(TICKET_NAME_SELECTOR)
        name = (
            _WHITESPACE_RE.sub(" ", name_node.get_text(" ", strip=True))
            if name_node is not None
            else ""
        )
        if not name:
            continue
        tickets.append(
            RegistrationTicket(
                name=name,
                selectable=unit.select_one(QUANTITY_INPUT_SELECTOR) is not None,
                status_text=_WHITESPACE_RE.sub(" ", unit.get_text(" ", strip=True)),
            )
        )
    return tickets


def registration_is_sold_out(tickets: list[RegistrationTicket]) -> bool | None:
    """全部票種都選不到就是售完；一個都沒解析到就回 `None`（不知道）。"""
    if not tickets:
        return None
    if any(ticket.selectable for ticket in tickets):
        return False
    return any(ticket.sold_out for ticket in tickets)
