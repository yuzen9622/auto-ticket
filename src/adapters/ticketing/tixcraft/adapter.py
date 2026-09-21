"""拓元 (Tixcraft) 售票流程 Adapter。"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, ClassVar

from adapters.payment.base import PaymentOutcome, PaymentProvider, PaymentResult
from adapters.ticketing.base import TicketingAdapter
from adapters.ticketing.dom import (
    contains_cloudflare_challenge,
    first_visible,
    page_text,
    try_solve_cloudflare_turnstile,
)
from adapters.ticketing.page_state import (
    CloudflareChallengeError,
    PageKind,
    PageState,
    REASON_NO_TICKET_UNITS,
    REASON_SELECTED,
    REASON_SOLD_OUT,
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
from strategy.ticket_strategy import TicketDecision, TicketOption
from telemetry.timeline import TimelineRecorder

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page
else:
    Locator = Any
    Page = Any

PRICE_RE = re.compile(r"(\d[\d,]*)\s*元?")


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

        if "/ticket/order" in curr_url or "/order" in curr_url:
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
        failure_loc = await first_visible(
            page,
            TixcraftSelectors.FAILURE_MODAL,
            timeout_ms=self.probe_timeout_ms,
            telemetry=self.telemetry,
            field="failure_modal",
        )
        if failure_loc is not None:
            modal_text = await page_text(page)
            if any(marker in modal_text for marker in TixcraftSelectors.SOLD_OUT_TEXTS):
                return PageState.FAILURE_MODAL

        # 4. 特權碼／資格審查
        if "/ticket/verify" in curr_url:
            return PageState.QUALIFICATION_CODE

        # 5. 區域選擇頁
        if "/ticket/area" in curr_url:
            return PageState.TICKET_SELECTION

        # 6. 張數與表單頁：只要進入 /ticket/ticket 即為 FORM_FILLING，不得附加已選張數條件
        if "/ticket/ticket" in curr_url:
            return PageState.FORM_FILLING

        # 7. 付款與完成
        if "/ticket/order" in curr_url or "/payment" in curr_url:
            return PageState.PAYMENT_REQUIRED

        if "/order/success" in curr_url or "/ticket/complete" in curr_url:
            return PageState.COMPLETED

        return PageState.UNKNOWN

    async def detect_sale_opened(self, page: Page, timeout_ms: int) -> bool:
        """偵測頁面是否開賣（區域連結或購票鈕是否可見）。"""
        btn = await first_visible(
            page,
            (TixcraftSelectors.ZONE_LINKS, TixcraftSelectors.SESSION_BUY_BUTTON),
            timeout_ms=timeout_ms,
            telemetry=self.telemetry,
        )
        return btn is not None

    # ---------------------------------------------------------------- 票券與區域

    async def read_registration_tickets(self, page: Page) -> list[TicketOption]:
        """讀取區域列表或票種張數選項。"""
        options: list[TicketOption] = []
        curr_url = getattr(page, "url", "").lower()

        if "/ticket/area" in curr_url or "/ticket/game" in curr_url:
            elements = await page.locator(TixcraftSelectors.ZONE_LINKS).all()
            for idx, el in enumerate(elements):
                raw_text = (await el.inner_text()).strip()
                match = PRICE_RE.search(raw_text)
                price = 0
                if match:
                    try:
                        price = int(match.group(1).replace(",", ""))
                    except Exception:
                        price = 0

                is_sold_out = any(m in raw_text for m in TixcraftSelectors.SOLD_OUT_TEXTS)
                options.append(
                    TicketOption(
                        index=idx,
                        name=raw_text.split("(")[0].strip() or f"區域 {idx + 1}",
                        price=price,
                        available=not is_sold_out,
                        remaining=None,
                        status_text=raw_text,
                    )
                )

        elif "/ticket/ticket" in curr_url:
            selects = await page.locator(TixcraftSelectors.TICKET_PRICE_SELECTS).all()
            for idx, sel in enumerate(selects):
                sel_id = await sel.get_attribute("id") or ""
                options.append(
                    TicketOption(
                        index=idx,
                        name=f"票種 {sel_id}",
                        price=0,
                        available=True,
                        remaining=None,
                        status_text=sel_id,
                    )
                )

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

        select_loc = await first_visible(
            page,
            TixcraftSelectors.TICKET_PRICE_SELECTS,
            timeout_ms=self.timeout_ms,
            telemetry=self.telemetry,
            field="ticket_quantity_select",
        )
        if select_loc is not None:
            await select_loc.select_option(str(quantity))

        agree_loc = await first_visible(
            page,
            TixcraftSelectors.AGREE_CHECKBOX,
            timeout_ms=self.probe_timeout_ms,
            telemetry=self.telemetry,
            field="agree_checkbox",
        )
        if agree_loc is not None and not await agree_loc.is_checked():
            await agree_loc.check()

        return True

    async def detect_verification(self, page: Page) -> bool:
        img = await first_visible(
            page,
            TixcraftSelectors.CAPTCHA_IMAGE,
            timeout_ms=self.probe_timeout_ms,
            telemetry=self.telemetry,
            field="captcha_image",
        )
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
        await page.goto(event_url, wait_until="domcontentloaded")
        curr_url = getattr(page, "url", "").lower()

        if "/activity/detail" in curr_url:
            buy_btn = await first_visible(
                page,
                "a[href*='/activity/game/'], .buy a, a.btn-primary",
                timeout_ms=self.timeout_ms,
                telemetry=self.telemetry,
            )
            if buy_btn is not None:
                await buy_btn.click()
                with contextlib.suppress(Exception):
                    await page.wait_for_url("**/activity/game/**", timeout=self.timeout_ms)

        curr_url = getattr(page, "url", "").lower()
        if "/activity/game" in curr_url:
            rows = await page.locator(TixcraftSelectors.GAME_LIST_ROWS).all()
            target_btn: Locator | None = None
            for row in rows:
                row_text = await row.inner_text()
                if session_preference and session_preference in row_text:
                    target_btn = row.locator(TixcraftSelectors.SESSION_BUY_BUTTON).first
                    break
                if target_btn is None:
                    target_btn = row.locator(TixcraftSelectors.SESSION_BUY_BUTTON).first

            if target_btn is not None and await target_btn.is_visible():
                await target_btn.click()
                with contextlib.suppress(Exception):
                    await page.wait_for_url("**/ticket/**", timeout=self.timeout_ms)

        return True

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
