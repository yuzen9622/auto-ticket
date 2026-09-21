"""拓元 (Tixcraft) 售票流程 Adapter。"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urljoin

from adapters.payment.base import PaymentOutcome, PaymentProvider, PaymentResult
from adapters.ticketing.base import TicketingAdapter
from adapters.ticketing.dom import (
    contains_cloudflare_challenge,
    first_present_visible,
    first_visible,
    present_count,
    page_text,
    try_solve_cloudflare_turnstile,
)
from adapters.ticketing.page_state import (
    REASON_NO_TICKET_UNITS,
    REASON_SELECTED,
    REASON_SOLD_OUT,
    CloudflareChallengeError,
    PageKind,
    PageState,
)
from adapters.ticketing.tixcraft.pages import BASE_URL as TIXCRAFT_BASE_URL
from adapters.ticketing.tixcraft.pages import (
    TicketRow,
    ZoneRow,
    parse_ticket_rows,
    parse_zone_rows,
)
from adapters.ticketing.tixcraft.selectors import TixcraftSelectors
from adapters.verification.base import (
    ChallengeKind,
    VerificationChallenge,
    VerificationProvider,
)
from domain.event import PlatformEnum
from domain.preference import SeatPreference, TicketPreference
from domain.task import CreditCardProfile, UserContactProfile
from strategy.ticket_strategy import TicketDecision, TicketOption, decide_ticket
from telemetry.timeline import TimelineEventType, TimelineRecorder

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page
else:
    Locator = Any
    Page = Any

#: 等開賣時的輪詢間隔。開著就立刻回，這個值只決定「還沒開」時多久再問一次。
SALE_PROBE_POLL_S = 0.1


def _ticket_row_options(rows: list[TicketRow]) -> list[TicketOption]:
    """張數頁的票種列轉成決策層看得懂的快照。

    `index` 用的是列在表格裡的位置，決策回來之後要靠它找回同一列的張數下拉。
    """
    return [
        TicketOption(
            index=row.index,
            name=row.name,
            price=row.price,
            available=row.available,
            remaining=None,
            status_text=row.status_text,
        )
        for row in rows
    ]


def _zone_options(rows: list[ZoneRow]) -> list[TicketOption]:
    """票區列轉成決策層看得懂的快照。

    票區頁是拓元少數會把剩餘張數印出來的地方，讀得到就帶上——`remaining` 一旦
    有值，`is_selectable` 就會把「只剩 1 張卻要買 2 張」的區擋在決策之外。
    """
    return [
        TicketOption(
            index=row.index,
            name=row.name,
            price=row.price,
            available=not row.sold_out,
            remaining=row.remaining,
            status_text=row.status_text,
        )
        for row in rows
    ]


class TixcraftAdapter(TicketingAdapter):
    platform: ClassVar[PlatformEnum] = PlatformEnum.TIXCRAFT

    def __init__(
        self,
        *,
        ticket_preference: TicketPreference | None = None,
        telemetry: TimelineRecorder | None = None,
        payment: PaymentProvider | None = None,
        verification: VerificationProvider | None = None,
        timeout_ms: int = 5000,
        probe_timeout_ms: int | None = None,
        ocr_max_retries: int = 5,
        ocr_expected_length: int | None = 4,
        screenshot: Callable[[str], Awaitable[Any]] | None = None,
        refresh_poll_s: float = 0.1,
        refresh_poll_rounds: int = 10,
        **kwargs: Any,
    ) -> None:
        super().__init__(ticket_preference=ticket_preference)
        self.telemetry = telemetry
        self.payment = payment
        self.verification = verification
        self.timeout_ms = timeout_ms
        self.probe_timeout_ms = min(probe_timeout_ms or 1000, timeout_ms)
        self.ocr_max_retries = ocr_max_retries
        self.ocr_expected_length = ocr_expected_length
        self.screenshot = screenshot
        self.refresh_poll_s = refresh_poll_s
        self.refresh_poll_rounds = refresh_poll_rounds
        self._image_fingerprint: str | None = None
        self.last_ticket_decision: TicketDecision | None = None
        self.last_payment_result: PaymentResult | None = None

    # ---------------------------------------------------------------- 頁面探測

    async def probe_page(self, page: Page, url: str | None = None) -> PageKind:
        if url is not None:
            curr = getattr(page, "url", "")
            if not curr or curr == "about:blank":
                await page.goto(url, wait_until="domcontentloaded")

        text = await page_text(page)
        if contains_cloudflare_challenge(text, TixcraftSelectors.CLOUDFLARE_CHALLENGE_TEXTS):
            return PageKind.CHALLENGE

        curr_url = getattr(page, "url", "").lower()
        if "/login" in curr_url or "user/login" in curr_url:
            return PageKind.LOGIN

        if "/activity/detail" in curr_url or "/activity/game" in curr_url:
            return PageKind.EVENT

        if any(seg in curr_url for seg in ("/ticket/area", "/ticket/ticket", "/ticket/verify")):
            return PageKind.REGISTRATION

        if (
            "/ticket/checkout" in curr_url
            or "/ticket/order" in curr_url
            or "/order" in curr_url
        ):
            return PageKind.ORDER

        return PageKind.UNKNOWN

    async def detect_page_state(self, page: Page) -> PageState:
        curr_url = getattr(page, "url", "").lower()
        text = await page_text(page)

        # 1. Queue-It 等候室優先判定，絕不觸發任何導航
        if "queue-it.net" in curr_url or "queue-it" in text.lower() or "#lbheaderh2" in text.lower():
            return PageState.QUEUE

        # 2. 開賣後遇 Cloudflare 直接拋出 fail-closed 例外
        if contains_cloudflare_challenge(text, TixcraftSelectors.CLOUDFLARE_CHALLENGE_TEXTS):
            raise CloudflareChallengeError("偵測到人機驗證挑戰；一律 fail-closed 中止")

        # 3. 售完或失敗彈窗
        # 「現在有沒有彈窗」是問句：用會等待的探測問，每一輪判頁都要多付一次逾時。
        failure_loc = await first_present_visible(page, TixcraftSelectors.FAILURE_MODAL)
        if failure_loc is not None:
            modal_text = await page_text(page)
            if any(marker in modal_text for marker in TixcraftSelectors.SOLD_OUT_TEXTS):
                return PageState.FAILURE_MODAL

        # 4. 被踢回登入頁：送出張數而沒有登入時拓元就這樣回應。判成 UNKNOWN 的話
        #    迴圈只會空轉到預算用完，最後回報「state loop budget exhausted」，
        #    把「你沒登入」說成了一個看不懂的逾時。
        if "/login" in curr_url:
            return PageState.GUEST_MODAL

        # 5. 購票頁但沒有登入：拓元要按下「確認張數」才會把人踢回登入頁，在那之前
        #    選區域、選張數、填驗證碼都照走。不先認出來的話，送出被拒會被讀成
        #    「驗證碼錯誤」，把驗證次數耗光後回報一個與真正原因無關的失敗。
        if "/ticket/" in curr_url and not any(
            marker in text for marker in TixcraftSelectors.LOGGED_IN_TEXTS
        ) and any(marker in text for marker in TixcraftSelectors.LOGGED_OUT_TEXTS):
            return PageState.GUEST_MODAL

        # 6. 特權碼／資格審查
        if "/ticket/verify" in curr_url:
            return PageState.QUALIFICATION_CODE

        # 5. 區域選擇頁
        if "/ticket/area" in curr_url:
            return PageState.TICKET_SELECTION

        # 6. 張數與表單頁：只要進入 /ticket/ticket 即為 FORM_FILLING，不得附加已選張數條件
        if "/ticket/ticket" in curr_url:
            return PageState.FORM_FILLING

        # 7. 付款與完成
        #    拓元送出張數後進 `/ticket/checkout`（購票確認→付款），這是「已經
        #    走到付款」的唯一標記；少了它整個流程會停在 UNKNOWN 直到逾時。
        if "/order/success" in curr_url or "/ticket/complete" in curr_url:
            return PageState.COMPLETED

        if (
            "/ticket/checkout" in curr_url
            or "/ticket/order" in curr_url
            or "/payment" in curr_url
        ):
            return PageState.PAYMENT_REQUIRED

        return PageState.UNKNOWN

    async def detect_sale_opened(self, page: Page, timeout_ms: int) -> bool:
        """偵測頁面是否開賣。

        售完的票區與場次連按鈕都不會渲染，所以「元素在不在」就是開賣與否；
        已經開賣時輪詢會立刻回，不必等滿逾時才確認一件早就成立的事。
        """
        deadline = asyncio.get_running_loop().time() + max(timeout_ms, 0) / 1000.0
        selectors = (
            TixcraftSelectors.ZONE_LINKS,
            TixcraftSelectors.SESSION_BUY_BUTTON,
        )
        while True:
            if await present_count(page, selectors) > 0:
                return True
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(SALE_PROBE_POLL_S)

    # ---------------------------------------------------------------- 票券與區域

    async def read_registration_tickets(self, page: Page) -> list[TicketOption]:
        """讀取區域列表或票種張數選項。"""
        options: list[TicketOption] = []
        curr_url = getattr(page, "url", "").lower()

        if "/ticket/area" in curr_url or "/ticket/game" in curr_url:
            options.extend(_zone_options(parse_zone_rows(await page.content())))

        elif "/ticket/ticket" in curr_url:
            options.extend(_ticket_row_options(parse_ticket_rows(await page.content())))

        return options

    async def select_tickets(
        self, page: Page, preference: TicketPreference
    ) -> tuple[bool, str]:
        options = await self.read_registration_tickets(page)
        if not options:
            return False, REASON_NO_TICKET_UNITS

        available = [opt for opt in options if opt.available]
        if not available:
            return False, REASON_SOLD_OUT

        target = available[0]
        decision = TicketDecision(
            status="SELECTED",
            option=target,
            quantity=preference.quantity,
            matched_priority=None,
            fallback_used=False,
            trace=(f"select_tickets -> {target.name}",),
        )
        return await self.apply_ticket_decision(page, decision)

    async def apply_ticket_decision(
        self, page: Page, decision: TicketDecision
    ) -> tuple[bool, str]:
        """套用票種決策：先保存 _target_quantity，再點選區域並等待進入 /ticket/ticket。"""
        self._target_quantity = decision.quantity
        self.last_ticket_decision = decision

        if decision.status != "SELECTED" or decision.option is None:
            return False, REASON_SOLD_OUT

        curr_url = getattr(page, "url", "").lower()
        if "/ticket/area" in curr_url:
            links = await page.locator(TixcraftSelectors.ZONE_LINKS).all()
            if decision.option.index >= len(links):
                return False, REASON_SOLD_OUT

            target_link = links[decision.option.index]
            await target_link.click()
            with contextlib.suppress(Exception):
                await page.wait_for_url("**/ticket/ticket/**", timeout=self.timeout_ms)

        return True, REASON_SELECTED

    async def reset_ticket_quantities(self, page: Page) -> bool:
        selects = await page.locator(TixcraftSelectors.TICKET_PRICE_SELECTS).all()
        for sel in selects:
            with contextlib.suppress(Exception):
                await sel.select_option("0")
        return True

    async def dismiss_failure_modal(self, page: Page) -> bool:
        """關閉售完彈窗並返回區域頁，等待區域容器可見。"""
        btn = await first_visible(
            page,
            TixcraftSelectors.FAILURE_MODAL_CLOSE,
            timeout_ms=self.probe_timeout_ms,
            telemetry=self.telemetry,
        )
        if btn is not None:
            await btn.click()

        curr_url = getattr(page, "url", "").lower()
        if "/ticket/ticket" in curr_url:
            with contextlib.suppress(Exception):
                await page.go_back()
                await page.wait_for_url("**/ticket/area/**", timeout=self.timeout_ms)

        # 確認區域列表容器可見
        container = await first_visible(
            page,
            TixcraftSelectors.ZONE_LIST_CONTAINER,
            timeout_ms=self.timeout_ms,
            telemetry=self.telemetry,
        )
        return container is not None

    async def handle_seat_selection(
        self, page: Page, preference: SeatPreference
    ) -> bool:
        return True

    # ---------------------------------------------------------------- 表單與驗證

    async def fill_contact_form(self, page: Page, profile: UserContactProfile) -> bool:
        """依決策張數填選張數下拉選單並勾選同意條款。"""
        quantity = (
            self._target_quantity
            if self._target_quantity is not None
            else (self.ticket_preference.quantity if self.ticket_preference else None)
        )
        if quantity is None:
            return False

        rows = parse_ticket_rows(await page.content())
        target_row, rejected = self._choose_ticket_row(rows)
        if rejected:
            return False

        select_loc = await self._quantity_select(page, target_row)
        if select_loc is not None:
            await select_loc.select_option(str(quantity))

        agree_loc = await first_present_visible(page, TixcraftSelectors.AGREE_CHECKBOX)
        if agree_loc is not None and not await agree_loc.is_checked():
            await agree_loc.check()

        return True

    def _choose_ticket_row(
        self, rows: list[TicketRow]
    ) -> tuple[TicketRow | None, bool]:
        """挑出要填張數的那一列票種；第二個回傳值是「偏好不接受這頁任何票種」。

        同一個票區的張數頁可能同時列出全票、優待票與身障票。一律填第一個下拉等於
        宣告「頁面第一個票種就是我要的」——優待票排在前面時買到的就是入場要查驗
        證件、資格不符當場作廢的票。只有一列可買時沒什麼好決策的，維持原本行為。
        """
        buyable = [row for row in rows if row.available]
        if len(buyable) < 2 or self.ticket_preference is None:
            return (buyable[0] if buyable else None), False

        decision = decide_ticket(_ticket_row_options(rows), self.ticket_preference)
        if self.telemetry is not None:
            self.telemetry.record(
                TimelineEventType.MARK,
                "tixcraft_ticket_row_decision",
                status=decision.status,
                ticket=decision.option.name if decision.option else "",
                trace=list(decision.trace),
            )
        if decision.status != "SELECTED" or decision.option is None:
            return None, True
        chosen = next((row for row in rows if row.index == decision.option.index), None)
        return chosen, False

    async def _quantity_select(
        self, page: Page, row: TicketRow | None
    ) -> Locator | None:
        """優先鎖定指定票種那一列的下拉；找不到才退回頁面上第一個。"""
        if row is not None and row.select_id:
            located = page.locator(f"#{row.select_id}")
            with contextlib.suppress(Exception):
                if await located.count() > 0:
                    return located.first
        return await first_visible(
            page,
            TixcraftSelectors.TICKET_PRICE_SELECTS,
            timeout_ms=self.timeout_ms,
            telemetry=self.telemetry,
            field="ticket_quantity_select",
        )

    async def detect_verification(self, page: Page) -> bool:
        # 驗證碼圖在張數頁是初始 HTML 的一部分，沒有「等它出現」這回事。
        img = await first_present_visible(page, TixcraftSelectors.CAPTCHA_IMAGE)
        return img is not None

    async def handle_verification(self, page: Page) -> bool:
        """處理圖形驗證碼，支援刷新換圖與 SHA-1 指紋比對。"""
        if self.verification is None:
            return False

        img_loc = await first_visible(
            page,
            TixcraftSelectors.CAPTCHA_IMAGE,
            timeout_ms=self.timeout_ms,
            telemetry=self.telemetry,
        )
        input_loc = await first_visible(
            page,
            TixcraftSelectors.CAPTCHA_INPUT,
            timeout_ms=self.timeout_ms,
            telemetry=self.telemetry,
        )
        if img_loc is None or input_loc is None:
            return True

        image_bytes = await img_loc.screenshot()
        self._image_fingerprint = hashlib.sha256(image_bytes).hexdigest()

        for attempt in range(1, self.ocr_max_retries + 1):
            result = await self.verification.solve(
                VerificationChallenge(
                    kind=ChallengeKind.IMAGE_CAPTCHA,
                    image_bytes=image_bytes,
                    question="",
                )
            )
            if (
                result.solved
                and result.answer
                and (
                    self.ocr_expected_length is None
                    or len(result.answer.strip()) == self.ocr_expected_length
                )
            ):
                await input_loc.fill(result.answer.strip())
                return True

            if attempt < self.ocr_max_retries:
                # 點擊驗證碼圖片以更換圖片
                await img_loc.click()
                before = self._image_fingerprint
                refreshed = False
                for _ in range(self.refresh_poll_rounds):
                    await asyncio.sleep(self.refresh_poll_s)
                    new_bytes = await img_loc.screenshot()
                    new_fp = hashlib.sha256(new_bytes).hexdigest()
                    if new_fp != before:
                        self._image_fingerprint = new_fp
                        image_bytes = new_bytes
                        refreshed = True
                        break
                if not refreshed:
                    break

        return False

    async def submit_order(self, page: Page) -> bool:
        btn = await first_visible(
            page,
            TixcraftSelectors.SUBMIT_BUTTON,
            timeout_ms=self.timeout_ms,
            telemetry=self.telemetry,
        )
        if btn is None:
            return False
        await btn.click()
        return True

    async def submit_qualification_code(self, page: Page, code: str) -> bool:
        input_loc = await first_visible(
            page,
            TixcraftSelectors.PROMO_INPUT,
            timeout_ms=self.timeout_ms,
            telemetry=self.telemetry,
        )
        if input_loc is None:
            return False
        await input_loc.fill(code)

        btn = await first_visible(
            page,
            TixcraftSelectors.PROMO_SUBMIT,
            timeout_ms=self.timeout_ms,
            telemetry=self.telemetry,
        )
        if btn is None:
            return False
        await btn.click()
        return True

    async def detect_verification_error(self, page: Page) -> bool:
        text = await page_text(page)
        return "驗證碼錯誤" in text or "驗證碼不正確" in text

    async def handle_cloudflare(self, page: Page) -> bool:
        async def is_active() -> bool:
            return contains_cloudflare_challenge(
                await page_text(page), TixcraftSelectors.CLOUDFLARE_CHALLENGE_TEXTS
            ) is not None

        return await try_solve_cloudflare_turnstile(page, is_challenge_active=is_active)

    async def login(self, page: Page, username: str, secret_token: str) -> bool:
        with contextlib.suppress(Exception):
            user_input = page.locator("#login_user, input[name='user'], input[name='account']").first
            pwd_input = page.locator("#login_password, input[name='password'], input[name='pwd']").first
            btn = page.locator("button[type='submit'], input[type='submit']").first
            if await user_input.is_visible() and await pwd_input.is_visible():
                await user_input.fill(username)
                await pwd_input.fill(secret_token)
                if await btn.is_visible():
                    await btn.click()
                    return True
        return False

    async def navigate_to_login_from_guest_modal(self, page: Page) -> bool:
        return True

    async def navigate_to_event(
        self, page: Page, event_url: str, session_preference: str | None = None
    ) -> bool:
        """從節目介紹頁一路走到區域選擇頁。

        節目介紹頁與場次頁只差 `/activity/detail/` → `/activity/game/`，直接換
        網址比點按鈕可靠：介紹頁的「立即購票」是 JS 綁定，點了不一定導頁。
        """
        await page.goto(event_url, wait_until="domcontentloaded")
        curr_url = getattr(page, "url", "")

        if "/activity/detail/" in curr_url:
            await page.goto(
                curr_url.replace("/activity/detail/", "/activity/game/"),
                wait_until="domcontentloaded",
            )
            curr_url = getattr(page, "url", "")

        if "/activity/game" not in curr_url.lower():
            return True

        with contextlib.suppress(Exception):
            await page.wait_for_selector(
                TixcraftSelectors.GAME_LIST_ROWS, timeout=self.timeout_ms
            )

        target_url = await self._pick_session_url(page, session_preference)
        if target_url is None:
            return False

        await page.goto(target_url, wait_until="domcontentloaded")
        return True

    async def _pick_session_url(
        self, page: Page, session_preference: str | None
    ) -> str | None:
        """挑一個還買得到的場次，回傳它的區域選擇頁網址。

        售完的場次那一列文字會出現「選購一空」，購票鈕仍在，點下去只會彈錯誤，
        因此要先用該列文字濾掉。
        """
        rows = await page.locator(TixcraftSelectors.GAME_LIST_ROWS).all()
        fallback: str | None = None
        for row in rows:
            row_text = ""
            with contextlib.suppress(Exception):
                row_text = await row.inner_text()
            if any(word in row_text for word in TixcraftSelectors.SOLD_OUT_TEXTS):
                continue

            button = row.locator(TixcraftSelectors.SESSION_BUY_BUTTON).first
            href = None
            with contextlib.suppress(Exception):
                href = await button.get_attribute("data-href") or await button.get_attribute("href")
            if not href:
                continue
            url = urljoin(TIXCRAFT_BASE_URL, href)
            if session_preference and session_preference in row_text:
                return url
            if fallback is None:
                fallback = url
        return fallback

    async def execute_payment(
        self, page: Page, payment_profile: CreditCardProfile | None
    ) -> PaymentResult:
        if self.payment is not None:
            return await self.payment.pay(page, payment_profile)
        return PaymentResult(
            PaymentOutcome.CHECKPOINT_REACHED,
            "tixcraft",
            {"status": "checkpoint_reached"},
        )
