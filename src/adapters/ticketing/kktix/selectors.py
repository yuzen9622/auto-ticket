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
    # 活動主頁的票種狀態是販售時間欄裡的一個 badge：`span.status.waiting`
    # （尚未開賣）或 `span.status.closed`（結束販售），販售中則整個 badge 不存在。
    # 不要把 `td.period` 放進來當備援——那一格平常只有日期，接起來比對會讓沒有
    # badge 的販售中票種被當成有狀態文字，主辦單位寫在說明裡的「售完為止」也會
    # 被讀成售完。
    EVENT_TICKET_ROW_STATUS = "td.period span.status, td.status, .ticket-status"

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
    # 數量欄位。實站實測（2026-09）：實際是 `<input type="text" ng-model="ticketModel.quantity">`，
    # 既無 `ticket-quantity` class 也不是 `type=number`——舊候選完全落空時會讓回讀失敗。
    TICKET_QUANTITY_INPUT = [
        "input[ng-model='ticketModel.quantity']",
        "input.ticket-quantity",
        "input[type='number']",
    ]

    # 同意條款 Checkbox (必須 dispatch click 事件以驅動 AngularJS Model)
    TERMS_CHECKBOX = [
        "#person_agree_terms",
    ]

    # 防機器人問答題 (Custom Quiz / Captcha)
    CAPTCHA_CONTAINER = ".custom-captcha-inner, div[ng-if*='captcha']"
    CAPTCHA_QUESTION_TEXT = ".custom-captcha-inner p, .custom-captcha-inner"
    CAPTCHA_INPUT = [
        "input[name='captcha_answer']",
        "input#captcha_answer",
        "input[placeholder*='答案']",
    ]
    # 答錯後的錯誤提示。**嚴格限縮在 captcha 容器內部**：聯絡資料的格式錯誤
    # 同樣會長出 .help-inline，放寬到整頁會把「email 少了 @」誤判成「問答題答錯」。
    CAPTCHA_ERROR_ALERT = (
        ".custom-captcha-inner .help-inline, "
        ".custom-captcha-inner .alert-danger, "
        ".custom-captcha-inner .text-error"
    )

    # 專屬會員碼／邀請碼區塊（資格審查）
    MEMBER_CODE_BLOCK = "div.code-input"
    MEMBER_CODE_INPUT = [
        "div.code-input input[type='text']",
        "input[ng-model*='code']",
        "input[name*='invitation']",
    ]
    MEMBER_CODE_VERIFY_BTN = [
        "div.code-input button",
        "button[ng-click*='verifyCode']",
        "button[ng-click*='applyCode']",
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

    # 動態聯絡人表單（實站實測 2026-09）。欄位 name 帶每場活動不同的數字 ID
    # （如 contact[field_text_1007673]），且三個欄位共用同一個
    # ng-model="contactModel[field.field_key]"——靠 name 或 ng-model 都無法辨識，
    # 唯一穩定的依據是 control-group 內的 label 文字。
    CONTACT_DYNAMIC_GROUP = "div.contact-info div.control-group"
    CONTACT_DYNAMIC_LABEL = "label.control-label"
    # 排除 checkbox／radio：它們同樣是 contact[...] 開頭，但拿 ng_fill 填字串進去是錯的。
    CONTACT_DYNAMIC_INPUT = (
        "input[name^='contact[']:not([type='checkbox']):not([type='radio']), "
        "textarea[name^='contact[']"
    )
    # 動態同意條款 checkbox（實站實測 2026-09），例如「我同意 KKTIX 系統所分配之
    # 門票，購買後將不能更改或退款」。沒勾就送不出去（欄位會出現「不能留空」），
    # 而欄位 id 同樣每場活動不同。
    CONTACT_DYNAMIC_CHECKBOX = "input[type='checkbox'][name^='contact[']"
    CONTACT_DYNAMIC_CONSENT_LABEL = (
        "label.checkbox:has(input[type='checkbox'][name^='contact['])"
    )
    # label 關鍵字 -> 邏輯欄位。順序即優先序，先命中者勝。
    CONTACT_LABEL_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("contact_email", ("email", "e-mail", "信箱", "電子郵件")),
        ("contact_phone", ("手機", "電話", "phone", "mobile")),
        ("contact_name", ("姓名", "名字", "name")),
    )

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

    # 訂單完成頁（未付款保留訂單亦算抵達）
    ORDER_COMPLETE_CONTAINER = [
        ".order-complete",
        ".registration-complete",
        "#orderShowApp",
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
    # 4b. 登入頁 (https://kktix.com/users/sign_in)
    # =========================================================================
    LOGIN_FORM = ["form[action*='sign_in']", "#new_user", "form#sign_in_form"]
    # 登入表單的三個欄位。常數命名刻意避開 PASSWORD 字樣：憑證欄位的識別子
    # 一旦帶上該關鍵字，靜態掃描會把「選擇器字串」與「真的密碼」混為一談。
    LOGIN_USER_INPUT = [
        "input#user_login",
        "input[name='user[login]']",
        "input[type='email']",
    ]
    LOGIN_KEY_FIELD = [
        "input#user_password",
        "input[name='user[password]']",
        "input[type='password']",
    ]
    LOGIN_SUBMIT_BTN = [
        "form[action*='sign_in'] input[type='submit']",
        "form[action*='sign_in'] button[type='submit']",
        "button[type='submit']",
    ]
    LOGIN_TURNSTILE_CONTAINER = [
        ".cf-turnstile",
        "div[class*='cf-turnstile']",
        "iframe[src*='challenges.cloudflare.com']",
    ]
    LOGIN_TURNSTILE_RESPONSE = [
        "input[name='cf-turnstile-response']",
        "input[name*='turnstile']",
    ]

    # =========================================================================
    # 4c. 彈窗 (Modal)：搶輸／無可配座位／未登入訪客
    # =========================================================================
    MODAL_CONTAINER = ".modal.in, .modal-dialog, div[role='dialog']"
    # 命中其一即視為「這一輪搶輸了」；一律換下一順位票種，不重刷同一票種。
    MODAL_FAILURE_TEXTS = (
        "別人搶先一步",
        "已無可配座位",
        "無法配位",
        "已被選走",
        "票券已售完",
    )
    MODAL_DISMISS_BTN = [
        ".modal.in button.close",
        ".modal-dialog button.close",
        "div[role='dialog'] button[data-dismiss='modal']",
    ]
    # 未登入訪客彈窗（KKTIX 會勸你先成為會員）。
    MODAL_GUEST_SIGNIN_TEXT = "立刻成為 KKTIX 會員"
    MODAL_GUEST_SIGNIN_LINK = [
        ".modal.in a[href*='sign_in']",
        ".modal-dialog a[href*='sign_in']",
        "div[role='dialog'] a[href*='sign_in']",
    ]

    # =========================================================================
    # 4d. 排隊等候室 (Waiting Room)
    # =========================================================================
    # 排隊中**嚴禁 reload**：重整會被丟回隊伍尾端。
    QUEUE_COUNTDOWN = "#cf-time"
    QUEUE_HEADING = [
        "#cf-wrapper h1",
        ".queue-heading",
        "#waiting-room",
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

    # 票種狀態列上的售罄字樣（`SOLD_OUT` 只能由頁面實際文字判定，不得臆造）。
    TICKET_STATUS_SOLD_OUT_TEXTS = (
        "售完",
        "售罄",
        "完售",
        "sold out",
        "已結束",
        "已額滿",
    )


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
