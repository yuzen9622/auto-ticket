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

from adapters.payment.base import PaymentOutcome, PaymentProvider, PaymentResult
from adapters.ticketing.base import TicketingAdapter
from adapters.ticketing.kktix.dom import (
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

ALL_TICKET_REASONS = frozenset({
    REASON_SELECTED,
    REASON_SOLD_OUT,
    REASON_NO_TICKET_UNITS,
    REASON_PLUS_BUTTON_MISSING,
    REASON_QUANTITY_MISMATCH,
    REASON_TERMS_NOT_ACCEPTED,
    REASON_NOT_REGISTRATION_PAGE,
})

CLOUDFLARE_MARK = "cloudflare_challenge"
# KKTIX 頁面帶著分析／廣告等長尾資源，`load` 事件常常遲遲不觸發。
# 搶票要的是「DOM 可以操作了」，不是「所有資源都下載完了」——等 load 只會白等。
NAVIGATION_WAIT_UNTIL = "domcontentloaded"
DEFAULT_NAVIGATION_TIMEOUT_MS = 30000
SOLD_OUT_MARKERS = ("售完", "售罄", "完售", "sold out", "已結束", "已額滿")
REMAINING_RE = re.compile(r"(?:剩餘|剩下|remaining)\D{0,4}(\d+)", re.IGNORECASE)
DIGITS_RE = re.compile(r"\d+")


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
        navigation_timeout_ms: int = DEFAULT_NAVIGATION_TIMEOUT_MS,
        screenshot: Callable[[str], Awaitable[Any]] | None = None,
    ) -> None:
        self.telemetry = telemetry
        self.payment = payment
        self.verification = verification
        self.attendees = tuple(attendees)
        self.timeout_ms = timeout_ms
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

    # ------------------------------------------------------- 1. 導航與開賣偵測

    async def navigate_to_event(self, page: Page, event_url: str) -> bool:
        await page.goto(
            event_url,
            wait_until=NAVIGATION_WAIT_UNTIL,
            timeout=self.navigation_timeout_ms,
        )
        await self._guard_cloudflare(page, "navigate")
        self.telemetry.record(
            TimelineEventType.MARK, "navigated", url=event_url
        )
        return True

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

    async def detect_page_kind(self, page: Page) -> KKTIXPageKind:
        """判斷目前頁面種類。順序不可調換：登記頁同樣有活動標題。"""
        if await self._has(page, KKTIXSelectors.REGISTRATION_APP):
            return KKTIXPageKind.REGISTRATION
        if await self._has(page, KKTIXSelectors.LOGIN_PASSWORD_INPUT) or await self._has(
            page, KKTIXSelectors.LOGIN_FORM
        ):
            return KKTIXPageKind.LOGIN
        if await self._has(page, KKTIXSelectors.EVENT_TICKET_TABLE_ROWS) or await self._has(
            page, KKTIXSelectors.EVENT_TITLE
        ):
            return KKTIXPageKind.EVENT
        if await self._has(page, KKTIXSelectors.CONTACT_NAME) or await self._has(
            page, KKTIXSelectors.RESELECT_TICKET_LINK
        ):
            return KKTIXPageKind.ORDER
        # 走到這裡代表三種已知頁面都不是；把它和 ORDER 混為一談會讓
        # 「被踢回登入頁」在研究資料裡看起來像「正常停在訂單頁」。
        return KKTIXPageKind.UNKNOWN

    async def probe_page(self, page: Page, url: str | None = None) -> KKTIXPageKind:
        """判斷目前狀態，**不拋例外**——包含命中人機驗證挑戰的情況。

        給「等人就緒」的閘門用：那裡需要知道「還沒好，是哪一種還沒好」，
        而不是直接中止。給了 `url` 才會導航；不給就只重新判讀目前這一頁，
        避免反覆輪詢對方站台。
        """
        if url is not None:
            await page.goto(
                url,
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
            status_text = await self._text_of(row, KKTIXSelectors.EVENT_TICKET_ROW_STATUS)
            remaining_match = REMAINING_RE.search(status_text)
            options.append(
                TicketOption(
                    index=len(options),
                    name=name,
                    price=self._parse_price(price_text),
                    available=not self._is_sold_out(f"{status_text} {name}"),
                    remaining=int(remaining_match.group(1)) if remaining_match else None,
                    status_text=status_text,
                )
            )
        return options

    @staticmethod
    def _parse_price(price_text: str) -> int:
        digits = DIGITS_RE.findall(price_text.replace(",", ""))
        return int(digits[0]) if digits else 0

    @staticmethod
    def _is_sold_out(text: str) -> bool:
        lowered = text.lower()
        return any(marker.lower() in lowered for marker in SOLD_OUT_MARKERS)

    async def read_registration_tickets(self, page: Page) -> list[TicketOption]:
        options: list[TicketOption] = []
        for index, unit in enumerate(await self._collect_ticket_units(page)):
            name = await self._text_of(unit, KKTIXSelectors.TICKET_NAME)
            price_text = await self._text_of(unit, KKTIXSelectors.TICKET_PRICE)
            status_text = await self._text_of(unit, KKTIXSelectors.EVENT_TICKET_ROW_STATUS)
            price = self._parse_price(price_text)
            sold_out = self._is_sold_out(f"{status_text} {name}")
            has_plus = await self._has(unit, KKTIXSelectors.TICKET_PLUS_BTN)
            remaining_match = REMAINING_RE.search(status_text)
            options.append(
                TicketOption(
                    index=index,
                    name=name,
                    price=price,
                    available=has_plus and not sold_out,
                    remaining=int(remaining_match.group(1)) if remaining_match else None,
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
        decision = decide_ticket(options, preference)
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

        unit = units[decision.option.index]
        plus = await self._locate(unit, KKTIXSelectors.TICKET_PLUS_BTN, "ticket_plus_btn")
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

        terms = await self._locate(page, KKTIXSelectors.TERMS_CHECKBOX, "terms_checkbox")
        if terms is None:
            return False, REASON_TERMS_NOT_ACCEPTED
        await ng_click(page, terms, telemetry=self.telemetry)

        return True, REASON_SELECTED

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
        selectors = (
            KKTIXSelectors.BTN_BEST_AVAILABLE
            if seat.action is SeatAction.BEST_AVAILABLE
            else KKTIXSelectors.BTN_PICK_SEAT
        )
        button = await self._locate(page, selectors, "seat_button")
        if button is None:
            button = await self._locate(page, KKTIXSelectors.BTN_NEXT_STEP, "next_step")
        if button is None:
            return False
        await ng_click(page, button, telemetry=self.telemetry)
        self.telemetry.record(
            TimelineEventType.MARK, "seat_action", action=seat.action.value
        )
        return True

    # -------------------------------------------------------------- 4. 表單填寫

    async def fill_contact_form(
        self, page: Page, profile: UserContactProfile
    ) -> bool:
        await self._guard_cloudflare(page, "contact_form")
        for field_name, selectors, value in (
            ("contact_name", KKTIXSelectors.CONTACT_NAME, profile.name),
            ("contact_email", KKTIXSelectors.CONTACT_EMAIL, profile.email),
            ("contact_phone", KKTIXSelectors.CONTACT_PHONE, profile.phone),
        ):
            locator = await self._locate(page, selectors, field_name)
            if locator is None:
                self.telemetry.record(
                    TimelineEventType.MARK, "contact_field_missing", field=field_name
                )
                return False
            await ng_fill(page, locator, value)

        return await self._fill_attendees(page)

    async def submit_order(self, page: Page) -> bool:
        """送出訂單表單（確認表單資料）。

        刻意與 `fill_contact_form` 分離：KKTIX 的防機器人問答題與聯絡人欄位同屬一張
        表單，狀態機卻要求「填表 -> 驗證 -> 付款」的順序，填寫與送出必須是兩個動作，
        否則驗證答案永遠來不及進入送出的那一次請求。
        """
        button = await self._locate(page, KKTIXSelectors.BTN_CONFIRM_ORDER, "confirm_order")
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
                    await ng_fill(page, page.locator(id_selector).first, attendee.id_number)
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
        container = await self._locate(
            page, KKTIXSelectors.CAPTCHA_CONTAINER, "captcha_container"
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
                TimelineEventType.MARK, "real_payment_submitted", provider=result.provider
            )
        return result
