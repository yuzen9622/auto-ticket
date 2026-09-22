# auto-ticket

自動購票研究系統。後端由 FastAPI 提供 REST 與 WebSocket 契約，獨立 Worker 以 Playwright
驅動購票流程並回報遙測，前端 `web/`（Next.js App Router）是即時監控主控台。

三個行程各自獨立：

| 行程 | Port | 職責 |
| --- | --- | --- |
| API Server | `8000` | REST（`/api/v1/*`）、WebSocket（`/ws/tasks/{task_id}`）、截圖靜態檔（`/static/screenshots/*`） |
| Worker | 無 | 認領任務、操作瀏覽器、推送狀態轉移／日誌／截圖／時鐘 |
| Web | `3000` | 儀表板、建立任務精靈、Live Console、實驗紀錄、設定 |

---

## 安裝與啟動

```bash
npx @yuzen9622/auto-ticket
```

第一次執行會下載 runtime 與 Chromium，之後啟動不再下載。API 固定 `127.0.0.1:8000`、
前端固定 `127.0.0.1:3000`，啟動後開 <http://127.0.0.1:3000>；首次請先到 `/settings`
設定憑證。

需要 **Node.js ≥ 20.10** 與 **Google Chrome**；支援 macOS 13 以上（Apple Silicon）
與 Windows 10 1803 以上 x64。首版不支援 Linux 與 Intel Mac。完整前置需求、埠位衝突處置與
macOS Gatekeeper 手動放行步驟見 [docs/INSTALL.md](docs/INSTALL.md)。

既有的 `data/` 會在首次啟動時**複製**到 `~/.auto-ticket/data/`，來源永不更動
（[docs/MIGRATION.md](docs/MIGRATION.md)）。

| 指令 | 作用 |
| --- | --- |
| `auto-ticket` | 啟動全套（等同 `start`） |
| `auto-ticket doctor` | 只讀診斷：平台、Node、`tar`、Chrome、埠、runtime、磁碟、OCR |
| `auto-ticket version` | CLI / runtime / Python / onnxruntime 版本 |
| `auto-ticket migrate --dry-run` | 預覽資料遷移計畫 |
| `auto-ticket logs api -f` | 追行程日誌 |

其他文件：[RUNTIME.md](docs/RUNTIME.md)（目錄配置、環境變數、升級回滾）、
[SECURITY.md](docs/SECURITY.md)、[RELEASING.md](docs/RELEASING.md)、
[CONTRIBUTING.md](CONTRIBUTING.md)。

---

## 本機開發

打包安裝與開發流程互不影響：下列所有環境變數未設定時都落回 repo 相對的預設值，
`pnpm run dev` 的行為與以前完全相同。

### 先決條件

