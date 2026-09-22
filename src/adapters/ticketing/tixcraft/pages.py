"""拓元頁面解析：純函式，吃 HTML 字串吐結構化資料。

拆出來是因為同一份 HTML 有兩個來源：活動列表可以用 httpx 直接抓（被 Varnish
快取，沒有人機驗證），但節目介紹頁與場次頁一律會回 401 的 JS 驗證頁，只有真瀏
覽器拿得到。解析邏輯必須兩邊共用，才不會出現「API 端與 Worker 端各解析一套」。
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag

from adapters.ticketing.tixcraft.selectors import TixcraftSelectors

TAIPEI_TZ = ZoneInfo("Asia/Taipei")
BASE_URL = "https://tixcraft.com"

#: 列表頁的三個頁籤：「全部節目」「近期演出」「最新開賣」。三個都只是陳列方式，
#: 沒有一個等於「現在買得到」——`latest-selling` 是依上架時間排的推薦區，賣了一陣子
#: 的活動會掉出去，剛上架但還沒開賣的活動則會在裡面。售票狀態只有場次頁說得準。
TAB_ALL = "all"
TAB_UPCOMING = "upcoming-activity"
TAB_LATEST_SELLING = "latest-selling"

#: 場次列的售完字樣。「選購一空」與「已售完」意思不同：前者購票鈕還在（還進得去
#: 票區頁，可能有回流票），後者連鈕都不給。兩者都代表當下沒票。
SOLD_OUT_TEXTS = ("選購一空", "已售完", "sold out", "完售")
#: 場次列沒有任何場次時，整張表只有這一格。
NO_SESSION_TEXT = "目前無場次資訊"
#: 該場次的購票時間已過，格子裡只剩「<日期> 截止」。
DEADLINE_TEXT = "截止"

_DATE_RE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})")
_DATETIME_RE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})[^\d]*?(\d{1,2}):(\d{2})")

ORGANIZER_LABELS = ("主辦單位", "主辦", "呈獻單位", "共同主辦", "指導單位")
ORGANIZER_REJECT_WORDS = (
    "保有",
    "保留",
    "有權",
    "規定",
    "公告",
    "權利",
    "變更",
    "不負",
    "謝絕",
    "拒絕",
    "請出",
)
#: 中文標籤後面常常再跟一個英文標籤（「主辦單位 Organizer：…」），要一起吃掉。
_ORGANIZER_RE = re.compile(
    r"(?:{labels})\s*(?:organizer|organiser|host|presented\s+by)?"
    r"\s*[：:︰/／｜|]\s*([^\r\n]{{2,60}})".format(labels="|".join(ORGANIZER_LABELS)),
    re.IGNORECASE,
)


#: 節目介紹前面常見的售票規則樣板，當描述開頭會讓每個活動長得一樣。
INTRO_NOISE_MARKERS = (
    "欲購票者",
    "手機號碼驗證",
    "本平台所售出之活動",
    "購票前請詳閱",
    "拓元會員加入辦法",
    "檢舉黃牛",
    "系統服務費",
)
_DECORATION_ONLY_RE = re.compile(
    r"^[\s\d\W_]*$|^[-=*＊※●・‧|｜]+$", re.UNICODE
)

#: 票種列的文字是「票種名 + 票價」，票價一定在尾端（`全票 1,450`）。
_TICKET_LABEL_RE = re.compile(r"^(?P<name>.*?)[\s　]*(?P<price>\d[\d,]*)\s*元?$")

#: 票區列的可售狀態寫成「剩餘 14」，是拓元少數會把庫存印在頁面上的地方。
_REMAINING_RE = re.compile(r"剩餘\s*(\d+)")


@dataclass(frozen=True, slots=True)
class ActivityListing:
    """活動列表上的一筆活動。列表是拓元唯一免人機驗證的資料來源。"""

    slug: str
    title: str
    url: str
    venue: str | None = None
    date_text: str | None = None
    event_start_at: datetime | None = None
    image_url: str | None = None
    tabs: tuple[str, ...] = ()
    """活動出現在哪幾個頁籤。只是陳列位置，不代表售票狀態——曾經拿
    `latest-selling` 當販售中，全站 73 個活動有 18 個判錯。"""


class SessionSaleState(str, Enum):
    """場次列的售票狀態。

    這五種是拓元場次頁實際會長出來的全部型態（2026-09-22 抓全站 73 個活動、
    177 列場次核對過），每一種對應一組固定的 DOM，不是從文字猜的：

    * `ON_SALE`：只有 `button[data-href]`。
    * `ZONE_EMPTY`：`button[data-href]` 旁邊多一個「選購一空」的 div——入口還在，
      但票區全空；這是「買不到」，不是「還沒開賣」。
    * `SOLD_OUT`：`div.text-center` 寫「已售完」，沒有按鈕。
    * `DEADLINE_PASSED`：`div.text-center.text-info` 寫「<日期> 截止」，沒有按鈕。
    * `UNKNOWN`：以上皆非，留給版型變動時不要亂猜。
    """

    ON_SALE = "on_sale"
    ZONE_EMPTY = "zone_empty"
    SOLD_OUT = "sold_out"
    DEADLINE_PASSED = "deadline_passed"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class GameSession:
    """場次頁 `#gameList` 的一列。"""

    key: str | None
    starts_at: datetime | None
    display_time: str
    name: str
    venue: str | None
    status_text: str
    purchase_url: str | None
    state: SessionSaleState = SessionSaleState.UNKNOWN

    @property
    def sold_out(self) -> bool:
        return self.state in (SessionSaleState.SOLD_OUT, SessionSaleState.ZONE_EMPTY)

    @property
    def buyable(self) -> bool:
        return self.state is SessionSaleState.ON_SALE and self.purchase_url is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "venue": self.venue,
            "display_date": self.display_time,
            "starts_at": self.starts_at.isoformat() if self.starts_at else None,
            "status_text": self.status_text,
            "state": self.state.value,
            "can_buy": self.buyable,
            "sold_out": self.sold_out,
            "purchase_url": self.purchase_url,
        }


@dataclass(frozen=True, slots=True)
class ZoneRow:
    """票區頁 `ul.area-list` 的一個票區。"""

    index: int
    """在 `ZoneSelectors.ZONE_LINKS` 找到的第幾個連結；要點哪一區就靠它。"""
    name: str
    price: int
    remaining: int | None
    sold_out: bool
    status_text: str
    """整列的原始文字，供事後追查。"""


@dataclass(frozen=True, slots=True)
class TicketRow:
    """張數頁 `#ticketPriceList` 的一列票種。"""

    index: int
    name: str
    price: int
    select_id: str | None
    """該列張數下拉的 id。沒有下拉就是這個票種當下買不到——這比字樣可靠，
    售完的票種拓元不一定寫「已售完」，但一定不給你選張數。"""
    status_text: str

    @property
    def available(self) -> bool:
        lowered = self.status_text.lower()
        if any(word.lower() in lowered for word in SOLD_OUT_TEXTS):
            return False
        return self.select_id is not None


@dataclass(slots=True)
class ActivityDetail:
    """節目介紹頁能挖到的東西。"""

    title: str | None = None
    description: str | None = None
    organizer: str | None = None
    venue: str | None = None
    date_text: str | None = None
    event_start_at: datetime | None = None
    sessions: list[GameSession] = field(default_factory=list)
    sessions_seen: bool = False
    """是否真的看過場次表。`sessions` 空掉有兩種原因——活動還沒開賣（場次表寫
    「目前無場次資訊」），或這次根本沒抓到場次頁。只有前者能推論出尚未開賣。"""


def slug_of(url: str) -> str:
    return urlsplit(url).path.rstrip("/").split("/")[-1]


def detail_url(slug: str) -> str:
    return f"{BASE_URL}/activity/detail/{slug}"


def game_url(slug: str) -> str:
    return f"{BASE_URL}/activity/game/{slug}"


def _taipei(year: int, month: int, day: int, hour: int = 0, minute: int = 0):
    try:
        return datetime(year, month, day, hour, minute, tzinfo=TAIPEI_TZ).astimezone(
            timezone.utc
        )
    except ValueError:
        return None


def parse_date_text(text: str | None) -> datetime | None:
    """`2027/05/01 (六)  ~ 2027/05/02 (日)` 或 `2027/05/01 (六) 18:45` 取開始時間。"""
    if not text:
        return None
    dt_match = _DATETIME_RE.search(text)
    if dt_match is not None:
        y, mo, d, h, mi = (int(g) for g in dt_match.groups())
        return _taipei(y, mo, d, h, mi)
    date_match = _DATE_RE.search(text)
    if date_match is not None:
        y, mo, d = (int(g) for g in date_match.groups())
        return _taipei(y, mo, d)
    return None


def parse_activity_list(html: str) -> list[ActivityListing]:
    """解析 `/activity`。每張卡片給的是標題、日期、場地與所屬頁籤。"""
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict[str, Any]] = {}

    for tab in (TAB_ALL, TAB_UPCOMING, TAB_LATEST_SELLING):
        pane = soup.select_one(f"#{tab}")
        if pane is None:
            continue
        for card in pane.select(".eventbl"):
            link = card.select_one("a[href*='/activity/detail/']")
            if link is None:
                continue
            href = str(link.get("href") or "")
            slug = slug_of(href)
            if not slug:
                continue

            title_el = card.select_one(".text-bold a") or link
            title = title_el.get_text(strip=True)
            if not title or title == "節目介紹":
                continue

            record = found.setdefault(
                slug,
                {
                    "slug": slug,
                    "title": title,
                    "url": urljoin(BASE_URL, href),
                    "venue": None,
                    "date_text": None,
                    "image_url": None,
                    "tabs": [],
                },
            )
            record["tabs"].append(tab)
            date_el = card.select_one(".date")
            if date_el is not None and not record["date_text"]:
                record["date_text"] = date_el.get_text(" ", strip=True)
            venue_el = card.select_one(".text-med-light")
            if venue_el is not None and not record["venue"]:
                record["venue"] = venue_el.get_text(strip=True) or None
            img = card.select_one("img")
            if img is not None and not record["image_url"]:
                record["image_url"] = str(img.get("src") or "") or None

    listings: list[ActivityListing] = []
    for record in found.values():
        listings.append(
            ActivityListing(
                slug=record["slug"],
                title=record["title"],
                url=record["url"],
                venue=record["venue"],
                date_text=record["date_text"],
                event_start_at=parse_date_text(record["date_text"]),
                image_url=record["image_url"],
                tabs=tuple(record["tabs"]),
            )
        )
    return listings


def classify_session_cell(cell: Tag) -> SessionSaleState:
    """從場次列最後一格的 DOM 判斷售票狀態。

    刻意不做整格字串比對：可購買的場次那一格同時有「立即訂購」按鈕與「選購一空」
    標籤，把整格文字接起來比對會讓兩個關鍵字同時命中，於是能買的場次被判成售完、
    售完的場次又因為有按鈕被判成能買。按鈕在不在是結構事實，先看它。
    """
    text = cell.get_text(" ", strip=True)
    if NO_SESSION_TEXT in text:
        return SessionSaleState.UNKNOWN
    if cell.select_one("button[data-href], a[href*='/ticket/area/']") is not None:
        lowered = text.lower()
        if any(word.lower() in lowered for word in SOLD_OUT_TEXTS):
            return SessionSaleState.ZONE_EMPTY
        return SessionSaleState.ON_SALE
    lowered = text.lower()
    if any(word.lower() in lowered for word in SOLD_OUT_TEXTS):
        return SessionSaleState.SOLD_OUT
    if DEADLINE_TEXT in text:
        return SessionSaleState.DEADLINE_PASSED
    return SessionSaleState.UNKNOWN


def has_game_list(html: str) -> bool:
    """頁面上是否真的有場次表。

    場次是空的（「目前無場次資訊」）跟「這份 HTML 根本不是場次頁」是兩件事：
    前者代表活動還沒開賣，後者代表我們什麼都不知道。少了這個區分，節目介紹頁
    會被當成「查過了、沒有場次」，把每個活動都標成尚未開賣。
    """
    return BeautifulSoup(html, "html.parser").select_one("#gameList") is not None


def parse_game_list(html: str) -> list[GameSession]:
    """解析 `/activity/game/<slug>` 的 `#gameList`。

    購票按鈕沒有 href，網址掛在 `data-href`；直接點它在無頭環境不一定觸發導頁，
    讀出網址自己導才穩。「目前無場次資訊」那一列不是場次，直接跳過。
    """
    soup = BeautifulSoup(html, "html.parser")
    sessions: list[GameSession] = []
    for row in soup.select("#gameList > table > tbody > tr"):
        tds = row.select("td")
        cells = [td.get_text(" ", strip=True) for td in tds]
        if not cells:
            continue
        if NO_SESSION_TEXT in " ".join(cells):
            continue
        button = row.select_one("button[data-href]")
        anchor = row.select_one("a[href]")
        purchase_url: str | None = None
        if button is not None:
            purchase_url = urljoin(BASE_URL, str(button.get("data-href") or ""))
        elif anchor is not None:
            purchase_url = urljoin(BASE_URL, str(anchor.get("href") or ""))

        display_time = cells[0] if cells else ""
        name = cells[1] if len(cells) > 1 else ""
        venue = cells[2] if len(cells) > 2 else None
        status_text = cells[3] if len(cells) > 3 else ""
        sessions.append(
            GameSession(
                key=str(row.get("data-key") or "") or None,
                starts_at=parse_date_text(display_time),
                display_time=display_time,
                name=name,
                venue=venue,
                status_text=status_text,
                purchase_url=purchase_url,
                state=classify_session_cell(tds[-1]),
            )
        )
    return sessions


def extract_intro_description(html: str, *, max_chars: int = 300) -> str | None:
    """節目介紹取 `#intro` 分頁，跳過售票規則樣板段落。"""
    soup = BeautifulSoup(html, "html.parser")
    intro = soup.select_one("#intro") or soup.select_one("#activityTabContent")
    if intro is None:
        return None
    lines = [line.strip() for line in intro.get_text("\n").splitlines()]
    kept: list[str] = []
    for line in lines:
        if len(line) < 6 or _DECORATION_ONLY_RE.match(line):
            continue
        if any(marker in line for marker in INTRO_NOISE_MARKERS):
            continue
        kept.append(line)
        if sum(len(x) for x in kept) >= max_chars:
            break
    text = " ".join(kept).strip()
    if not text:
        return None
    return text[: max_chars - 1] + "…" if len(text) > max_chars else text


def extract_organizer(html: str) -> str | None:
    """拓元沒有主辦單位欄位，只有節目介紹裡的「主辦單位/xxx」一行。

    有些場（尤其國際巡演）連那一行都沒有。那就讓它空著：曾經改用「內文裡出現
    哪家主辦的名字」來猜，結果把內文引用到別家公告的場次標成錯的主辦單位——
    寫錯的主辦單位比沒有主辦單位更糟。
    """
    soup = BeautifulSoup(html, "html.parser")
    scope = soup.select_one("#intro") or soup.select_one("#activityTabContent") or soup
    text = scope.get_text("\n")
    for match in _ORGANIZER_RE.finditer(text):
        value = match.group(1).strip(" 　:：/／｜|")
        value = re.split(r"[。；;，,]", value)[0].strip()
        if len(value) < 2 or any(w in value for w in ORGANIZER_REJECT_WORDS):
            continue
        return value[:60]
    return None


def parse_activity_detail(html: str, *, game_html: str | None = None) -> ActivityDetail:
    """把節目介紹頁（可再加場次頁）解析成一份完整的活動資料。"""
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.select_one(".activityContent h1") or soup.select_one("h1")
    title = heading.get_text(strip=True) if heading is not None else None

    venue: str | None = None
    date_text: str | None = None
    page_title = soup.title.get_text(strip=True) if soup.title else ""
    # `<標題> @ <場地> | <日期> | tixcraft拓元售票`
    parts = [p.strip() for p in page_title.split("|")]
    if parts and "@" in parts[0]:
        venue = parts[0].split("@", 1)[1].strip() or None
    if len(parts) > 1:
        date_text = parts[1] or None

    session_html = game_html if game_html else html
    sessions = parse_game_list(session_html)
    sessions_seen = has_game_list(session_html)
    if not venue:
        venue = next((s.venue for s in sessions if s.venue), None)
    session_starts = [s.starts_at for s in sessions if s.starts_at is not None]

    return ActivityDetail(
        title=title,
        description=extract_intro_description(html),
        organizer=extract_organizer(html),
        venue=venue,
        date_text=date_text,
        event_start_at=(
            min(session_starts) if session_starts else parse_date_text(date_text)
        ),
        sessions=sessions,
        sessions_seen=sessions_seen,
    )


def split_ticket_label(text: str) -> tuple[str, int]:
    """`全票 1,450` → `("全票", 1450)`。

    票種名稱本身可能含數字（「VIP2 全票」），票價永遠寫在最後，所以從字串尾端
    取數字，不能拿第一個出現的數字當價格。整串沒有價格就回 0，代表這一列沒印
    價格——那是「不知道」，不是「免費」。
    """
    cleaned = " ".join(text.split())
    match = _TICKET_LABEL_RE.match(cleaned)
    if match is None:
        return cleaned, 0
    try:
        price = int(match.group("price").replace(",", ""))
    except ValueError:
        return cleaned, 0
    name = match.group("name").strip(" -/｜|")
    return name or cleaned, price


def parse_ticket_rows(html: str) -> list[TicketRow]:
    """解析張數頁的 `#ticketPriceList`。

    這張表就是拓元唯一寫出「全票／優待票／身障票」與各自票價的地方；票區頁只有
    區名，節目介紹頁只有一串沒有票種對應的價目。
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("#ticketPriceList")
    if table is None:
        return []

    # 表頭用 `<th>`，內容列用 `<td>`；沒有 `<tbody>` 的版型就靠這點區分。
    body_rows = table.select("tbody tr") or [
        tr for tr in table.select("tr") if tr.find("td")
    ]

    rows: list[TicketRow] = []
    for idx, tr in enumerate(body_rows):
        label_el = tr.select_one(".text-bold")
        name, price = split_ticket_label((label_el or tr).get_text(" ", strip=True))
        select = tr.select_one("select")
        select_id = (
            (str(select.get("id") or "") or None) if select is not None else None
        )
        if not name and select_id is None:
            continue
        rows.append(
            TicketRow(
                index=idx,
                name=name or f"票種 {idx + 1}",
                price=price,
                select_id=select_id,
                status_text=tr.get_text(" ", strip=True),
            )
        )
    return rows


