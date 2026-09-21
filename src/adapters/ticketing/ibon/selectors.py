"""ibon 售票系統選擇器。

實際購票流程橫跨兩個網域：`ticket.ibon.com.tw` 是 Angular SPA 的活動頁，真正的
訂購頁在 `orders.ibon.com.tw/application/UTK02/`（ASP.NET WebForms）。頁面順序是

    UTK0201_000.aspx  選擇票區（座位圖 image map，每一區是一個 <area>）
    UTK0202_.aspx     電腦配位的張數頁（自行選位則是 UTK0205_ / UTK0201_001）
    UTK0206_.aspx     購票確認與付款方式
    UTK0207_.aspx     實際付款

票區不是表格而是 `<area title="票區:… 票價：… 尚餘：…" href="javascript:Send(…)">`，
張數下拉是 WebForms 動態產生的 `…$AMOUNT_DDL`，兩者都沒有固定的 ctl00 編號，只能
用 name 片段比對。
"""

from __future__ import annotations


class IbonSelectors:
    # 活動頁（Angular SPA）的購票鈕
    ACTIVITY_DETAILS_BUY_BTN = "button.btn-buy, a.btn-buy, a[href*='UTK0201_000']"

    # 選擇票區 (UTK0201_000.aspx)：座位圖 image map
    ZONE_TABLE = "map, img[usemap], area"
    ZONE_ROWS = "area[title]"
    ZONE_BUY_BUTTONS = "area[title]"
    ZONE_CONTAINER = "map, img[usemap]"

    # 張數頁 (UTK0202_.aspx / UTK0205_.aspx / UTK0201_001.aspx)
    TICKET_SELECTS = (
        "select[name*='AMOUNT_DDL'], table.rwdtable select.form-control-sm"
    )
    NON_ADJACENT_SEAT_CHECKBOX = (
        "div.not-consecutive input[type='checkbox'], [id$='chkSeat']"
    )
    AGREE_CHECKBOX = "#agreen, [id$='chkAgree']"
    CAPTCHA_IMAGE = "img[src*='pic.aspx'], [id$='imgCHK'], [id$='imgVerify']"
    CAPTCHA_INPUT = (
        "[id$='_CHK'], input[placeholder*='驗證碼'], [id$='txtVerify']"
    )
    CAPTCHA_CONTAINER = "div.verify-code, tr:has(img[src*='pic.aspx'])"
    SUBMIT_BUTTON = (
        "[id$='AddShopingCart2'], #ticket-wrap a.btn.btn-primary, "
        "a.btn.btn-pink.continue, [id$='btnNext']"
    )

    # 驗證問題／資格審查 (UTK0201_0.aspx)
    PROMO_INPUT = (
        "#content div.form-group input[type='text'], "
        "div.editor-box input[type='text'], [id$='txtCode']"
    )
    PROMO_SUBMIT = "#content a.btn, div.editor-box a.btn, [id$='btnCheck']"

    # 購票確認與付款 (UTK0206_.aspx)
    PAYMENT_CONTACT_PHONE = "[id$='INPUT_SHOPPER_PHONE_MOBILE']"
    PAYMENT_CREDIT_CARD_RADIO = "[id$='rbPayMethodCreditCard']"
    PAYMENT_PICKUP_RADIO = "[id$='rdo_GET_METHOD']"
    PAYMENT_NEXT_BUTTON = "[id$='NEXT_BTN']"
    PAYMENT_CANCEL_BUTTON = "[id$='ibClearShoppingCart']"

    # 售完與錯誤提示
    FAILURE_MODAL = ".modal.show, .modal.in, #myModal, .sweet-alert, div[class*='alert']"
    FAILURE_MODAL_CLOSE = ".modal.show button.close, .sweet-alert button.confirm, button[data-dismiss='modal'], button.btn-primary"
    SOLD_OUT_TEXTS = ("已售完", "售罄", "無剩餘座位", "sold out", "額滿", "尚餘：0")

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