- Python ≥ 3.12 與 [uv](https://docs.astral.sh/uv/)
- Node.js ≥ 20 與 pnpm（本專案以 pnpm 10.30.0 驗證）

### 快速啟動

```bash
# 1. 安裝 Python 與前端依賴
pnpm run setup

# 2. 一行拉起 API + Worker + Web
pnpm run dev
```

啟動後開啟 <http://localhost:3000>。首次執行請先到 `/settings` 設定 kktix 憑證。

Worker 預設 headless。需要人工登入（Manual Login）或想看瀏覽器畫面時，改用：

```bash
pnpm run dev:worker:headed
```

---

## 環境變數

後端變數皆為選填，未設定時使用預設值。

| 變數 | 作用 | 預設 |
| --- | --- | --- |
| `AUTO_TICKET_VAULT_KEY` | 憑證加密金鑰。**未設定時 vault 為鎖定狀態**，`PUT /accounts/{platform}/credentials` 會回 `409 vault_locked` | 無（vault 停用） |
| `AUTO_TICKET_DB_PATH` | SQLite 資料庫路徑 | `data/auto-ticket.db` |
| `AUTO_TICKET_SCREENSHOT_DIR` | 截圖存放目錄 | `data/screenshots` |
| `AUTO_TICKET_VAULT_ROOT` | vault 檔案根目錄 | `data/credentials` |
| `AUTO_TICKET_TIMELINE_DIR` | 任務時間軸存放目錄 | `data/timelines` |
| `AUTO_TICKET_BROWSER_PROFILE_ROOT` | Playwright persistent context 根目錄 | `.browser_profiles` |
| `AUTO_TICKET_CORS_ORIGINS` | API 允許的來源，逗號分隔 | 見 `src/api/settings.py` |
| `NEXT_PUBLIC_API_BASE_URL` | 前端呼叫的 API base URL | `http://127.0.0.1:8000` |
| `NEXT_PUBLIC_WS_BASE_URL` | 前端連線的 WebSocket base URL | `ws://127.0.0.1:8000` |

前端變數請複製 `web/.env.example` 為 `web/.env.local` 後修改；兩者在 `web/lib/config.ts`
都有程式內預設值，不建立此檔也能直接啟動。

---

## 常用指令

| 指令 | 作用 |
| --- | --- |
| `pnpm run setup` | `uv sync` + 前端依賴安裝 |
| `pnpm run dev` | 同時啟動 API、Worker、Web |
| `pnpm run dev:api` | 只啟動 API Server（`127.0.0.1:8000`） |
| `pnpm run dev:worker` | 只啟動 Worker（headless） |
| `pnpm run dev:worker:headed` | 只啟動 Worker（顯示瀏覽器視窗） |
| `pnpm run dev:web` | 只啟動前端 dev server |
| `pnpm run build` | 建置前端 production bundle |
| `pnpm run test` | 後端 pytest |
| `pnpm run test:web` | 前端 vitest |
| `pnpm run typecheck` | 前端 `tsc --noEmit` |
| `pnpm run lint` | 前端 ESLint |
| `pnpm run invariants` | 不變式守門員 G1–G32 |
| `pnpm run check` | 以上全部依序執行（含前後端契約比對） |

---

## Mock 付款模式

**本系統不會發生真實付款。** `payment_method` 固定為 `"mock"`，API 對任何其他值直接回
`400`。前端不提供、也不接受任何信用卡欄位（卡號、持卡人、有效期限、CVV 一律不存在於
API 與前端路徑）。Live Console 與建立精靈的確認步驟都以橘色徽章標示
「MOCK 測試模式 — 不會發生真實付款」。

---

## Local-first 個資聲明

- **常用聯絡人**（姓名／電話／Email）存在瀏覽器 `localStorage`，只在你這台機器上，
  不會上傳到任何第三方。可在 `/settings` 逐筆刪除或一次全部清除。
- **身分證字號永不寫入瀏覽器**。它只活在表單的 React state，隨本次送出後即隨頁面卸載消失
  （`web/lib/local-profile.ts` 採白名單序列化，並有專屬單元測試把關）。
- **帳號與 access_key 永不進 `localStorage`**，只經
  `PUT /api/v1/accounts/{platform}/credentials` 送進後端加密 vault，送出後前端立即清空欄位。
- 唯讀檢視與表格中的敏感欄位一律顯示遮罩值；帳號遮罩由後端產生，前端不還原。
- 前端**不含**任何 analytics、error-reporting 或 session-replay SDK。

---

## 疑難排解

**儀表板顯示「Worker 離線」**
`/healthz` 的 `worker_seen_at` 為 `null`（從未上線）或超過 60 秒未更新。確認 Worker 行程仍在
執行；`pnpm run dev` 的 `worker` 欄位會顯示它的輸出。Worker 與 API 必須指向同一個
`AUTO_TICKET_DB_PATH`，否則彼此看不到對方。

**儲存憑證時回 `409 vault_locked`**
未設定 `AUTO_TICKET_VAULT_KEY`。設定後重啟 API Server 再儲存：

```bash
export AUTO_TICKET_VAULT_KEY="<your-key>"
pnpm run dev:api
```

**瀏覽器 Console 出現 CORS 錯誤**
API 的允許來源清單在 `src/api/settings.py` 的 `DEFAULT_CORS_ORIGINS`，預設含
`127.0.0.1` 與 `localhost` 的 `3000`／`5173`。要從其他 origin 存取，設定
`AUTO_TICKET_CORS_ORIGINS`（逗號分隔）即可。另請確認前端
`NEXT_PUBLIC_API_BASE_URL` 與實際 API 位址一致（`localhost` 與 `127.0.0.1` 是不同 origin）。

**Live Console 連線狀態停在「重連中」**
API Server 未啟動或已離線。WebSocket 會以 500ms → 8s 指數退避自動重試，API 回來後自動恢復；
重連時伺服器會回放最近 50 筆訊息，前端以 `outbox_id` 去重，日誌不會出現重複列。

**截圖破圖**
截圖由 API（`:8000`）而非前端（`:3000`）提供。確認 API 仍在執行，且
`AUTO_TICKET_SCREENSHOT_DIR` 與 Worker 實際寫入的目錄相同。
