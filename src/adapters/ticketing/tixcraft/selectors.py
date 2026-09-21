"""拓元 (Tixcraft) 售票系統選擇器。

集中管理拓元購票流程中各頁面的 DOM 選擇器。
"""

from __future__ import annotations


class TixcraftSelectors:
    # 活動主頁與場次
    GAME_LIST_ROWS = "#gameList > table > tbody > tr"
    GAME_LIST_TABLE = "#gameList table"
    # 場次列的購票鈕沒有 href，網址掛在 data-href（賣完的場次則整顆鈕都不在）。
    SESSION_BUY_BUTTON = "button[data-href], a[href*='/ticket/area/']"
    ACTIVITY_DETAIL_BUY_LINK = "a[href*='/activity/game/']"

    # 區域選擇頁 (/ticket/area)
    ZONE_LINKS = ".zone a, ul.area-list a"
    ZONE_LIST_CONTAINER = ".zone, ul.area-list, #area-list"

    # 張數選擇與同意條款 (/ticket/ticket)
    TICKET_PRICE_SELECTS = "select[id*='TicketForm_ticketPrice_'], .mobile-select"
    AGREE_CHECKBOX = "#TicketForm_agree"
    CAPTCHA_IMAGE = "#TicketForm_verifyCode-image"
    CAPTCHA_INPUT = "#TicketForm_verifyCode"
    CAPTCHA_CONTAINER = "#form-ticket-ticket .verify-code, #form-ticket-ticket .captcha"
    CAPTCHA_REFRESH_BTN = "#TicketForm_verifyCode-image"  # 拓元點圖片本身即可換圖
    SUBMIT_BUTTON = "#form-ticket-ticket button[type=submit], #form-ticket-ticket input[type=submit]"

    # 特權碼／驗證碼 (/ticket/verify)
    PROMO_BOX = "#promoBox, .zone-verify"
    PROMO_INPUT = "#promoBox input[type='text'], .zone-verify input[type='text'], #checkCode"
    PROMO_SUBMIT = "#promoBox button, .zone-verify button, #submitVerifyCode"

    # 登入狀態：拓元未登入也走得完選區域與選張數，直到按下「確認張數」才被踢回
    # 登入頁。頁首同時只會出現其中一組字樣，據此在燒掉驗證碼次數前就認出來。
    LOGGED_OUT_TEXTS = ("會員登入",)
    LOGGED_IN_TEXTS = ("登出", "會員中心")

    # 售完與錯誤提示
    FAILURE_MODAL = ".modal.in, .bootbox.modal, .modal.show, #msg-modal"
    FAILURE_MODAL_CLOSE = ".modal.in button.close, .bootbox button[data-bb-handler='ok'], .modal.show button.btn-primary"
    FAILURE_MODAL_TEXT = ".modal.in .modal-body, .bootbox .modal-body, .modal.show .modal-body"
    SOLD_OUT_TEXTS = ("已售完", "售罄", "無剩餘座位", "sold out", "暫無票券")

    # Cloudflare / Turnstile
    CLOUDFLARE_CHALLENGE_TEXTS = (
        "正在執行安全驗證",
        "just a moment",
        "請啟用 javascript 與 cookie 以繼續",
        "驗證您是人類",
        "verify you are human",
        "let us know you are human",
        "let's get your identity verified",
        "identity verified",
    )
