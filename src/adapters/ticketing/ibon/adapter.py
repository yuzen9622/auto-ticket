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
    first_present_visible,
    first_visible,
    present_count,
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

#: 座位圖每一區的 href：`javascript:Send('0202','PERF_ID','AREA_ID','GROUP_ID');`
AREA_SEND_RE = re.compile(
    r"Send\(\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*'([^']*)'\s*\)"
)
#: `票區:2M樓VIP1區6000 票價：6000 尚餘：熱賣中`
AREA_TITLE_RE = re.compile(
    r"票區[:：]\s*(?P<name>.*?)\s*票價[:：]\s*(?P<price>[\d,]+)"
    r"(?:\s*尚餘[:：]\s*(?P<remaining>\S+))?"
)

#: 張數頁：電腦配位走 UTK0202_，自行選位走 UTK0205_／UTK0201_001。
QUANTITY_PAGE_MARKERS = ("utk0202_", "utk0205_", "utk0201_001.aspx")
#: 購票確認與付款；`UTK0207_` 是刷卡，抵達即代表已經走到付款。
PAYMENT_PAGE_MARKERS = ("utk0206_", "utk0207_", "/payment")

#: 等開賣時的輪詢間隔。開著就立刻回，這個值只決定「還沒開」時多久再問一次。
SALE_PROBE_POLL_S = 0.1


