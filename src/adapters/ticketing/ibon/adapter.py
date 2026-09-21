"""ibon 售票流程 Adapter。"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlsplit

from adapters.payment.base import PaymentOutcome, PaymentProvider, PaymentResult
from adapters.ticketing.base import TicketingAdapter
from adapters.ticketing.dom import (
    contains_cloudflare_challenge,
    first_visible,
    page_text,
    try_solve_cloudflare_turnstile,
)
from adapters.ticketing.ibon.selectors import IbonSelectors
from adapters.ticketing.page_state import (
    REASON_NO_TICKET_UNITS,
    REASON_SELECTED,
    REASON_SOLD_OUT,
    CloudflareChallengeError,
    PageKind,
    PageState,
)
from adapters.verification.base import (
    ChallengeKind,
    VerificationChallenge,
    VerificationProvider,
)
from domain.event import PlatformEnum
from domain.preference import SeatPreference, TicketPreference
from domain.task import CreditCardProfile, UserContactProfile
from strategy.ticket_strategy import TicketDecision, TicketOption
from telemetry.timeline import TimelineEventType, TimelineRecorder

if TYPE_CHECKING:
    from playwright.async_api import Page
else:
    Page = Any

PRICE_RE = re.compile(r"(\d[\d,]*)")
OLD_URL_RE = re.compile(r"UTK0202_[^.]*\.aspx", re.IGNORECASE)


def normalize_ibon_url(url: str) -> str:
    """轉換舊版 ibon 網址：UTK0202_... 轉為 UTK0201_000.aspx。"""
    if "UTK0202" in url.upper() and "PERFORMANCE_PRICE_AREA_ID" in url.upper():
        # 將舊區域網址替換為標準區域選擇頁
        return OLD_URL_RE.sub("UTK0201_000.aspx", url)
    return url


class IbonAdapter(TicketingAdapter):
    platform: ClassVar[PlatformEnum] = PlatformEnum.IBON

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
        kick_max_retries: int = 2,
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
        self.kick_max_retries = kick_max_retries
        self.refresh_poll_s = refresh_poll_s
        self.refresh_poll_rounds = refresh_poll_rounds
        self._kick_count = 0
        self._image_fingerprint: str | None = None
        self.last_ticket_decision: TicketDecision | None = None
        self.last_payment_result: PaymentResult | None = None

    # ---------------------------------------------------------------- 頁面探測

    async def probe_page(self, page: Page, url: str | None = None) -> PageKind:
        if url is not None:
            curr = getattr(page, "url", "")
            if not curr or curr == "about:blank":
                target_url = normalize_ibon_url(url)
                await page.goto(target_url, wait_until="domcontentloaded")

        text = await page_text(page)
        if contains_cloudflare_challenge(text, IbonSelectors.CLOUDFLARE_CHALLENGE_TEXTS):
            return PageKind.CHALLENGE

        curr_url = getattr(page, "url", "").lower()

        # 防踢登入頁檢查
        if "loginhuiwan" in curr_url or "/login" in curr_url or "member/login" in curr_url:
            return PageKind.LOGIN

        if "/activityinfo/details/" in curr_url:
            return PageKind.EVENT

        if any(seg in curr_url for seg in ("utk0201_0.aspx", "utk0201_000.aspx", "utk0201_001.aspx")):
            return PageKind.REGISTRATION

        if any(seg in curr_url for seg in ("utk0201_002.aspx", "utk0201_003.aspx", "/order")):
            return PageKind.ORDER

        return PageKind.UNKNOWN

    async def detect_page_state(self, page: Page) -> PageState:
        curr_url = getattr(page, "url", "").lower()
        text = await page_text(page)

        # 1. Queue-It 等候室優先判定，絕不導航
        if "queue-it.net" in curr_url or "queue-it" in text.lower() or "#lbheaderh2" in text.lower():
            return PageState.QUEUE

        # 2. 開賣後遇 Cloudflare 直接 fail-closed
        if contains_cloudflare_challenge(text, IbonSelectors.CLOUDFLARE_CHALLENGE_TEXTS):
            raise CloudflareChallengeError("偵測到人機驗證挑戰；一律 fail-closed 中止")

        # 3. 售完或錯誤彈窗
        failure_loc = await first_visible(
            page,
            IbonSelectors.FAILURE_MODAL,
            timeout_ms=self.probe_timeout_ms,
            telemetry=self.telemetry,
            field="failure_modal",
        )
        if failure_loc is not None:
            modal_text = await page_text(page)
            if any(marker in modal_text for marker in IbonSelectors.SOLD_OUT_TEXTS):
                return PageState.FAILURE_MODAL

        # 4. 特權碼／資格審查
        if "utk0201_0.aspx" in curr_url:
            return PageState.QUALIFICATION_CODE

        # 5. 區域選擇頁
        if "utk0201_000.aspx" in curr_url:
            return PageState.TICKET_SELECTION

        # 6. 張數與驗證頁
        if "utk0201_001.aspx" in curr_url:
            return PageState.FORM_FILLING

        # 7. 付款與完成
        if any(seg in curr_url for seg in ("utk0201_002.aspx", "utk0201_003.aspx", "/payment")):
            return PageState.PAYMENT_REQUIRED

        if "complete" in curr_url or "success" in curr_url:
            return PageState.COMPLETED

        return PageState.UNKNOWN

    async def detect_sale_opened(self, page: Page, timeout_ms: int) -> bool:
        btn = await first_visible(
            page,
            (IbonSelectors.ZONE_BUY_BUTTONS, IbonSelectors.ACTIVITY_DETAILS_BUY_BTN),
            timeout_ms=timeout_ms,
            telemetry=self.telemetry,
        )
        return btn is not None

    # ---------------------------------------------------------------- 票券與區域

    async def read_registration_tickets(self, page: Page) -> list[TicketOption]:
        """讀取區域選擇頁 (UTK0201_000.aspx) 或張數頁 (UTK0201_001.aspx)。"""
        options: list[TicketOption] = []
        curr_url = getattr(page, "url", "").lower()

        if "utk0201_000.aspx" in curr_url:
            rows = await page.locator(IbonSelectors.ZONE_ROWS).all()
            for idx, row in enumerate(rows):
                row_text = (await row.inner_text()).strip()
                match = PRICE_RE.search(row_text)
                price = 0
                if match:
                    try:
                        price = int(match.group(1).replace(",", ""))
                    except (TypeError, ValueError):
                        price = 0

                is_sold_out = any(m in row_text for m in IbonSelectors.SOLD_OUT_TEXTS)
                cols = row_text.split()
                name = cols[0] if cols else f"區域 {idx + 1}"
                options.append(
                    TicketOption(
                        index=idx,
                        name=name,
                        price=price,
                        available=not is_sold_out,
                        remaining=None,
                        status_text=row_text,
                    )
                )

        elif "utk0201_001.aspx" in curr_url:
            selects = await page.locator(IbonSelectors.TICKET_SELECTS).all()
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
        """套用票種決策：保存張數，點擊區域選購並等待進入 UTK0201_001.aspx。"""
        self._target_quantity = decision.quantity
        self.last_ticket_decision = decision

        if decision.status != "SELECTED" or decision.option is None:
            return False, REASON_SOLD_OUT

        curr_url = getattr(page, "url", "").lower()
        if "utk0201_000.aspx" in curr_url:
            buttons = await page.locator(IbonSelectors.ZONE_BUY_BUTTONS).all()
            if decision.option.index >= len(buttons):
                return False, REASON_SOLD_OUT

            target_btn = buttons[decision.option.index]
            await target_btn.click()
            with contextlib.suppress(Exception):
                await page.wait_for_url("**/UTK0201_001.aspx*", timeout=self.timeout_ms)

        return True, REASON_SELECTED

    async def reset_ticket_quantities(self, page: Page) -> bool:
        selects = await page.locator(IbonSelectors.TICKET_SELECTS).all()
        for sel in selects:
            with contextlib.suppress(Exception):
                await sel.select_option("0")
        return True

    async def dismiss_failure_modal(self, page: Page) -> bool:
        """關閉售完彈窗並主動返回 UTK0201_000 區域頁，等待區域表格可見。"""
        btn = await first_visible(
            page,
            IbonSelectors.FAILURE_MODAL_CLOSE,
            timeout_ms=self.probe_timeout_ms,
            telemetry=self.telemetry,
        )
        if btn is not None:
            await btn.click()

        curr_url = getattr(page, "url", "").lower()
        if "utk0201_001.aspx" in curr_url:
            with contextlib.suppress(Exception):
                await page.go_back()
                await page.wait_for_url("**/UTK0201_000.aspx*", timeout=self.timeout_ms)

        # 確認區域表格容器可見
        table = await first_visible(
            page,
            IbonSelectors.ZONE_TABLE,
            timeout_ms=self.timeout_ms,
            telemetry=self.telemetry,
        )
        return table is not None

    async def handle_seat_selection(
        self, page: Page, preference: SeatPreference
    ) -> bool:
        return True

    # ---------------------------------------------------------------- 表單與驗證

    async def fill_contact_form(self, page: Page, profile: UserContactProfile) -> bool:
        """填寫張數、非相鄰座位偏好與同意條款。"""
        quantity = (
            self._target_quantity
            if self._target_quantity is not None
            else (self.ticket_preference.quantity if self.ticket_preference else None)
        )
        if quantity is None:
            return False

        select_loc = await first_visible(
            page,
            IbonSelectors.TICKET_SELECTS,
            timeout_ms=self.timeout_ms,
            telemetry=self.telemetry,
            field="ticket_quantity_select",
        )
        if select_loc is not None:
            await select_loc.select_option(str(quantity))

        # 非相鄰座位勾選
        chk_seat = await first_visible(
            page,
            IbonSelectors.NON_ADJACENT_SEAT_CHECKBOX,
            timeout_ms=self.probe_timeout_ms,
            telemetry=self.telemetry,
            field="non_adjacent_seat_checkbox",
        )
        if chk_seat is not None and not await chk_seat.is_checked():
            await chk_seat.check()

        # 同意規則勾選
        agree_loc = await first_visible(
            page,
            IbonSelectors.AGREE_CHECKBOX,
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
            IbonSelectors.CAPTCHA_IMAGE,
            timeout_ms=self.probe_timeout_ms,
            telemetry=self.telemetry,
            field="captcha_image",
        )
        return img is not None

    async def handle_verification(self, page: Page) -> bool:
        """處理圖形驗證碼，支援 OCR 與刷新比對。"""
        if self.verification is None:
            return False

        img_loc = await first_visible(
            page,
            IbonSelectors.CAPTCHA_IMAGE,
            timeout_ms=self.timeout_ms,
            telemetry=self.telemetry,
        )
        input_loc = await first_visible(
            page,
            IbonSelectors.CAPTCHA_INPUT,
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
            IbonSelectors.SUBMIT_BUTTON,
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
            IbonSelectors.PROMO_INPUT,
            timeout_ms=self.timeout_ms,
            telemetry=self.telemetry,
        )
        if input_loc is None:
            return False
        await input_loc.fill(code)

        btn = await first_visible(
            page,
            IbonSelectors.PROMO_SUBMIT,
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
                await page_text(page), IbonSelectors.CLOUDFLARE_CHALLENGE_TEXTS
            ) is not None

        return await try_solve_cloudflare_turnstile(page, is_challenge_active=is_active)

    async def login(self, page: Page, username: str, secret_token: str) -> bool:
        pwd_input: Any | None = None
        login_succeeded = False
        try:
            # 1. 若尚未在會員登入頁，先導航至登入入口
            curr_url = getattr(page, "url", "").lower()
            if "userlogin.aspx" not in curr_url:
                login_url = (
                    "https://huiwan.ibon.com.tw/huiwan/LoginHuiwan/UserLogin.aspx"
                    "?taxid=775995263&targeturl=https://ticket.ibon.com.tw/login"
                )
                await page.goto(login_url, wait_until="domcontentloaded")

            # 2. 若當前在 Cloudflare 挑戰，先嘗試處理
            text = await page_text(page)
            if contains_cloudflare_challenge(text, IbonSelectors.CLOUDFLARE_CHALLENGE_TEXTS):
                await self.handle_cloudflare(page)
                await asyncio.sleep(2.0)

            # 3. 支援多組常見 ASP.NET WebForms 登入欄位（含真實欄位 #Mobile, #password, #boxWebCode）
            user_selectors = (
                "#Mobile",
                "input[name='Mobile']",
                "input[id$='txtID']",
                "input[id$='txtAccount']",
                "input[id$='txtUser']",
                "input[id$='txtUserId']",
                "input[id$='txtUserName']",
                "input[id$='txtMobile']",
                "input[placeholder*='手機']",
                "input[placeholder*='身分證']",
                "input[type='text']",
            )
            pwd_selectors = (
                "#password",
                "input[name='password']",
                "input[type='password']",
                "input[id$='txtPassword']",
                "input[id$='txtPwd']",
                "input[placeholder*='密碼']",
            )
            captcha_img_selectors = (
                "#validateCode",
                "#imgVerifyCode",
                "img[src*='validateCode']",
                "[id$='imgVerify']",
            )
            captcha_input_selectors = (
                "#boxWebCode",
                "#txtVerifyCode",
                "input[name='boxWebCode']",
                "[id$='txtVerify']",
                "input[placeholder*='驗證碼']",
            )
            btn_selectors = (
                "#login",
                "#btnLogin",
                "button#login",
                "input[type='submit']",
                "button[type='submit']",
                "input[value*='登入']",
                "button:has-text('登入')",
            )

            user_input = await first_visible(
                page, ", ".join(user_selectors), timeout_ms=self.timeout_ms
            )
            pwd_input = await first_visible(
                page, ", ".join(pwd_selectors), timeout_ms=self.timeout_ms
            )
            if user_input is None or pwd_input is None:
                return False

            captcha_img = await first_visible(
                page, ", ".join(captcha_img_selectors), timeout_ms=self.timeout_ms
            )
            captcha_input = await first_visible(
                page, ", ".join(captcha_input_selectors), timeout_ms=self.timeout_ms
            )
            has_captcha = captcha_img is not None or captcha_input is not None
            if has_captcha and (
                captcha_img is None
                or captcha_input is None
                or self.verification is None
            ):
                return False

            # ibon 登入驗證碼在送出錯誤答案後會換圖；每次都重新截圖辨識，
            # 不沿用已被伺服器拒絕的答案。
            attempts = max(1, self.ocr_max_retries) if has_captcha else 1
            previous_fingerprint: str | None = None
            for attempt in range(1, attempts + 1):
                await user_input.fill(username)
                await pwd_input.fill(secret_token)

                submitted_fingerprint: str | None = None
                if captcha_img is not None and captcha_input is not None:
                    image_bytes = await captcha_img.screenshot()
                    submitted_fingerprint = hashlib.sha256(image_bytes).hexdigest()

                    # 若上一輪未觸發伺服器換圖，主動點圖取得新的 challenge。
                    if submitted_fingerprint == previous_fingerprint:
                        await captcha_img.click()
                        for _ in range(self.refresh_poll_rounds):
                            await asyncio.sleep(self.refresh_poll_s)
                            image_bytes = await captcha_img.screenshot()
                            refreshed_fingerprint = hashlib.sha256(image_bytes).hexdigest()
                            if refreshed_fingerprint != submitted_fingerprint:
                                submitted_fingerprint = refreshed_fingerprint
                                break

                    res = await self.verification.solve(
                        VerificationChallenge(
                            question="ibon 登入驗證碼",
                            kind=ChallengeKind.IMAGE_CAPTCHA,
                            image_bytes=image_bytes,
                        )
                    )
                    cleaned_code = (
                        re.sub(r"[^a-zA-Z0-9]", "", res.answer)
                        if res.solved and res.answer
                        else ""
                    )
                    if not cleaned_code:
                        previous_fingerprint = submitted_fingerprint
                        if self.telemetry is not None:
                            self.telemetry.record(
                                TimelineEventType.MARK,
                                "ibon_login_captcha_unsolved",
                                attempt=attempt,
                            )
                        continue
                    await captcha_input.fill(cleaned_code)

                btn = await first_visible(
                    page, ", ".join(btn_selectors), timeout_ms=self.timeout_ms
                )
                if btn is None:
                    return False
                await btn.click()

                captcha_rejected = False
                for _ in range(16):
                    await asyncio.sleep(0.5)
                    curr = getattr(page, "url", "").lower()
                    parsed_url = urlsplit(curr)
                    if (
                        parsed_url.hostname == "ticket.ibon.com.tw"
                        and parsed_url.path in {"", "/"}
                        and "token=" not in parsed_url.query
                    ):
                        with contextlib.suppress(Exception):
                            await page.wait_for_load_state(
                                "domcontentloaded", timeout=self.timeout_ms
                            )
                        login_succeeded = True
                        if self.telemetry is not None:
                            self.telemetry.record(
                                TimelineEventType.MARK,
                                "ibon_login_succeeded",
                                attempt=attempt,
                            )
                        return True

                    if captcha_img is not None and submitted_fingerprint is not None:
                        with contextlib.suppress(Exception):
                            current_bytes = await captcha_img.screenshot()
                            current_fingerprint = hashlib.sha256(current_bytes).hexdigest()
                            if current_fingerprint != submitted_fingerprint:
                                captcha_rejected = True
                                break

                if not has_captcha:
                    return False

                previous_fingerprint = submitted_fingerprint
                if self.telemetry is not None:
                    self.telemetry.record(
                        TimelineEventType.MARK,
                        "ibon_login_captcha_rejected",
                        attempt=attempt,
                        refreshed=captcha_rejected,
                    )
            return False
        except Exception as exc:  # noqa: BLE001 - browser/Playwright failures are adapter failures
            if self.telemetry is not None:
                self.telemetry.record_error("ibon_login_failed", exc)
        finally:
            if not login_succeeded and pwd_input is not None:
                with contextlib.suppress(Exception):
                    await pwd_input.fill("")
        return False

    async def navigate_to_login_from_guest_modal(self, page: Page) -> bool:
        return True

    async def navigate_to_event(
        self, page: Page, event_url: str, session_preference: str | None = None
    ) -> bool:
        target_url = normalize_ibon_url(event_url)
        await page.goto(target_url, wait_until="domcontentloaded")
        curr_url = getattr(page, "url", "").lower()

        if "/activityinfo/details/" in curr_url:
            buy_btn = await first_visible(
                page,
                IbonSelectors.ACTIVITY_DETAILS_BUY_BTN,
                timeout_ms=self.timeout_ms,
                telemetry=self.telemetry,
            )
            if buy_btn is not None:
                await buy_btn.click()
                with contextlib.suppress(Exception):
                    await page.wait_for_url("**/UTK0201_000.aspx*", timeout=self.timeout_ms)

        return True

    async def execute_payment(
        self, page: Page, payment_profile: CreditCardProfile | None
    ) -> PaymentResult:
        if self.payment is not None:
            return await self.payment.pay(page, payment_profile)
        return PaymentResult(
            PaymentOutcome.CHECKPOINT_REACHED,
            "ibon",
            {"status": "checkpoint_reached"},
        )
