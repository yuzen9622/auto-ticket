"""ibon 售票系統選擇器。

集中管理 ibon (ASP.NET WebForms) 購票流程中各頁面的 DOM 選擇器。
"""

from __future__ import annotations


class IbonSelectors:
    # 活動與場次
    ACTIVITY_DETAILS_BUY_BTN = "a.btn-buy, a[href*='UTK0201_000'], a[href*='PERFORMANCE_ID'], .buy-tickets a"

    # 區域選擇頁 (UTK0201_000.aspx)
    ZONE_TABLE = "#ctl00_ContentPlaceHolder1_DataGrid1, #ctl00_ContentPlaceHolder1_divArea table, table.table"
    ZONE_ROWS = "#ctl00_ContentPlaceHolder1_DataGrid1 tbody tr, table.table tbody tr"
    ZONE_BUY_BUTTONS = "a[id*='btnBuy'], [id$='btnBuy'], table.table a.btn, input[value*='選購']"
    ZONE_CONTAINER = "#ctl00_ContentPlaceHolder1_divArea, #ctl00_ContentPlaceHolder1_DataGrid1"

    # 張數、座位與驗證頁 (UTK0201_001.aspx)
    TICKET_SELECTS = "select[id*='ddlAmount'], [id$='ddlAmount'], table.table select"
    NON_ADJACENT_SEAT_CHECKBOX = "#ctl00_ContentPlaceHolder1_chkSeat, [id$='chkSeat']"
    AGREE_CHECKBOX = "#ctl00_ContentPlaceHolder1_chkAgree, [id$='chkAgree']"
    CAPTCHA_IMAGE = "#ctl00_ContentPlaceHolder1_imgVerify, [id$='imgVerify'], #imgVerify"
    CAPTCHA_INPUT = "#ctl00_ContentPlaceHolder1_txtVerify, [id$='txtVerify'], #txtVerify"
    CAPTCHA_CONTAINER = ".captcha-container, #captcha, tr:has([id$='imgVerify'])"
    SUBMIT_BUTTON = "#ctl00_ContentPlaceHolder1_btnNext, [id$='btnNext'], input[value*='下一步']"

    # 特權碼／資格審查 (UTK0201_0.aspx)
    PROMO_INPUT = "#ctl00_ContentPlaceHolder1_txtCode, [id$='txtCode'], #txtCode"
    PROMO_SUBMIT = "#ctl00_ContentPlaceHolder1_btnCheck, [id$='btnCheck'], #btnCheck"

    # 售完與錯誤提示
    FAILURE_MODAL = ".modal.show, .modal.in, #myModal, .sweet-alert, div[class*='alert']"
    FAILURE_MODAL_CLOSE = ".modal.show button.close, .sweet-alert button.confirm, button[data-dismiss='modal'], button.btn-primary"
    SOLD_OUT_TEXTS = ("已售完", "售罄", "無剩餘座位", "sold out", "額滿")

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