def normalize_ibon_url(url: str) -> str:
    """ibon 的訂購網址不需要改寫；保留函式是因為呼叫端與測試都在用。"""
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

        # 訂購頁的網域本身就叫 orders.ibon.com.tw，用 "/order" 比對會把票區頁
        # 也判成訂單頁；只認訂單查詢頁與付款流程的實際頁碼。
        if any(seg in curr_url for seg in PAYMENT_PAGE_MARKERS) or "/web/orders" in curr_url:
            return PageKind.ORDER

        if "utk0201_0.aspx" in curr_url or "utk0201_000.aspx" in curr_url or any(
            seg in curr_url for seg in QUANTITY_PAGE_MARKERS
        ):
            return PageKind.REGISTRATION

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
        # 「現在有沒有彈窗」是問句：用會等待的探測問，每一輪判頁都要多付一次逾時。
        failure_loc = await first_present_visible(page, IbonSelectors.FAILURE_MODAL)
        if failure_loc is not None:
            modal_text = await page_text(page)
            if any(marker in modal_text for marker in IbonSelectors.SOLD_OUT_TEXTS):
                return PageState.FAILURE_MODAL

        # 4. 被踢回登入頁：會員 session 過期時 ibon 會導到 huiwan 單一登入。
        if "loginhuiwan" in curr_url or "/login" in curr_url:
            return PageState.GUEST_MODAL

        # 5. 特權碼／資格審查
        if "utk0201_0.aspx" in curr_url:
            return PageState.QUALIFICATION_CODE

        # 5. 完成頁要排在付款頁前面判，否則訂購完成也會被當成還要付款
        if "complete" in curr_url or "success" in curr_url:
            return PageState.COMPLETED

        # 6. 購票確認／付款（UTK0206_、UTK0207_）
        if any(seg in curr_url for seg in PAYMENT_PAGE_MARKERS):
            return PageState.PAYMENT_REQUIRED

        # 7. 選擇票區（座位圖）
        if "utk0201_000.aspx" in curr_url:
            return PageState.TICKET_SELECTION

        # 8. 張數頁
        if any(seg in curr_url for seg in QUANTITY_PAGE_MARKERS):
            return PageState.FORM_FILLING

        return PageState.UNKNOWN

    async def detect_sale_opened(self, page: Page, timeout_ms: int) -> bool:
        """票區出現就算開賣。

        票區是座位圖上的 `<area>`，零尺寸，Playwright 的可見性判定對它永遠是
        False——拿「可不可見」問，不只每次都等滿逾時，拿到的答案還一定是「沒開賣」。
        改成輪詢「在不在 DOM 裡」，開著就立刻回，沒開才等到逾時。
        """
        return await self._poll_until_present(
            page,
            present_selectors=IbonSelectors.ZONE_ROWS,
            visible_selectors=IbonSelectors.ACTIVITY_DETAILS_BUY_BTN,
            timeout_ms=timeout_ms,
        )

    async def _poll_until_present(
        self,
        page: Page,
        *,
        present_selectors: str,
        visible_selectors: str,
        timeout_ms: int,
    ) -> bool:
        deadline = asyncio.get_running_loop().time() + max(timeout_ms, 0) / 1000.0
        while True:
            if await present_count(page, present_selectors) > 0:
                return True
            if await first_present_visible(page, visible_selectors) is not None:
                return True
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(SALE_PROBE_POLL_S)

    # ---------------------------------------------------------------- 票券與區域

    async def read_registration_tickets(self, page: Page) -> list[TicketOption]:
        """讀取區域選擇頁 (UTK0201_000.aspx) 或張數頁 (UTK0201_001.aspx)。"""
        options: list[TicketOption] = []
        curr_url = getattr(page, "url", "").lower()

        if "utk0201_000.aspx" in curr_url:
            for idx, area in enumerate(await self._read_zone_areas(page)):
                options.append(
                    TicketOption(
                        index=idx,
                        name=area["name"],
                        price=area["price"],
                        available=area["available"],
                        remaining=area["remaining"],
                        status_text=area["title"],
                    )
                )

        elif any(seg in curr_url for seg in QUANTITY_PAGE_MARKERS):
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

    async def _read_zone_areas(self, page: Page) -> list[dict[str, Any]]:
        """讀座位圖上的票區。

        `<area>` 是零尺寸元素，可見性判斷與 `inner_text()` 都對它無效，只能讀
        屬性。`title` 同時帶了區名、票價與剩餘量，是這一頁唯一的庫存來源。
        """
        areas: list[dict[str, Any]] = []
        for el in await page.locator(IbonSelectors.ZONE_ROWS).all():
            title = (await el.get_attribute("title")) or ""
            href = (await el.get_attribute("href")) or ""
            matched = AREA_TITLE_RE.search(title)
            if matched is None:
                continue

            try:
                price = int(matched.group("price").replace(",", ""))
            except (TypeError, ValueError):
                price = 0

            remaining_text = (matched.group("remaining") or "").strip()
            remaining: int | None = None
            if remaining_text.isdigit():
                remaining = int(remaining_text)
            # 「熱賣中」代表有票但不公布剩餘量；只有明確的 0 才是售完。
            available = remaining != 0 and not any(
                marker in title for marker in IbonSelectors.SOLD_OUT_TEXTS
            )

            send = AREA_SEND_RE.search(href)
            areas.append(
                {
                    "name": matched.group("name").strip() or title,
                    "price": price,
                    "remaining": remaining,
                    "available": available,
                    "title": title,
                    "send_args": list(send.groups()) if send else None,
                }
            )
        return areas

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
            areas = await self._read_zone_areas(page)
            if decision.option.index >= len(areas):
                return False, REASON_SOLD_OUT

            send_args = areas[decision.option.index].get("send_args")
            if not send_args:
                return False, REASON_SOLD_OUT

            # `<area>` 點不動（零尺寸），而且 Send() 自己會擋未開賣的場次並依
            # 「自行選位／電腦配位」決定下一頁，照呼叫比自己拼網址安全。
            await page.evaluate(
                "args => window.Send(args[0], args[1], args[2], args[3])", send_args
            )
            with contextlib.suppress(Exception):
                await page.wait_for_url(
                    lambda url: any(
                        marker in url.lower() for marker in QUANTITY_PAGE_MARKERS
                    ),
                    timeout=self.timeout_ms,
                )

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
        if any(seg in curr_url for seg in QUANTITY_PAGE_MARKERS):
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
        # 非相鄰座位與同意條款都是「有就勾」的選配欄位，多數場次根本沒有這兩格。
        chk_seat = await first_present_visible(
            page, IbonSelectors.NON_ADJACENT_SEAT_CHECKBOX
        )
        if chk_seat is not None and not await chk_seat.is_checked():
            await chk_seat.check()

        # 同意規則勾選
        agree_loc = await first_present_visible(page, IbonSelectors.AGREE_CHECKBOX)
        if agree_loc is not None and not await agree_loc.is_checked():
            await agree_loc.check()

        return True

    async def detect_verification(self, page: Page) -> bool:
        img = await first_present_visible(page, IbonSelectors.CAPTCHA_IMAGE)
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
        """從活動頁走到選擇票區頁。

        活動頁是 Angular SPA，場次清單由 `GetGameInfoList` 撐起來，而且在活動的
        顯示期間之外根本不渲染；靠點畫面上的購票鈕會在開賣前後都撲空。改成直接
        向同一支 API 要場次的訂購網址再導頁，開賣瞬間也少掉一次渲染等待。
        """
        target_url = normalize_ibon_url(event_url)
        if "/activityinfo/details/" in target_url.lower():
            purchase_url = await self._resolve_session_url(
                target_url, session_preference
            )
            if purchase_url is not None:
                target_url = purchase_url

        await page.goto(target_url, wait_until="domcontentloaded")
        curr_url = getattr(page, "url", "").lower()

        if "/activityinfo/details/" in curr_url:
            # API 沒給網址（例如還沒開放）時，退回頁面上的購票鈕。
            buy_btn = await first_visible(
                page,
                IbonSelectors.ACTIVITY_DETAILS_BUY_BTN,
                timeout_ms=self.timeout_ms,
                telemetry=self.telemetry,
            )
            if buy_btn is None:
                return False
            await buy_btn.click()
            with contextlib.suppress(Exception):
                await page.wait_for_url("**/UTK0201_000.aspx*", timeout=self.timeout_ms)

        return True

    async def _resolve_session_url(
        self, event_url: str, session_preference: str | None
    ) -> str | None:
        from adapters.ticketing.ibon.resolver import IbonEventResolver

        activity_id = urlsplit(event_url).path.rstrip("/").split("/")[-1]
        resolver = IbonEventResolver()
        try:
            sessions = await resolver.fetch_sessions(activity_id)
        except Exception:
            return None

        buyable = [
            s
            for s in sessions
            if s.get("purchase_url") and s.get("can_buy") and not s.get("sold_out")
        ]
        if not buyable:
            return None
        if session_preference:
            for session in buyable:
                haystack = f"{session.get('name') or ''} {session.get('display_date') or ''}"
                if session_preference in haystack:
                    return str(session["purchase_url"])
        return str(buyable[0]["purchase_url"])

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