def parse_zone_rows(html: str) -> list[ZoneRow]:
    """解析票區頁 `/ticket/area/<slug>/<gameId>`。

    一列長這樣（`<span>` 是色塊，`<font>` 是可售狀態）：

        <a id="22949_37"><span>&nbsp;</span>1樓身障 G Island F區1790 <font>剩餘 14</font></a>

    票價**黏在區名後面**、而且區名自己就以樓層數字開頭，所以「抓第一個數字當票價」
    會把「1樓」讀成 1 塊錢；票價要從扣掉狀態字之後的尾端取。售完的票區整個沒有
    `<a>`，連點都點不到，因此這裡看到的基本上都是還買得到的。

    走 `ZONE_LINKS` 而不是自己另寫選擇器，是因為 `index` 必須和 Adapter 點擊時
    拿到的連結順序完全一致。
    """
    soup = BeautifulSoup(html, "html.parser")
    rows: list[ZoneRow] = []
    for idx, link in enumerate(soup.select(TixcraftSelectors.ZONE_LINKS)):
        status_text = link.get_text(" ", strip=True)

        label_el = copy.copy(link)
        if isinstance(label_el, Tag):
            for font in label_el.find_all("font"):
                font.extract()
        label = label_el.get_text(" ", strip=True)
        name, price = split_ticket_label(label)

        state = link.find("font")
        state_text = state.get_text(" ", strip=True) if isinstance(state, Tag) else ""
        matched = _REMAINING_RE.search(state_text or status_text)
        lowered = status_text.lower()
        rows.append(
            ZoneRow(
                index=idx,
                name=name or f"區域 {idx + 1}",
                price=price,
                remaining=int(matched.group(1)) if matched else None,
                sold_out=any(word.lower() in lowered for word in SOLD_OUT_TEXTS),
                status_text=status_text,
            )
        )
    return rows
