# pyright: reportMissingImports=false
# ruff: noqa: S112, SIM105, S110
"""KKTIX 購票流程 adapter。

契約：
* 選擇器**一律**經 `KKTIXSelectors` 屬性存取，本檔不得出現任何選擇器字面值。
* DOM 讀取與決策分離：本檔只把頁面讀成不可變快照，決策交給 `strategy`。
* Cloudflare 挑戰只偵測、截圖、回報，**不實作繞過**——這是研究系統，不是規避工具。
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Sequence
from enum import Enum
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from adapters.payment.base import PaymentOutcome, PaymentProvider, PaymentResult
from adapters.ticketing.base import TicketingAdapter
from adapters.ticketing.kktix.dom import (
    DEFAULT_OPTIONAL_PROBE_MS,
    contains_cloudflare_challenge,
    first_visible,
    ng_click,
    ng_fill,
    page_text,
    read_input_value,
)
from adapters.ticketing.kktix.selectors import KKTIXSelectors
from adapters.verification.base import (
    ChallengeKind,
    VerificationChallenge,
    VerificationProvider,
)
from domain.preference import SeatPreference, TicketPreference
from domain.task import AttendeeProfile, CreditCardProfile, UserContactProfile
from strategy.seat_strategy import DOWNGRADE_MARK, SeatAction, decide_seat_action
from strategy.ticket_strategy import TicketDecision, TicketOption, decide_ticket
from telemetry.timeline import TimelineEventType, TimelineRecorder

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page
else:
    Locator = Any
    Page = Any

# select_tickets 的理由碼；協調器依此對應 FSM 事件。
REASON_SELECTED = "SELECTED"
REASON_SOLD_OUT = "SOLD_OUT"
REASON_NO_TICKET_UNITS = "NO_TICKET_UNITS"
REASON_PLUS_BUTTON_MISSING = "PLUS_BUTTON_MISSING"
REASON_QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
REASON_TERMS_NOT_ACCEPTED = "TERMS_NOT_ACCEPTED"
REASON_CLOUDFLARE = "CLOUDFLARE_CHALLENGE"
REASON_NOT_REGISTRATION_PAGE = "NOT_REGISTRATION_PAGE"

ALL_TICKET_REASONS = frozenset(
    {
        REASON_SELECTED,
        REASON_SOLD_OUT,
        REASON_NO_TICKET_UNITS,
        REASON_PLUS_BUTTON_MISSING,
        REASON_QUANTITY_MISMATCH,
        REASON_TERMS_NOT_ACCEPTED,
        REASON_NOT_REGISTRATION_PAGE,
    }
)

CLOUDFLARE_MARK = "cloudflare_challenge"
FAILURE_MODAL_MARK = "failure_modal_dismissed"
FAILURE_MODAL_MISSING_MARK = "failure_modal_dismiss_missing"
RESET_MARK = "ticket_quantities_reset"

# 歸零失敗的細分理由：只進 timeline，**不**併入 `ALL_TICKET_REASONS`——
# 那份清單與 FSM 事件表一一對應，混進沒有事件的理由碼會讓對應關係失效。
RESET_REASON_NO_QUANTITY_FIELD = "NO_QUANTITY_FIELD"
RESET_REASON_MINUS_MISSING = "MINUS_BUTTON_MISSING"
RESET_REASON_UNREADABLE = "QUANTITY_UNREADABLE"
RESET_REASON_NOT_ZERO = "QUANTITY_NOT_ZERO"
ZERO_QUANTITY = "0"

# 頁面狀態的 URL 物理特徵。訂單完成與付款頁共用 `/orders/` 前綴，
# 唯一的差別就是後綴有沒有 `/payment`——判定順序因此不可調換。
ORDER_URL_MARKER = "/orders/"
PAYMENT_URL_MARKER = "/payment"
# KKTIX 頁面帶著分析／廣告等長尾資源，`load` 事件常常遲遲不觸發。
# 搶票要的是「DOM 可以操作了」，不是「所有資源都下載完了」——等 load 只會白等。
NAVIGATION_WAIT_UNTIL = "domcontentloaded"
DEFAULT_NAVIGATION_TIMEOUT_MS = 30000
# 同一場活動有多個等價入口：`https://<org>.kktix.cc/events/<slug>` 與
# `https://kktix.com/events/<slug>/registrations/new` 指的是同一件事，主機名因此
# 不是可靠的身分——活動 slug 與「是否已經在登記頁上」才是。
EVENT_PATH_RE = re.compile(
    r"\A/events/(?P<slug>[^/]+)(?P<registration>/registrations/new)?/?\Z"
)
# 登記頁只掛在 `kktix.com` 上：org 子網域的 `/registrations/new` 會被 301 打回
# kktix.com 首頁（連 path 都不保留），活動主頁上的購票連結指的也是這個主機。
REGISTRATION_ORIGIN = "https://kktix.com"

# 以下選擇器刻意放在這裡而不是 selectors.py：後者是凍結模組（check_invariants G1），
# 任何改動都會讓守門員紅燈。

#: Angular 還沒編譯模板時，原始 mustache 會留在 HTML 裡。只看容器存在會把
#: 「還在轉圈的頁」與「母活動的空頁」都誤判成可下單的登記頁。
REGISTRATION_UNRENDERED_MARKERS = ("{{'new.i_read_and_agree_to'", "{{'new.")

#: 多場次活動：母活動頁列出各場次，每張卡片有自己的「下一步」連到該場次的登記頁。
#: 母活動本身的 /registrations/new 是空的，不先選場次就永遠看不到票種。
EVENT_SESSION_ITEMS = "div.event-list ul.clearfix > li"
#: 沒有場次卡片結構時的退路；單場次活動會有多顆按鈕指向同一個網址，需去重。
EVENT_SESSION_LINKS = "a[href*='registrations/new']"

#: 主辦自訂的 radio 欄位（聯絡人與參加者兩種範圍）。KKTIX 用「單一選項的 radio
#: 群組」表達必選的確認事項（例如「我同意系統配位、不得更改或退款」），沒選就送不出
#: 訂單；既有程式只處理 checkbox，於是停在填表頁直到預算耗盡。
DYNAMIC_RADIO = "input[type='radio'][name^='contact['], input[type='radio'][name^='attendees[']"

#: 登入頁要求人工驗證時的字樣。出現它代表帳密沒被受理，**不是**帳密錯誤。
LOGIN_HUMAN_VERIFICATION_TEXTS = (
    "請完成驗證後再試一次",
    "請完成驗證",
    "complete the verification",
)
SOLD_OUT_MARKERS = ("售完", "售罄", "完售", "sold out", "已結束", "已額滿")
REMAINING_RE = re.compile(r"(?:剩餘|剩下|remaining)\D{0,4}(\d+)", re.IGNORECASE)
DIGITS_RE = re.compile(r"\d+")


def to_registration_url(event_url: str) -> str:
    """把活動主頁網址換成同一場活動的登記頁網址；認不出來的網址原樣回傳。

    活動主頁下不了單，而開賣前的就緒閘門只認登記頁。沒有這一步轉換，帶著主頁網址
    的任務會因為「已經停在目標頁」而永遠不再導航，一路輪詢到閘門逾時。
    """
    match = EVENT_PATH_RE.match(urlsplit(event_url).path)
    if match is None or match.group("registration") is not None:
        return event_url
    return f"{REGISTRATION_ORIGIN}/events/{match.group('slug')}/registrations/new"


class KKTIXPageKind(str, Enum):
    """目前停在哪一種頁面。票種的讀法在兩種頁面上完全不同，不得混用。"""

    EVENT = "EVENT"
    """公開活動主頁：票種是一張表格，只看得到售賣時段，看不到可購買數量。"""
    REGISTRATION = "REGISTRATION"
    """購票登記頁：票種是可加減數量的單元，這裡才下得了單。"""
    ORDER = "ORDER"
    """劃位與訂單填寫頁：已經有訂單了，這裡填聯絡人與參加人。"""
    LOGIN = "LOGIN"
    """被導到登入頁：需要人自己登入，本專案不自動填任何憑證。"""
    CHALLENGE = "CHALLENGE"
    """人機驗證挑戰頁：只偵測與回報，不繞過；需要人自己在瀏覽器裡通過。"""
    UNKNOWN = "UNKNOWN"
    """以上皆非——多半是被導去登入頁或錯誤頁。"""


class PageState(str, Enum):
    """搶票迴圈每一輪實際看到的頁面狀態。

    與 `KKTIXPageKind`（「這是哪一類網址」）刻意分離：這裡描述的是「現在該做哪個
    動作」，粒度細到彈窗與局部區塊。**絕不**包含 `SOLD_OUT`——售罄是策略層看完
    票種快照後的結論，不是頁面上讀得到的物理特徵，在這一層臆造它就是汙染研究資料。
    """

    FAILURE_MODAL = "FAILURE_MODAL"
    """「別人搶先一步」「已無可配座位」——這一輪搶輸了，得換票種。"""
    GUEST_MODAL = "GUEST_MODAL"
    """未登入訪客彈窗：KKTIX 勸你先成為會員。"""
    QUEUE = "QUEUE"
    """排隊等候室。**嚴禁 reload**：重整會被丟回隊伍尾端。"""
    COMPLETED = "COMPLETED"
    """訂單完成頁（未付款保留訂單亦算抵達）。"""
    PAYMENT_REQUIRED = "PAYMENT_REQUIRED"
    """付款頁。"""
    QUALIFICATION_CODE = "QUALIFICATION_CODE"
    """專屬會員碼／邀請碼的資格審查區塊。"""
    FORM_FILLING = "FORM_FILLING"
    """聯絡人與動態同意條款表單；問答題同屬這張表單，故優先於局部驗證題。"""
    VERIFICATION_CHALLENGE = "VERIFICATION_CHALLENGE"
    """只有題幹與答案欄、沒有表單欄位的獨立驗證題。"""
    SEAT_SELECTION = "SEAT_SELECTION"
    """登記頁且**已選數量 > 0**：該按配位或下一步了。"""
    TICKET_SELECTION = "TICKET_SELECTION"
    """登記頁且**已選數量 == 0**：還沒選票。"""
    UNKNOWN = "UNKNOWN"
    """過渡暫態。呼叫端只能微等待後重判，逾時即 fail-closed。"""


class CloudflareChallengeError(RuntimeError):
    """偵測到人機驗證挑戰；一律 fail-closed 中止，不嘗試繞過。"""


class KKTIXAdapter(TicketingAdapter):
    def __init__(
        self,
        *,
        telemetry: TimelineRecorder,
        payment: PaymentProvider,
        verification: VerificationProvider | None = None,
        attendees: Sequence[AttendeeProfile] = (),
        timeout_ms: int = 5000,
        probe_timeout_ms: int | None = None,
        navigation_timeout_ms: int = DEFAULT_NAVIGATION_TIMEOUT_MS,
        screenshot: Callable[[str], Awaitable[Any]] | None = None,
    ) -> None:
        self.telemetry = telemetry
        self.payment = payment
        self.verification = verification
        self.attendees = tuple(attendees)
        self.timeout_ms = timeout_ms
        # 「這個選配元素在不在」與「等這個必要元素出現」是兩件事：前者以不存在為常態，
        # 給它整份 timeout 等於每次都把預算燒完——開賣瞬間這筆帳付不起。
        # 且探測預算永遠不得超過元素預算：呼叫端把 timeout_ms 調小時它要跟著縮，
        # 否則「快速探測」反而成為整條流程最漫長的一步。
        self.probe_timeout_ms = min(
            DEFAULT_OPTIONAL_PROBE_MS if probe_timeout_ms is None else probe_timeout_ms,
            timeout_ms,
        )
        self.navigation_timeout_ms = navigation_timeout_ms
        self.screenshot = screenshot
        self.last_ticket_decision: TicketDecision | None = None
        self.last_payment_result: PaymentResult | None = None

    # ------------------------------------------------------------------ 共用

    async def _locate(
        self, root: Any, selectors: str | Sequence[str], field: str
    ) -> Locator | None:
        return await first_visible(
            root,
            selectors,
            timeout_ms=self.timeout_ms,
            telemetry=self.telemetry,
            field=field,
        )

    async def _guard_cloudflare(self, page: Page, stage: str) -> None:
        marker = contains_cloudflare_challenge(await page_text(page))
        if marker is None:
            return
        if self.screenshot is not None:
            await self.screenshot(f"{CLOUDFLARE_MARK}_{stage}")
        error = CloudflareChallengeError(
            f"偵測到人機驗證挑戰（stage={stage}, marker={marker}）；依安全邊界不實作繞過"
        )
        self.telemetry.record_error(CLOUDFLARE_MARK, error, stage=stage, marker=marker)
        raise error

    @staticmethod
    async def _text_of(root: Any, selectors: str | Sequence[str]) -> str:
        from adapters.ticketing.kktix.dom import candidate_selectors

        for selector in candidate_selectors(selectors):
            locator = root.locator(selector).first
            try:
                if await locator.count() == 0:
                    continue
                return str(await locator.inner_text()).strip()
            except Exception:
                continue
        return ""

    @staticmethod
    async def _has(root: Any, selectors: str | Sequence[str]) -> bool:
        from adapters.ticketing.kktix.dom import candidate_selectors

        for selector in candidate_selectors(selectors):
            try:
                if await root.locator(selector).count() > 0:
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    async def _first_present(
        root: Any, selectors: str | Sequence[str]
    ) -> Locator | None:
        """依候選順序回傳第一個**存在**的元素，不等待、不要求可見。

        狀態偵測與歸零回讀都跑在每 50ms 一輪的熱迴圈上，付不起
        `first_visible` 的逾時預算：一輪等下來搶票就結束了。
        """
        from adapters.ticketing.kktix.dom import candidate_selectors

        for selector in candidate_selectors(selectors):
            locator = root.locator(selector).first
            try:
                if await locator.count() == 0:
                    continue
            except Exception:
                continue
            return locator
        return None

    @staticmethod
    async def _visible_locators(
        root: Any, selectors: str | Sequence[str]
    ) -> list[Locator]:
        """命中且**實際可見**的全部元素（立即判定，不等待）。

        Bootstrap 彈窗的骨架常駐留在 DOM 裡，只看 `count()` 會把一個從未顯示
        的模板讀成「正在彈的失敗訊息」，直接把整場搶票誤判成搶輸。
        """
        from adapters.ticketing.kktix.dom import candidate_selectors

        found: list[Locator] = []
        for selector in candidate_selectors(selectors):
            try:
                matched = await root.locator(selector).all()
            except Exception:
                continue
            for locator in matched:
                try:
                    visible = await locator.is_visible()
                except Exception:
                    continue
                if visible:
                    found.append(locator)
        return found

    async def _has_visible(self, root: Any, selectors: str | Sequence[str]) -> bool:
        return bool(await self._visible_locators(root, selectors))

    async def _visible_text(self, root: Any, selectors: str | Sequence[str]) -> str:
        chunks: list[str] = []
        for locator in await self._visible_locators(root, selectors):
            try:
                chunks.append(str(await locator.inner_text()))
            except Exception:
                continue
        return " ".join(chunks)

    # ------------------------------------------------------- 1. 導航與開賣偵測

    @staticmethod
    def _current_url(page: Page) -> str:
        return str(getattr(page, "url", "") or "")

    @staticmethod
    def _event_identity(url: str) -> tuple[str, bool] | None:
        """（活動 slug, 是否為登記頁）；不是活動網址就回 `None`。"""
        match = EVENT_PATH_RE.match(urlsplit(url).path)
        if match is None:
            return None
        return match.group("slug"), match.group("registration") is not None

    @classmethod
    def _is_same_event_or_registration(cls, current_url: str, target_url: str) -> bool:
        """目前這一頁是否已經是 `target_url`（或它的登記頁）。

        登記頁比活動頁更深：停在登記頁時導回活動頁只會把已到手的位置丟掉；
        反向不成立——停在活動頁而目標是登記頁時，還沒走到下得了單的那一頁。
        """
        if not current_url or not target_url:
            return False
        if current_url == target_url:
            return True
        current = cls._event_identity(current_url)
        target = cls._event_identity(target_url)
        if current is None or target is None:
            return False
        slug, on_registration = current
        target_slug, target_is_registration = target
        return slug == target_slug and (on_registration or not target_is_registration)

    async def list_sessions(self, page: Page) -> list[dict[str, str]]:
        """讀出活動頁上的場次清單（標籤＋該場次的登記頁網址）。

        優先認多場次活動的場次卡片結構；沒有那個結構時，退而掃描頁面上指向登記頁的
        連結——單場次活動的「立即購票」「下一步」會有好幾顆按鈕指向**同一個**網址，
        所以一律以網址去重，否則一場會被數成三場而挑不出唯一解。
        """
        sessions: list[dict[str, str]] = []
        try:
            sessions = await page.eval_on_selector_all(
                EVENT_SESSION_ITEMS,
                """els => els.map(li => {
                     const a = li.querySelector(
                       'div.content > a.btn-point, a[href*="registrations/new"]'
                     );
                     return a ? {url: a.href, label: li.innerText.trim()} : null;
                   }).filter(Boolean)""",
            )
        except Exception:
            sessions = []

        if not sessions:
            try:
                sessions = await page.eval_on_selector_all(
                    EVENT_SESSION_LINKS,
                    """els => els.map(e => {
                         const card = e.closest('div');
                         return {
                           url: e.href,
                           label: (card ? card.innerText : e.innerText).trim(),
                         };
                       })""",
                )
            except Exception:
                return []

        return self._dedupe_sessions(sessions)

    @staticmethod
    def _dedupe_sessions(sessions: list[dict[str, str]]) -> list[dict[str, str]]:
        """同一個登記頁網址只算一場；保留第一個（標籤通常最完整）。"""
        seen: set[str] = set()
        unique: list[dict[str, str]] = []
        for session in sessions:
            url = str(session.get("url", ""))
            if not url or url in seen:
                continue
            seen.add(url)
            unique.append(session)
        return unique

    @staticmethod
    def pick_session(
        sessions: list[dict[str, str]], preference: str | None
    ) -> str | None:
        """挑出要買的場次網址。

        挑不出唯一一個就回 None，由呼叫端 fail-closed——**絕不**替使用者亂猜場次，
        買錯場次跟買不到一樣糟，而且是不可逆的。
        """
        unique = KKTIXAdapter._dedupe_sessions(sessions)
        if not unique:
            return None
        # 只有一個登記頁時偏好沒有意義——單場次活動本來就只有一場可買。
        if len(unique) == 1:
            return unique[0]["url"]
        if preference:
            wanted = preference.strip()
            matched = [s for s in unique if wanted and wanted in s.get("label", "")]
            return matched[0]["url"] if len(matched) == 1 else None
        return None

    async def navigate_to_event(
        self, page: Page, event_url: str, session_preference: str | None = None
    ) -> bool:
        """進登記頁；已經停在該活動的登記頁上就不重新導航。

        帶進來的若是活動主頁網址，先轉成同一場活動的登記頁——主頁下不了單。

        沿用 CDP 借來的分頁時，再 goto 一次等於把人工通過的排隊、驗證與登入狀態
        敲掉重來——省這一次導航不是效能最佳化，是不可逆狀態的保全。
        """
        target = to_registration_url(event_url)
        reused = self._is_same_event_or_registration(self._current_url(page), target)
        if not reused:
            await page.goto(
                target,
                wait_until=NAVIGATION_WAIT_UNTIL,
                timeout=self.navigation_timeout_ms,
            )
        await self._guard_cloudflare(page, "navigate")

        # 多場次活動：母活動的登記頁沒有票種表單，真正的入口在各場次底下。
        # 這一步刻意留在預熱期做完——開賣瞬間才去找場次就來不及了。
        if await self.detect_page_kind(page) is not KKTIXPageKind.REGISTRATION:
            target = await self._enter_session_registration(
                page, event_url, session_preference
            ) or target

        self.telemetry.record(
            TimelineEventType.MARK, "navigated", url=target, reused=reused
        )
        return True

    async def _enter_session_registration(
        self, page: Page, event_url: str, session_preference: str | None
    ) -> str | None:
        """從母活動頁選出場次並進入它的登記頁；進不去回 None。"""
        await page.goto(
            event_url,
            wait_until=NAVIGATION_WAIT_UNTIL,
            timeout=self.navigation_timeout_ms,
        )
        # 場次清單是 Angular 後渲染的：`domcontentloaded` 當下 DOM 裡還沒有這些連結，
        # 不等就會把多場次活動誤判成單場次，然後停在買不了票的母活動頁上。
        try:
            await page.wait_for_selector(
                EVENT_SESSION_LINKS,
                timeout=self.navigation_timeout_ms,
                state="attached",
            )
        except Exception:
            pass

        sessions = await self.list_sessions(page)
        if not sessions:
            return None

        chosen = self.pick_session(sessions, session_preference)
        if chosen is None:
            self.telemetry.record(
                TimelineEventType.MARK,
                "session_choice_ambiguous",
                count=len(sessions),
                preference=session_preference or "",
            )
            return None

        await page.goto(
            chosen,
            wait_until=NAVIGATION_WAIT_UNTIL,
            timeout=self.navigation_timeout_ms,
        )
        await self._guard_cloudflare(page, "session")
        self.telemetry.record(
            TimelineEventType.MARK, "session_selected", url=chosen
        )
        return chosen

    # ------------------------------------------------------------- 1b. 登入

    async def navigate_to_login_from_guest_modal(self, page: Page) -> bool:
        """從「立刻成為 KKTIX 會員」彈窗跳到登入頁。"""
        link = await self._locate(
            page, KKTIXSelectors.MODAL_GUEST_SIGNIN_LINK, "guest_signin_link"
        )
        if link is None:
            self.telemetry.record(TimelineEventType.MARK, "guest_modal_link_missing")
            return False
        await ng_click(page, link, telemetry=self.telemetry)
        await self._settle(page)
        self.telemetry.record(TimelineEventType.MARK, "guest_modal_login_redirect")
        return True

    async def login(self, page: Page, username: str, secret_token: str) -> bool:
        """以呼叫端傳入的憑證登入，回報是否已離開登入頁。

        憑證只經 `ng_fill` 進 DOM：**絕不**進 log、timeline、例外訊息或截圖檔名。
        任何失敗路徑都先清空密碼欄位再返回：留在 DOM 上的密碼會被下一張截圖拍走。
        """
        await self._guard_cloudflare(page, "login")
        user_field = await self._locate(
            page, KKTIXSelectors.LOGIN_USER_INPUT, "login_user_input"
        )
        key_field = await self._locate(
            page, KKTIXSelectors.LOGIN_KEY_FIELD, "login_key_field"
        )
        if user_field is None or key_field is None:
            self.telemetry.record(
                TimelineEventType.MARK,
                "login_form_incomplete",
                has_user_field=user_field is not None,
                has_key_field=key_field is not None,
            )
            return False

        await ng_fill(page, user_field, username)
        await ng_fill(page, key_field, secret_token)

        submit = await self._locate(
            page, KKTIXSelectors.LOGIN_SUBMIT_BTN, "login_submit_btn"
        )
        if submit is None:
            await self._clear_key_field(key_field)
            self.telemetry.record(TimelineEventType.MARK, "login_submit_missing")
            return False

        await ng_click(page, submit, telemetry=self.telemetry)
        await self._settle(page)
        kind = await self.detect_page_kind(page)
        ok = kind is not KKTIXPageKind.LOGIN
        if not ok:
            await self._clear_key_field(key_field)
        self.telemetry.record(
            TimelineEventType.MARK, "login_result", ok=ok, page_kind=kind.value
        )
        return ok

    @staticmethod
    async def _clear_key_field(field: Locator) -> None:
        try:
            await field.fill("")
        except Exception:
            # 欄位已隨導頁消失就算清完了；這裡不得把例外往上拋，
            # 否則 traceback 反而會把登入現場的上下文寫進錯誤輸出。
            return

    async def _settle(self, page: Page) -> None:
        try:
            await page.wait_for_load_state(
                NAVIGATION_WAIT_UNTIL, timeout=self.navigation_timeout_ms
            )
        except Exception as exc:
            self.telemetry.record(
                TimelineEventType.MARK, "settle_skipped", reason=type(exc).__name__
            )

    async def detect_sale_opened(self, page: Page, timeout_ms: int) -> bool:
        await self._guard_cloudflare(page, "detect_sale")
        app = await first_visible(
            page,
            KKTIXSelectors.REGISTRATION_APP,
            timeout_ms=timeout_ms,
            telemetry=self.telemetry,
            field="registration_app",
        )
        if app is None:
            return False
        units = await self._collect_ticket_units(page)
        opened = len(units) > 0
        self.telemetry.record(
            TimelineEventType.MARK, "sale_opened_probe", opened=opened, units=len(units)
        )
        return opened

    # -------------------------------------------------------------- 2. 票種選取

    async def _collect_ticket_units(self, page: Page) -> list[Locator]:
        from adapters.ticketing.kktix.dom import (
            SELECTOR_FALLBACK_MARK,
            candidate_selectors,
        )

        candidates = candidate_selectors(KKTIXSelectors.TICKET_UNIT)
        for rank, selector in enumerate(candidates, start=1):
            locator = page.locator(selector)
            try:
                count = await locator.count()
            except Exception:
                continue
            if count == 0:
                continue
            if rank > 1:
                self.telemetry.record(
                    TimelineEventType.MARK,
                    SELECTOR_FALLBACK_MARK,
                    field="ticket_unit",
                    selector=selector,
                    rank=rank,
                    total=len(candidates),
                )
            return list(await locator.all())
        return []

    async def _is_rendered_registration(self, page: Page) -> bool:
        """真的是「可以下單的登記頁」嗎。

        光看容器存在不夠：Angular 還沒編譯完時原始 mustache 仍在 HTML 裡，
        多場次活動的母頁更是連容器都沒有卻仍可能被誤判。把「還沒渲染完」
        當成就緒，會讓開賣前的閘門直接放行，然後停在一張買不了票的頁上。
        """
        if not await self._has(page, KKTIXSelectors.REGISTRATION_APP):
            return False
        try:
            html = str(await page.content())
        except Exception:
            # 讀不到內容時不要擅自升級判定；交給下一輪重探。
            return False
        return not any(
            marker in html
            for marker in REGISTRATION_UNRENDERED_MARKERS
        )

    async def detect_page_kind(self, page: Page) -> KKTIXPageKind:
        """判斷目前頁面種類。順序不可調換：登記頁同樣有活動標題。"""
        if await self._is_rendered_registration(page):
            return KKTIXPageKind.REGISTRATION
        if await self._has(page, KKTIXSelectors.LOGIN_KEY_FIELD) or await self._has(
            page, KKTIXSelectors.LOGIN_FORM
        ):
            return KKTIXPageKind.LOGIN
        if await self._has(
            page, KKTIXSelectors.EVENT_TICKET_TABLE_ROWS
        ) or await self._has(page, KKTIXSelectors.EVENT_TITLE):
            return KKTIXPageKind.EVENT
        if await self._has(page, KKTIXSelectors.CONTACT_NAME) or await self._has(
            page, KKTIXSelectors.RESELECT_TICKET_LINK
        ):
            return KKTIXPageKind.ORDER
        # 走到這裡代表三種已知頁面都不是；把它和 ORDER 混為一談會讓
        # 「被踢回登入頁」在研究資料裡看起來像「正常停在訂單頁」。
        return KKTIXPageKind.UNKNOWN

    async def read_selected_quantity(self, page: Page) -> int:
        """登記頁上目前已選的總張數。

        選票頁與劃位頁在 DOM 上是同一個 AngularJS app，唯一的物理差別就是這個
        總數。讀不到或非數字的欄位一律以 0 計，不臆造選取狀態。
        """
        total = 0
        for unit in await self._collect_ticket_units(page):
            quantity_input = await self._first_present(
                unit, KKTIXSelectors.TICKET_QUANTITY_INPUT
            )
            if quantity_input is None:
                continue
            raw = (await read_input_value(quantity_input)).strip()
            if raw.isdigit():
                try:
                    total += int(raw)
                except ValueError:
                    pass
        return total

    async def detect_page_state(self, page: Page) -> PageState:
        """依優先序將目前頁面歸納成一個 `PageState`；**不拋例外、不等待**。

        順序嚴禁調換，每一條都是举證責任在頁面上的物理特徵：
        彈窗盖住底下的任何狀態；排隊室與登記頁互斥；`/orders/` 要先排除 `/payment`
        才算完成；表單頁優先於局部驗證題（問答題就长在那張表單裡）；
        最後才以「已選數量」區分劃位與選票。
        """
        url = str(getattr(page, "url", "") or "")

        modal_text = await self._visible_text(page, KKTIXSelectors.MODAL_CONTAINER)
        if modal_text:
            if any(t in modal_text for t in KKTIXSelectors.MODAL_FAILURE_TEXTS):
                return PageState.FAILURE_MODAL
            if KKTIXSelectors.MODAL_GUEST_SIGNIN_TEXT in modal_text:
                return PageState.GUEST_MODAL

        if await self._has(page, KKTIXSelectors.QUEUE_COUNTDOWN) or await self._has(
            page, KKTIXSelectors.QUEUE_HEADING
        ):
            return PageState.QUEUE

        on_order_url = ORDER_URL_MARKER in url and PAYMENT_URL_MARKER not in url
        if on_order_url or await self._has(
            page, KKTIXSelectors.ORDER_COMPLETE_CONTAINER
        ):
            return PageState.COMPLETED

        if PAYMENT_URL_MARKER in url or await self._has(
            page, KKTIXSelectors.PAYMENT_RADIO_CREDIT_CARD
        ):
            return PageState.PAYMENT_REQUIRED

        if await self._has(
            page, KKTIXSelectors.MEMBER_CODE_BLOCK
        ) and await self._has_visible(page, KKTIXSelectors.MEMBER_CODE_INPUT):
            return PageState.QUALIFICATION_CODE

        for selectors in (
            KKTIXSelectors.ORDER_COUNTDOWN_NOTICE,
            KKTIXSelectors.CONTACT_DYNAMIC_GROUP,
            KKTIXSelectors.CONTACT_NAME,
            KKTIXSelectors.BTN_CONFIRM_ORDER,
        ):
            if await self._has(page, selectors):
                return PageState.FORM_FILLING

        if await self._has(page, KKTIXSelectors.CAPTCHA_CONTAINER) and await self._has(
            page, KKTIXSelectors.CAPTCHA_INPUT
        ):
            return PageState.VERIFICATION_CHALLENGE

        if await self._has(page, KKTIXSelectors.REGISTRATION_APP):
            selected = await self.read_selected_quantity(page)
            if selected == 0:
                return PageState.TICKET_SELECTION
            for selectors in (
                KKTIXSelectors.BTN_BEST_AVAILABLE,
                KKTIXSelectors.BTN_PICK_SEAT,
                KKTIXSelectors.BTN_NEXT_STEP,
            ):
                if await self._has(page, selectors):
                    return PageState.SEAT_SELECTION

        return PageState.UNKNOWN

    async def dismiss_failure_modal(self, page: Page) -> bool:
        """關掉「別人搶先一步」彈窗，並把彈窗文字原樣記進 timeline。

        彈窗文字是「這一輪為什麼搶輸」的唯一直接証據（座位被抽走與票券售完在
        降級策略上完全不同），不記下來就只剩一個無法分析的「失敗」。
        """
        text = " ".join(
            (await self._visible_text(page, KKTIXSelectors.MODAL_CONTAINER)).split()
        )
        button = await self._first_present(page, KKTIXSelectors.MODAL_DISMISS_BTN)
        if button is None:
            self.telemetry.record(
                TimelineEventType.MARK, FAILURE_MODAL_MISSING_MARK, text=text[:200]
            )
            return False
        await ng_click(page, button, telemetry=self.telemetry)
        self.telemetry.record(
            TimelineEventType.MARK, FAILURE_MODAL_MARK, text=text[:200]
        )
        return True

    async def probe_page(self, page: Page, url: str | None = None) -> KKTIXPageKind:
        """判斷目前狀態，**不拋例外**——包含命中人機驗證挑戰的情況。

        給「等人就緒」的閘門用：那裡需要知道「還沒好，是哪一種還沒好」，
        而不是直接中止。給了 `url` 才會導航；不給就只重新判讀目前這一頁，
        避免反覆輪詢對方站台。給了 `url` 但已經停在那一頁上時也不導航：
        就地判讀與重整後判讀讀到的是同一件事，重整卻會敲掉現場狀態。
        """
        target = None if url is None else to_registration_url(url)
        if target is not None and not self._is_same_event_or_registration(
            self._current_url(page), target
        ):
            await page.goto(
                target,
                wait_until=NAVIGATION_WAIT_UNTIL,
                timeout=self.navigation_timeout_ms,
            )
        marker = contains_cloudflare_challenge(await page_text(page))
        if marker is not None:
            self.telemetry.record(
                TimelineEventType.MARK, CLOUDFLARE_MARK, marker=marker, stage="probe"
            )
            return KKTIXPageKind.CHALLENGE
        return await self.detect_page_kind(page)

    async def read_ticket_options(self, page: Page) -> list[TicketOption]:
        """把目前頁面的票種讀成不可變快照，依頁面種類選用對應的選擇器。"""
        if await self.detect_page_kind(page) is KKTIXPageKind.EVENT:
            return await self.read_event_page_tickets(page)
        return await self.read_registration_tickets(page)

    async def read_event_page_tickets(self, page: Page) -> list[TicketOption]:
        """讀活動主頁的票種表格。

        主頁**看不到剩餘數量**，狀態欄位放的是售賣時段而不是庫存；因此除非頁面
        明寫售完字樣，一律 `available=True`、`remaining=None`——不臆造沒讀到的資訊。
        表頭列沒有票名，直接略過。
        """
        rows = page.locator(KKTIXSelectors.EVENT_TICKET_TABLE_ROWS)
        options: list[TicketOption] = []
        for row in await rows.all():
            name = await self._text_of(row, KKTIXSelectors.EVENT_TICKET_ROW_NAME)
            price_text = await self._text_of(row, KKTIXSelectors.EVENT_TICKET_ROW_PRICE)
            if not name or not price_text:
                continue
            status_text = await self._text_of(
                row, KKTIXSelectors.EVENT_TICKET_ROW_STATUS
            )
            remaining_match = REMAINING_RE.search(status_text)
            remaining: int | None = None
            if remaining_match:
                try:
                    remaining = int(remaining_match.group(1))
                except ValueError:
                    remaining = None
            options.append(
                TicketOption(
                    index=len(options),
                    name=name,
                    price=self._parse_price(price_text),
                    available=not self._is_sold_out(f"{status_text} {name}"),
                    remaining=remaining,
                    status_text=status_text,
                )
            )
        return options

    @staticmethod
    def _parse_price(price_text: str) -> int:
        digits = DIGITS_RE.findall(price_text.replace(",", ""))
        if not digits:
            return 0
        try:
            return int(digits[0])
        except ValueError:
            return 0

    @staticmethod
    def _is_sold_out(text: str) -> bool:
        lowered = text.lower()
        return any(marker.lower() in lowered for marker in SOLD_OUT_MARKERS)

    async def read_registration_tickets(self, page: Page) -> list[TicketOption]:
        options: list[TicketOption] = []
        for index, unit in enumerate(await self._collect_ticket_units(page)):
            name = await self._text_of(unit, KKTIXSelectors.TICKET_NAME)
            price_text = await self._text_of(unit, KKTIXSelectors.TICKET_PRICE)
            status_text = await self._text_of(
                unit, KKTIXSelectors.EVENT_TICKET_ROW_STATUS
            )
            price = self._parse_price(price_text)
            sold_out = self._is_sold_out(f"{status_text} {name}")
            has_plus = await self._has(unit, KKTIXSelectors.TICKET_PLUS_BTN)
            remaining_match = REMAINING_RE.search(status_text)
            remaining: int | None = None
            if remaining_match:
                try:
                    remaining = int(remaining_match.group(1))
                except ValueError:
                    remaining = None
            options.append(
                TicketOption(
                    index=index,
                    name=name,
                    price=price,
                    available=has_plus and not sold_out,
                    remaining=remaining,
                    status_text=status_text,
                )
            )
        return options

    async def select_tickets(
        self, page: Page, preference: TicketPreference
    ) -> tuple[bool, str]:
        await self._guard_cloudflare(page, "select_tickets")
        kind = await self.detect_page_kind(page)
        if kind is not KKTIXPageKind.REGISTRATION:
            # 站錯頁（多半是還在活動主頁、或被導去登入頁）。**不得**回報售罄：
            # 那會把「沒進到登記頁」寫成「票賣完了」，直接汙染研究結論。
            self.telemetry.record(
                TimelineEventType.MARK, "wrong_page_for_selection", page_kind=kind.value
            )
            return False, REASON_NOT_REGISTRATION_PAGE

        units = await self._collect_ticket_units(page)
        if not units:
            return False, REASON_NO_TICKET_UNITS

        options = await self.read_registration_tickets(page)
        return await self.apply_ticket_decision(
            page, decide_ticket(options, preference)
        )

    async def apply_ticket_decision(
        self, page: Page, decision: TicketDecision
    ) -> tuple[bool, str]:
        """把**已經作出的**票種決策套用到頁面上（加号 + 條款）。

        與 `select_tickets` 分離是故意的：搶票迴圈得以同一份排除清單「決策一次、
        套用一次」，若在這裡重新決策，降級鍵路就會拿到第二份不同的決策而反覆
        選到已經搶輸的同一張票。
        """
        self.last_ticket_decision = decision
        self.telemetry.record(
            TimelineEventType.MARK,
            "ticket_decision",
            status=decision.status,
            fallback_used=decision.fallback_used,
            quantity=decision.quantity,
            trace=decision.trace,
        )
        if decision.status != "SELECTED" or decision.option is None:
            return False, REASON_SOLD_OUT

        units = await self._collect_ticket_units(page)
        if not units:
            return False, REASON_NO_TICKET_UNITS
        if decision.option.index >= len(units):
            # 決策到套用之間頁面重渲了：寧可重試，也不能拿舊索引去點到別人的票種。
            self.telemetry.record(
                TimelineEventType.MARK,
                "ticket_unit_index_out_of_range",
                index=decision.option.index,
                units=len(units),
            )
            return False, REASON_QUANTITY_MISMATCH

        unit = units[decision.option.index]
        plus = await self._locate(
            unit, KKTIXSelectors.TICKET_PLUS_BTN, "ticket_plus_btn"
        )
        if plus is None:
            return False, REASON_PLUS_BUTTON_MISSING
        for _ in range(decision.quantity):
            await ng_click(page, plus, telemetry=self.telemetry)

        quantity_input = await self._locate(
            unit, KKTIXSelectors.TICKET_QUANTITY_INPUT, "ticket_quantity_input"
        )
        if quantity_input is None:
            return False, REASON_QUANTITY_MISMATCH
        # AngularJS 的 model 可能沒跟上 DOM：回讀驗證，不一致即失敗而非靜默送出 0 張。
        actual = (await read_input_value(quantity_input)).strip()
        if actual != str(decision.quantity):
            self.telemetry.record(
                TimelineEventType.MARK,
                "quantity_readback_mismatch",
                expected=decision.quantity,
                actual=actual,
            )
            return False, REASON_QUANTITY_MISMATCH

        terms = await self._locate(
            page, KKTIXSelectors.TERMS_CHECKBOX, "terms_checkbox"
        )
        if terms is None:
            return False, REASON_TERMS_NOT_ACCEPTED
        # 已勾就不要再點：降級重選時再點一次等於把合法的同意狀態切回未勾。
        if not await terms.is_checked():
            await ng_click(page, terms, telemetry=self.telemetry)

        return True, REASON_SELECTED

    async def reset_ticket_quantities(self, page: Page) -> bool:
        """把登記頁上所有票種數量歸零，逐一回讀確認真的是 "0"。

        失敗彈窗之後必須換票種重選，而残留的數量會連同新票種一起送出。
        任一欄位回讀不到 "0" 即回報 False 讓呼叫端 fail-closed：
        帶着残留數量選票比完全不選更糟。
        """
        units = await self._collect_ticket_units(page)
        if not units:
            self.telemetry.record(
                TimelineEventType.MARK,
                RESET_MARK,
                ok=False,
                reason=REASON_NO_TICKET_UNITS,
            )
            return False

        verified = 0
        for index, unit in enumerate(units):
            quantity_input = await self._first_present(
                unit, KKTIXSelectors.TICKET_QUANTITY_INPUT
            )
            if quantity_input is None:
                # 售完的票種不長數量欄位，本來就沒有東西要歸零。
                continue
            raw = (await read_input_value(quantity_input)).strip() or ZERO_QUANTITY
            if not raw.isdigit():
                self.telemetry.record(
                    TimelineEventType.MARK,
                    RESET_MARK,
                    ok=False,
                    reason=RESET_REASON_UNREADABLE,
                    unit=index,
                )
                return False
            try:
                current = int(raw)
            except ValueError:
                return False
            if current > 0:
                minus = await self._first_present(unit, KKTIXSelectors.TICKET_MINUS_BTN)
                if minus is None:
                    self.telemetry.record(
                        TimelineEventType.MARK,
                        RESET_MARK,
                        ok=False,
                        reason=RESET_REASON_MINUS_MISSING,
                        unit=index,
                    )
                    return False
                for _ in range(current):
                    await ng_click(page, minus, telemetry=self.telemetry)
            actual = (await read_input_value(quantity_input)).strip() or ZERO_QUANTITY
            if actual != ZERO_QUANTITY:
                self.telemetry.record(
                    TimelineEventType.MARK,
                    RESET_MARK,
                    ok=False,
                    reason=RESET_REASON_NOT_ZERO,
                    unit=index,
                    actual=actual,
                )
                return False
            verified += 1

        if verified == 0:
            # 有票種協但一個數量欄位也沒有：頁面形狀變了，不該臆測「歸零成功」。
            self.telemetry.record(
                TimelineEventType.MARK,
                RESET_MARK,
                ok=False,
                reason=RESET_REASON_NO_QUANTITY_FIELD,
                units=len(units),
            )
            return False
        self.telemetry.record(
            TimelineEventType.MARK, RESET_MARK, ok=True, units=verified
        )
        return True

    # -------------------------------------------------------------- 3. 座位處理

    async def handle_seat_selection(
        self, page: Page, preference: SeatPreference
    ) -> bool:
        await self._guard_cloudflare(page, "seat_selection")
        seat = decide_seat_action(preference)
        if seat.downgraded:
            self.telemetry.record(
                TimelineEventType.MARK, DOWNGRADE_MARK, reason=seat.reason
            )
        # 票種選擇頁有兩種形狀：劃位活動同時給「電腦配位／自行選位」，不劃位
        # 活動只給一顆「下一步」。以「電腦配位是否存在」區分頁型，而不是盲目 fallback：
        # 否則不劃位頁會把唯一那顆按鈕當成自行選位，劃位頁又可能誤點進尚未
        # 實作的座位圖，而 timeline 還報成電腦配位成功。
        # 用短預算：「電腦配位不存在」本身就是辨識不劃位頁的依據，不存在是常態而
        # 不是意外。拿元素預算去等它，每一場不劃位活動都要在這裡白燒一整份。
        auto_assign = await first_visible(
            page,
            KKTIXSelectors.BTN_BEST_AVAILABLE,
            timeout_ms=self.probe_timeout_ms,
            telemetry=self.telemetry,
            field="seat_best_available",
        )
        if auto_assign is None:
            button = await self._locate(page, KKTIXSelectors.BTN_NEXT_STEP, "next_step")
            effective, page_shape = "NEXT_STEP", "single_next_step"
        elif seat.action is SeatAction.PICK_SEAT:
            button = await self._locate(
                page, KKTIXSelectors.BTN_PICK_SEAT, "seat_pick_seat"
            )
            effective, page_shape = SeatAction.PICK_SEAT.value, "reserved_seating"
            if button is None:
                button = auto_assign
                effective = SeatAction.BEST_AVAILABLE.value
        else:
            button = auto_assign
            effective, page_shape = SeatAction.BEST_AVAILABLE.value, "reserved_seating"

        if button is None:
            return False
        await ng_click(page, button, telemetry=self.telemetry)
        self.telemetry.record(
            TimelineEventType.MARK,
            "seat_action",
            action=effective,
            requested=seat.action.value,
            page_shape=page_shape,
        )
        return True

    # -------------------------------------------------------------- 4. 表單填寫

    async def fill_contact_form(self, page: Page, profile: UserContactProfile) -> bool:
        await self._guard_cloudflare(page, "contact_form")
        # 先試動態表單：它只需一次 DOM 查詢且不等待，而静態候選全數落空時
        # 每個欄位都要燒掉一整份 timeout——開賣瞬間沒有這麼多秒可以浪。
        dynamic = await self._resolve_dynamic_contact_fields(page)
        for field_name, selectors, value in (
            ("contact_name", KKTIXSelectors.CONTACT_NAME, profile.name),
            ("contact_email", KKTIXSelectors.CONTACT_EMAIL, profile.email),
            ("contact_phone", KKTIXSelectors.CONTACT_PHONE, profile.phone),
        ):
            locator = dynamic.get(field_name)
            if locator is None:
                locator = await self._locate(page, selectors, field_name)
            if locator is None:
                self.telemetry.record(
                    TimelineEventType.MARK, "contact_field_missing", field=field_name
                )
                return False
            existing = (await read_input_value(locator)).strip()
            if existing:
                # KKTIX 會拿登入帳號預填真實聯絡資料。覆寫等於拿任務檔裡的
                # 佔位資料送出真實訂單，寧可保留頁面上的值並誘實記一筆。
                self.telemetry.record(
                    TimelineEventType.MARK,
                    "contact_field_prefilled",
                    field=field_name,
                    matches_profile=existing == value,
                )
                continue
            await ng_fill(page, locator, value)

        await self._accept_dynamic_consents(page)
        await self._answer_dynamic_radios(page)
        return await self._fill_attendees(page)

    async def _accept_dynamic_consents(self, page: Page) -> None:
        """勾選訂單頁動態產生的同意條款 checkbox。

        沒勾就送不出去，而欄位 id 每場活動都不同。勾了什麼一律連同條款文字
        記進 timeline：這是代替使用者按下的同意，必須可事後追查。
        """
        accepted: list[str] = []
        seen: set[str] = set()

        for label in await page.locator(
            KKTIXSelectors.CONTACT_DYNAMIC_CONSENT_LABEL
        ).all():
            box = label.locator(KKTIXSelectors.CONTACT_DYNAMIC_CHECKBOX).first
            if await box.count() == 0:
                continue
            seen.add(str(await box.get_attribute("name") or ""))
            if await box.is_checked():
                continue
            await ng_click(page, box, telemetry=self.telemetry)
            terms = " ".join((await label.inner_text()).split())
            accepted.append(terms[:120])

        # 沒被 label 包起來的同意欄位也得勾——漏一個就是整單送不出去。
        for box in await page.locator(KKTIXSelectors.CONTACT_DYNAMIC_CHECKBOX).all():
            name = str(await box.get_attribute("name") or "")
            if name in seen or await box.is_checked():
                continue
            await ng_click(page, box, telemetry=self.telemetry)
            accepted.append(name)

        if accepted:
            self.telemetry.record(
                TimelineEventType.MARK,
                "contact_consent_accepted",
                count=len(accepted),
                terms=tuple(accepted),
            )

    async def _answer_dynamic_radios(self, page: Page) -> None:
        """處理主辦自訂的 radio 欄位。

        **只動單一選項的群組**——那是「必須確認才能送出」的項目（KKTIX 用 radio
        而不是 checkbox 表達），語意與同意條款相同，一律連同題幹記進 timeline。

        多選項的群組是真正的選擇題，替使用者亂點可能買錯票或答錯資格問題，
        因此只記錄待處理，交由既有的驗證規則或真人處理，**絕不**自行挑一個。
        """
        groups: dict[str, list[Locator]] = {}
        for radio in await page.locator(DYNAMIC_RADIO).all():
            name = str(await radio.get_attribute("name") or "")
            if not name:
                continue
            groups.setdefault(name, []).append(radio)

        confirmed: list[str] = []
        unanswered: list[str] = []
        for name, radios in groups.items():
            if len(radios) > 1:
                if not any([await r.is_checked() for r in radios]):
                    unanswered.append(name)
                continue

            radio = radios[0]
            if await radio.is_checked():
                continue
            await ng_click(page, radio, telemetry=self.telemetry)
            confirmed.append(f"{name}: {await self._radio_question(page, radio)}")

        if confirmed:
            self.telemetry.record(
                TimelineEventType.MARK,
                "form_radio_confirmed",
                count=len(confirmed),
                items=tuple(confirmed),
            )
        if unanswered:
            # 沒答的選擇題會讓送出被擋下；把它說清楚，不要變成無聲的逾時。
            self.telemetry.record(
                TimelineEventType.MARK,
                "form_radio_unanswered",
                count=len(unanswered),
                fields=tuple(unanswered),
            )

    async def _radio_question(self, page: Page, radio: Locator) -> str:
        """取出該 radio 的題幹文字，供事後追查我們替使用者確認了什麼。"""
        del page
        try:
            container = radio.locator(
                "xpath=ancestor::*[self::div or self::li][1]"
            ).first
            if await container.count():
                return " ".join((await container.inner_text()).split())[:160]
        except Exception:
            pass
        return ""

    async def _resolve_dynamic_contact_fields(self, page: Page) -> dict[str, Locator]:
        """把動態命名的聯絡人欄位依 label 文字歸位。

        欄位 name 帶每場活動不同的數字 ID、三個欄位又共用同一個 ng-model，
        所以只能靠 label。分類寫在這裡而不是塞進 selector，離線測試才驗得到分類邏輯。
        """
        # 點完配位會導頁到訂單頁，而 AngularJS 的聯絡人表單是非同步渲染的。
        # 不等它出現就列舉 group 只會拿到空集合，接著静態選擇器再各燒一份
        # timeout 後失敗——失敗原因還會被記成「欄位不存在」而不是「還沒渲染」。
        appeared = await first_visible(
            page,
            KKTIXSelectors.CONTACT_DYNAMIC_INPUT,
            timeout_ms=self.navigation_timeout_ms,
            telemetry=self.telemetry,
            field="contact_dynamic_input",
        )
        if appeared is None:
            self.telemetry.record(
                TimelineEventType.MARK,
                "contact_form_not_rendered",
                waited_ms=self.navigation_timeout_ms,
            )
            return {}

        resolved: dict[str, Locator] = {}
        for group in await page.locator(KKTIXSelectors.CONTACT_DYNAMIC_GROUP).all():
            label = group.locator(KKTIXSelectors.CONTACT_DYNAMIC_LABEL).first
            if await label.count() == 0:
                continue
            text = (await label.inner_text()).strip().lower()
            field = next(
                (
                    name
                    for name, words in KKTIXSelectors.CONTACT_LABEL_KEYWORDS
                    if any(word in text for word in words)
                ),
                None,
            )
            if field is None or field in resolved:
                continue
            box = group.locator(KKTIXSelectors.CONTACT_DYNAMIC_INPUT).first
            if await box.count() == 0:
                continue
            resolved[field] = box
        if resolved:
            self.telemetry.record(
                TimelineEventType.MARK,
                "contact_fields_resolved_dynamically",
                fields=sorted(resolved),
            )
        return resolved

    async def submit_order(self, page: Page) -> bool:
        """送出訂單表單（確認表單資料）。

        刻意與 `fill_contact_form` 分離：KKTIX 的防機器人問答題與聯絡人欄位同屬一張
        表單，狀態機卻要求「填表 -> 驗證 -> 付款」的順序，填寫與送出必須是兩個動作，
        否則驗證答案永遠來不及進入送出的那一次請求。
        """
        button = await self._locate(
            page, KKTIXSelectors.BTN_CONFIRM_ORDER, "confirm_order"
        )
        if button is None:
            return False
        await ng_click(page, button, telemetry=self.telemetry)
        self.telemetry.record(TimelineEventType.MARK, "order_submitted")
        return True

    async def _fill_attendees(self, page: Page) -> bool:
        required = 0
        while True:
            selector = KKTIXSelectors.ATTENDEE_NAME_TEMPLATE.format(index=required)
            if await page.locator(selector).count() == 0:
                break
            required += 1
        if required == 0:
            return True
        if required > len(self.attendees):
            # R7：頁面要幾位就是幾位，資料不足即誠實失敗，不猜測、不重複填。
            self.telemetry.record(
                TimelineEventType.MARK,
                "attendee_profile_insufficient",
                required=required,
                provided=len(self.attendees),
            )
            return False

        for index in range(required):
            attendee = self.attendees[index]
            name_locator = page.locator(
                KKTIXSelectors.ATTENDEE_NAME_TEMPLATE.format(index=index)
            ).first
            await ng_fill(page, name_locator, attendee.name)
            phone_selector = KKTIXSelectors.ATTENDEE_PHONE_TEMPLATE.format(index=index)
            if await page.locator(phone_selector).count() > 0:
                await ng_fill(page, page.locator(phone_selector).first, attendee.phone)
            if attendee.id_number is None:
                continue
            for template in KKTIXSelectors.ATTENDEE_ID_TEMPLATE:
                id_selector = template.format(index=index)
                if await page.locator(id_selector).count() > 0:
                    await ng_fill(
                        page, page.locator(id_selector).first, attendee.id_number
                    )
                    break
        self.telemetry.record(
            TimelineEventType.MARK, "attendees_filled", count=required
        )
        return True

    # ---------------------------------------------------------------- 5. 驗證

    async def detect_verification(self, page: Page) -> bool:
        """只偵測是否存在驗證題，不作答。

        協調器必須在送出 `form_submitted` **之前**知道答案，因為該事件的目標狀態
        取決於 `requires_verification`；把偵測與作答合併會讓 FSM 分流無從決定。
        """
        await self._guard_cloudflare(page, "verification_probe")
        # 用短預算：驗證題跟聯絡人欄位同屬一張表單，而 fill_contact_form 已經等過
        # 表單渲染。到這裡 DOM 早就在了，驗證題存在就一定查得到；再等 5 秒
        # 只是在為「沒有驗證題」這个常態付費。
        container = await first_visible(
            page,
            KKTIXSelectors.CAPTCHA_CONTAINER,
            timeout_ms=self.probe_timeout_ms,
            telemetry=self.telemetry,
            field="captcha_container",
        )
        present = container is not None
        self.telemetry.record(
            TimelineEventType.MARK, "verification_probe", present=present
        )
        return present

    async def handle_verification(self, page: Page) -> bool:
        await self._guard_cloudflare(page, "verification")
        container = await self._locate(
            page, KKTIXSelectors.CAPTCHA_CONTAINER, "captcha_container"
        )
        if container is None:
            # 沒有驗證題是正常情況，不得誤報成失敗。
            self.telemetry.record(TimelineEventType.MARK, "verification_absent")
            return True
        if self.verification is None:
            self.telemetry.record(
                TimelineEventType.MARK, "verification_provider_missing"
            )
            return False

        question = await self._text_of(page, KKTIXSelectors.CAPTCHA_QUESTION_TEXT)
        result = await self.verification.solve(
            VerificationChallenge(kind=ChallengeKind.TEXT_QUIZ, question=question)
        )
        self.telemetry.record(
            TimelineEventType.MARK,
            "verification_result",
            provider=result.provider,
            solved=result.solved,
            detail=dict(result.detail),
        )
        if not result.solved or result.answer is None:
            return False

        answer_input = await self._locate(
            page, KKTIXSelectors.CAPTCHA_INPUT, "captcha_input"
        )
        if answer_input is None:
            return False
        await ng_fill(page, answer_input, result.answer)
        return True

    # ---------------------------------------------------------------- 6. 付款

    async def execute_payment(
        self, page: Page, payment_profile: CreditCardProfile | None
    ) -> PaymentResult:
        await self._guard_cloudflare(page, "payment")
        result = await self.payment.pay(page, payment_profile)
        self.last_payment_result = result
        self.telemetry.record(
            TimelineEventType.MARK,
            "payment_result",
            provider=result.provider,
            outcome=result.outcome.value,
            detail=dict(result.detail),
        )
        if result.outcome is PaymentOutcome.SUBMITTED:
            self.telemetry.record(
                TimelineEventType.MARK,
                "real_payment_submitted",
                provider=result.provider,
            )
        return result
