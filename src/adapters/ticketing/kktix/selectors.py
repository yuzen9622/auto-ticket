from __future__ import annotations

import re

_PLAYWRIGHT_ONLY_MARKERS = (":has-text(", ":visible", ">>", ":text(", ":text-is(")


class KKTIXSelectors:
    """KKTIX 驗證 Selector 註冊表（依網頁生命週期分類）"""

    # =========================================================================
    # 1. 公開活動主頁 (https://<org>.kktix.cc/events/<slug>)
    # =========================================================================
    EVENT_TITLE = ".header-title h1, h1.event-title"
    EVENT_ORGANIZER = ".organizers a, .organizer-name"
    EVENT_SALE_TIME = ".event-info .timezoneSuffix, .period-time .time"
    EVENT_BUY_LINK = [
        ".order-now-section a.btn-point",
        "a.btn-point:has-text('立即購票')",
        "a.btn-point:has-text('下一步')",
        "#order-now a",
    ]
    EVENT_TICKET_TABLE_ROWS = "div.tickets table tbody tr"

    # -------------------------------------------------------------------------
    # 1b. 主頁 metadata 離線解析專用（offline metadata parsing）
    # -------------------------------------------------------------------------
    # JSON-LD 結構化資料（event_start_at 的主要來源）
    EVENT_JSONLD_SCRIPT = "script[type='application/ld+json']"
    # 活動詳情描述（event_start_at 的後備來源）
    EVENT_DESCRIPTION = ".event-description, #eventDescription, .description"
    # 票券表格中的販售時間區間（sale_start_at 的主要來源）
    # 注意：一列可能有 2 個 .time（起始 / 結束），取索引 0 為販售起始
    EVENT_TICKET_PERIOD_TIMES = "td.period .time"
    EVENT_TICKET_ROW_NAME = "td.name, .ticket-name"
    EVENT_TICKET_ROW_PRICE = "td.price, .ticket-price"
    EVENT_TICKET_ROW_STATUS = "td.status, .ticket-status, td.period"

    # =========================================================================
    # 2. 購票登記頁 (https://kktix.com/events/<slug>/registrations/new)
    # =========================================================================
    REGISTRATION_APP = "#registrationsNewApp"

    # 票種單元 (支援 table 與 div 清單兩種模板)
    TICKET_UNIT = [
        ".ticket-list .ticket-unit",
        "tr[id^='ticket_']",
        ".display-table",
    ]
    TICKET_NAME = ".ticket-name, td.name"
    TICKET_PRICE = ".ticket-price, td.price"

    # 加號按鈕 (觸發 AngularJS quantityBtnClick)
    TICKET_PLUS_BTN = [
        "button.btn-default.plus",
        "button[ng-click*='quantityBtnClick(1)']",
        "button:has-text('+')",
    ]
    TICKET_MINUS_BTN = [
        "button.btn-default.minus",
        "button[ng-click*='quantityBtnClick(-1)']",
        "button:has-text('-')",
    ]
    TICKET_QUANTITY_INPUT = "input.ticket-quantity, input[type='number']"

    # 同意條款 Checkbox (必須 dispatch click 事件以驅動 AngularJS Model)
    TERMS_CHECKBOX = [
        "#person_agree_terms",
        "input[name='agree_terms']",
        "label:has-text('我同意') input",
    ]

    # 防機器人問答題 (Custom Quiz / Captcha)
    CAPTCHA_CONTAINER = ".custom-captcha-inner, div[ng-if*='captcha']"
    CAPTCHA_QUESTION_TEXT = ".custom-captcha-inner p, .custom-captcha-inner"
    CAPTCHA_INPUT = [
        "input[name='captcha_answer']",
        "input#captcha_answer",
        "input[placeholder*='答案']",
    ]

    # 配位與下一步按鈕 (AngularJS challenge 動作)
    BTN_BEST_AVAILABLE = [
        "button[ng-click='challenge(1)']",  # 電腦配位（優先）
        "button.btn-primary:has-text('電腦配位')",
    ]
    BTN_PICK_SEAT = [
        "button[ng-click='challenge()']",  # 自行選位
        "button.btn-primary:has-text('選位')",
    ]
    BTN_NEXT_STEP = [
        "div.register-new-next-button-area button:not([disabled])",
        "button.btn.btn-primary.btn-lg.ng-isolate-scope:not([disabled])",
        "button[type='submit']:has-text('下一步')",
        "button:has-text('下一步')",
    ]

    # =========================================================================
    # 3. 劃位與訂單填寫頁 (https://kktix.com/events/<slug>/registrations/<order_id>)
    # =========================================================================
    ORDER_COUNTDOWN_NOTICE = "div[ng-switch-when='countingDown']"
    RESELECT_TICKET_LINK = "a.reselect-ticket"

    # 聯絡人表單 (Contact Fields)
    CONTACT_NAME = [
        "input[name='contact[name]']",
        "input#order_contact_name",
        "input[name*='contact_name']",
    ]
    CONTACT_EMAIL = [
        "input[name='contact[email]']",
        "input#order_contact_email",
        "input[name*='contact_email']",
    ]
    CONTACT_PHONE = [
        "input[name='contact[phone]']",
        "input#order_contact_phone",
        "input[name*='contact_phone']",
    ]

    # 實名制參加人欄位 (Attendee Fields - 支援複數參加者 attendees[0], attendees[1])
    ATTENDEE_NAME_TEMPLATE = "input[name='attendees[{index}][name]']"
    ATTENDEE_PHONE_TEMPLATE = "input[name='attendees[{index}][phone]']"
    ATTENDEE_ID_TEMPLATE = [
        "input[name='attendees[{index}][id_number]']",
        "input[name*='field_idnumber']",
    ]

    # 送出訂單按鈕
    BTN_CONFIRM_ORDER = [
        "[ng-click='confirmOrder()']",
        "button[type='submit']:has-text('確認表單')",
        "button:has-text('確認表單資料')",
    ]

    # =========================================================================
    # 4. 信用卡付款頁面 (Credit Card Payment)
    # =========================================================================
    PAYMENT_RADIO_CREDIT_CARD = [
        "input[type='radio'][value*='credit_card']",
        "label:has-text('信用卡') input",
    ]
    CARD_NUMBER_INPUT = [
        "input#card-number",
        "input[name*='card_number']",
        "input[name='pan']",
    ]
    CARD_EXPIRY_INPUT = [
        "input#card-expiry",
        "input[name*='card_expiry']",
        "input[name='expiration']",
    ]
    CARD_CVV_INPUT = [
        "input#card-ccv",
        "input#card-cvv",
        "input[name*='card_cvv']",
        "input[name='cvc']",
    ]
    BTN_CONFIRM_PAYMENT = [
        "button#submit-payment",
        "button:has-text('確認付款')",
        "button:has-text('立即付款')",
    ]

    # =========================================================================
    # 5. 防機器人與驗證偵測 (Anti-Bot / Cloudflare Challenge)
    # =========================================================================
    CLOUDFLARE_CHALLENGE_TEXTS = [
        "正在執行安全驗證",
        "just a moment",
        "請啟用 javascript 與 cookie 以繼續",
        "驗證您是人類",
    ]


KKTIX_EVENT_URL_RE = re.compile(
    r"^https?://(?P<org>[\w-]+)\.kktix\.cc/events/(?P<slug>[\w.\-]+)"
)


def css_only(selectors: str | list[str]) -> list[str]:
    """Keep only selectors that soupsieve can parse (drop Playwright-specific syntax)."""
    raw = selectors if isinstance(selectors, list) else [selectors]
    result: list[str] = []
    for group in raw:
        for part in group.split(","):
            candidate = part.strip()
            if not candidate:
                continue
            if any(marker in candidate for marker in _PLAYWRIGHT_ONLY_MARKERS):
                continue
            result.append(candidate)
    return result
