# 購票控制與流程修復驗證（2026-10-08）

本次修復涵蓋提出的 8 項 P1 與 2 項 P2。未提交 commit、未發布版本；驗證使用獨立資料庫，不修改原本執行於 3000／8000 的應用資料。

## 修復與回歸證據

| 問題 | 修復後行為 | 主要證據 |
| --- | --- | --- |
| P1 階段中途緊急停止 | 取消執行中的階段，喚醒階段間等待，收斂排程；worker 將任務和 job 標成 CANCELLED。取消不再被畫面當成失敗，終態不再顯示控制列。 | `test_worker_control.py` 的執行中、inline、預約與階段間停止；真實 Chrome 三種取消流程與儀表板操作 |
| P1 背景工作阻塞購票 | 每輪優先領取到期 PURCHASE；背景登入／補票況先取消、排空後才開始購票，避免共用 Chrome 被晚到的導航干擾。背景工作保留 `interrupted_for_purchase` 原因及重新執行說明。 | `test_due_purchase_preempts_background` 同 profile、單一容量的登入與補票況兩例 |
| P1 supervisor 漏接啟動退出 | spawn 後立即監聽退出與錯誤，整段啟動共用失敗訊號；worker 在自身就緒前或 Web 啟動期間退出都會清理已啟動子行程並回報失敗。 | CLI `supervisor.test.mjs` 新增兩例；原有啟動／重啟測試全通過 |
| P1 KKTIX 重送舊錯答 | 僅在驗證區域有拒絕提示時清除原答案，再呼叫 provider；沒有拒絕提示的人工輸入仍保留。 | `test_rejected_answer_is_solved_again` 與原有人工答案保留測試 |
| P1 獨立驗證缺少下一步 | 填答後發出人工送出提示並等待；沒有解答時提示人工填答。網站尚未接受答案前不提前宣告等待付款，也不把獨立驗證當成訂單已提交。 | 單元測試及真實 Chrome 的等待、取消、人工完成後續行 |
| P1 尚未實作的自行選位 | same_zone／specific_zone 進入自行選位時明確交接；不再把點入口回報為完成選座，不自動降級成任意電腦配位。只有自行選位按鈕的頁面也交接。 | 座位 adapter 測試、協調器交接測試及真實 Chrome 的取消與續行 |
| P1 固定 120 秒 | 預設採用任務 timeout_seconds；整段購票包括卡住的 adapter 操作與人工等待均受逾時控制。 | 600 秒任務可通過模擬 130 秒排隊、120 秒反例逾時、blocked adapter 被取消且未付款 |
| P1 任務與入列非原子 | TaskRepository.create 與 broker.enqueue 共用同一個 DB session／交易。 | 入列前故障與 job flush 後故障注入，都不留下任務或 job |
| P2 HTTP Date 整秒誤校時 | HTTP Date 加上不確定度並僅供診斷；NTP 不可用時保留既有校時或本機時鐘，不套用整秒偏移。 | 900ms 量化反例不再延後觸發目標；時鐘及 scheduler 全套回歸 |
| P2 50 筆回放遺失提示 | 回放耐久事件、最新控制狀態與最新時鐘；人工提示與解除提示另行保留，重連先清掉舊提示再還原。 | 80 筆時鐘洪流、80 筆耐久日誌洪流、解除提示回放、前端 hook 測試，以及實際頁面重整 |

## 完整檢查

`pnpm run check` 成功，包含：

- Python：1195 tests passed。
- Web：23 files、243 tests passed。
- CLI：13 files、158 tests passed。
- invariant guards、TypeScript、Web ESLint、前後端 contract check 全部通過。
- ESLint 有 6 項既有 warning，無 error；Python 有 2 項相依套件 deprecation warning。

`pnpm run build` 成功；最後的終態控制列調整另經 TypeScript 與建置驗證。`git diff --check` 通過。

Python 額外 Ruff 掃描不是本專案 `check` 的門檻；掃描觸及舊碼的 UTC alias、例外處理等既有問題，沒有將整個 repo 宣稱為 Ruff 零問題。

## 真實應用與瀏覽器

實際 API：`http://127.0.0.1:18000`。建置後 standalone 儀表板：`http://127.0.0.1:13000`。

使用真實 SQLite、API、WorkerLoop、購票 handler、ControllableScheduler、PurchaseOrchestrator、KKTIXAdapter 與有視窗的 Chrome。只有售票頁網路回應改由本機 HTML fixture 提供，校時網路取樣關閉。沒有向售票站送出訂單、占位或付款。

可重現腳本：`tests/browser/smoke_purchase_controls.py`。腳本需接到獨立的 QA API，並使用同一個 QA 資料夾；不可對個人／正式資料庫執行。

```sh
uv run python tests/browser/smoke_purchase_controls.py --root /tmp/auto-ticket-qa-20261008
```

最後一輪五條流程全部通過：

| 流程 | task id | 結果 |
| --- | --- | --- |
| 獨立驗證等待中取消 | 1fccd9bc5abe445d | task 與 job 均 CANCELLED |
| 自行選位等待中取消 | 4fc5a5101ff04aee | task 與 job 均 CANCELLED |
| 排隊中取消 | 71d398bdc6f748b7 | task 與 job 均 CANCELLED |
| 人工完成獨立驗證 | 3134aad5d98f4680 | task COMPLETED、job DONE |
| 人工完成自行選位 | e2c88e666f984097 | task COMPLETED、job DONE |

另以 agent-browser 操作儀表板：人工提示出現後累積大量時鐘事件，重新整理仍可見提示；點「緊急停止」及確認後任務變為已取消。正式建置頁面確認取消結果一致，終態控制列已隱藏。

證據保留於 `/tmp/auto-ticket-qa-20261008/`：`browser-results.json`、`browser-final.log`、`dashboard-gate.png`、`dashboard-stopped.png`、`standalone-cancelled.png` 與各 fixture 截圖。完整測試與建置紀錄為 `/tmp/auto-ticket-check-final.log`、`/tmp/auto-ticket-build.log`。

## 驗證界線

指定區域與自行選位仍需人工操作，沒有宣稱已實作自動座位圖。獨立驗證頁採人工送出交接。NTP 不可用時沒有精確校時保證。已到達售票站的請求無法透過本機停止回溯撤銷。

未驗證實際售票站登入、付費訂單、支付結果、打包發布或多行程 worker 的共用瀏覽器隔離。此次採主代理自行審查，沒有獨立審查者。既有歷史孤立任務未做資料遷移；原子交易修復防止新增此類孤立任務。
