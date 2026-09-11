# 架構註記：競速先決驅動層（Race-First Driver）

本文件補充 `docs/SDD.md` §3.3 / §3.4 / §3.8，記錄驅動層由「線性推進」改為
「狀態偵測迴圈」之後的契約。SDD 是規格，本文件是**實際落地的合約細節**；
兩者衝突時以本文件為準。

## 1. 分層與責任

| 層 | 模組 | 責任 | 硬性邊界 |
| :--- | :--- | :--- | :--- |
| 選擇器註冊表 | `adapters/ticketing/kktix/selectors.py` | 所有 CSS/Playwright 選擇器的唯一真相 | adapter 內不得出現選擇器字面值（G22） |
| DOM 操作 | `adapters/ticketing/kktix/dom.py` | AngularJS 相容的點擊／填值／探測 | 不做任何決策 |
| Adapter | `adapters/ticketing/kktix/adapter.py` | 讀頁面成快照、套用決策、偵測 `PageState` | 不持有流程狀態 |
| 策略 | `strategy/` | 純函式決策（只吃快照） | 禁止 import playwright、禁止出現 `Page`/`Locator`（G27） |
| FSM | `fsm/machine.py` | 歷程記錄與架構審計 | 終態不可覆寫、不可復活 |
| 驅動層 | `purchase/orchestrator.py` | 反應式迴圈、動作進度、單次付款鎖 | 所有失敗往外拋，交由排程器 fail-closed |

## 2. `PageState` 判定優先序

`detect_page_state` 只依真實 DOM/URL 物理特徵判定，**不臆造 `SOLD_OUT`**
（售罄是策略層讀完票種快照後的結論，不是頁面狀態）。

| # | 狀態 | 判定 | 動作 |
| :---: | :--- | :--- | :--- |
| 1 | `FAILURE_MODAL` | `MODAL_CONTAINER` 可見且含 `MODAL_FAILURE_TEXTS` | 關閉彈窗、排除該票種、換下一順位 |
| 2 | `GUEST_MODAL` | `MODAL_CONTAINER` 可見且含 `MODAL_GUEST_SIGNIN_TEXT` | 共用登入 helper，失敗即停 |
| 3 | `QUEUE` | `QUEUE_COUNTDOWN` 或 `QUEUE_HEADING` | 靜態等待，**嚴禁 reload** |
| 4 | `COMPLETED` | URL 含 `/orders/` 且不含 `/payment`，或 `ORDER_COMPLETE_CONTAINER` | 結束迴圈 |
| 5 | `PAYMENT_REQUIRED` | URL 含 `/payment` 或 `PAYMENT_RADIO_CREDIT_CARD` | 付款（單次鎖），結束迴圈 |
| 6 | `QUALIFICATION_CODE` | `MEMBER_CODE_BLOCK` 且 `MEMBER_CODE_INPUT` 可見 | 填碼並驗證（單次鎖） |
| 7 | `FORM_FILLING` | `ORDER_COUNTDOWN_NOTICE` / `CONTACT_DYNAMIC_GROUP` / `CONTACT_NAME` / `BTN_CONFIRM_ORDER` | 填表、作答、送出 |
| 8 | `VERIFICATION_CHALLENGE` | `CAPTCHA_CONTAINER` + `CAPTCHA_INPUT`，且無表單欄位 | 抽題作答回填 |
| 9 | `SEAT_SELECTION` | `REGISTRATION_APP` 且已選數量 > 0 | 電腦配位或下一步 |
| 10 | `TICKET_SELECTION` | `REGISTRATION_APP` 且已選數量 == 0 | 讀快照交給 `decide_ticket()` |
| 11 | `UNKNOWN` | 以上皆非 | 微等待，超時 fail-closed |

「表單頁優先於局部驗證題」與「以已選數量區分選票／劃位」是解除重疊的兩條關鍵規則。

## 3. 票種降級鏈路

遇 `FAILURE_MODAL` 時的順序**不可調換**：

1. `dismiss_failure_modal(page)`
2. 把當前 `_current_ticket_name` 加入排除集合
3. 重置 `_current_ticket_name` / `_seat_action_taken` / `_form_submitted`
4. `reset_ticket_quantities(page)` 歸零並**逐欄回讀確認為 `"0"`**；失敗立即拋例外中止
5. 重讀快照、以 `excluded_names` 重新決策；無票才送 `all_tickets_unavailable`

降級時只送觀察者事件 `ticket_fallback_reselected`（經 `sync_to_state`），
**不送** `retry_fallback_ticket`——後者會累加 `current_priority_index`，
而「同一 priority 底下換一張同價票」並沒有用掉一個優先序。

## 4. 憑證衛生

- 帳密只從環境變數 `AUTO_TICKET_KKTIX_USERNAME` / `AUTO_TICKET_KKTIX_PASSWORD` 讀入，
  只存在記憶體，不寫入任何檔案。
- 登入期間暫時卸除 screenshot hook（防截圖屏障），失敗時先清空密碼欄位再拋例外。
- G29 以 AST 掃描 `src/` 與 `scripts/` 全域，禁止敏感名稱流入 log／telemetry／
  例外訊息／`write_text` / `write_bytes` / `screenshot` 等 sink。
- 選擇器常數命名避開 `PASSWORD` 字樣（`LOGIN_KEY_FIELD`），避免靜態掃描把
  選擇器字串誤判為硬編碼憑證。
