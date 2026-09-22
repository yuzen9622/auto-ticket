# 安全性

## Local-first

這個工具沒有伺服器端。所有東西都在你自己的機器上：

- 資料庫、截圖、時間軸、憑證都在 `~/.auto-ticket/`
- API 與前端只綁 `127.0.0.1`，不對外監聽
- 除了下載 runtime（GitHub Release）與 Playwright Chromium，以及你自己要搶的票務
  網站之外，不向任何第三方送出資料
- 不做遙測、不回報使用狀況、不上傳日誌

## 憑證保管

票務平台的帳號密碼與信用卡資料存在本機的 Fernet 加密 vault：

```
~/.auto-ticket/data/credentials/
├── .vault_key        主金鑰，POSIX 權限 0600
└── ...
```

- 金鑰只存在本機，沒有備份、沒有雲端同步
- 設定物件（`ApiSettings` / `WorkerSettings`）刻意**不含任何憑證欄位**，因為
  `repr()` 一進 log 就全裸
- 遷移時只記錄檔名與位元組數，**絕不記錄金鑰內容**

**刪掉 `.vault_key` 等於永久失去 vault 內所有資料。**

### Windows 的已知限制

POSIX 的 `0600` 在 Windows 上沒有對應語意，首版依賴你家目錄的預設 ACL。
共用電腦的同機管理員帳號仍可讀取。強化（`icacls` 移除繼承）列為後續項目。

## Chrome profile 邊界

搶票借用你本機的**真** Chrome（loopback CDP，`127.0.0.1:9222`），因為 Playwright
自帶的瀏覽器過不了票務網站的人機驗證。

但用的是**專用 profile**：

```
~/.auto-ticket/chrome-profile/      唯一允許的 user-data-dir
```

工具**絕不**讀寫你日常的預設 Chrome profile：

- macOS `~/Library/Application Support/Google/Chrome`
- Windows `%LOCALAPPDATA%\Google\Chrome\User Data`

launcher 也不管理 Chrome——只在 `doctor` 檢查它存不存在，不啟動、不終止、不清理。
你日常瀏覽的 cookie、登入狀態、書籤、擴充功能都不在這個工具的作用範圍內。

## 不繞過 Gatekeeper

首版不做程式碼簽章與公證，所以 macOS 可能擋下第一次執行。CLI **不會**：

- 執行 `xattr -d com.apple.quarantine`
- 呼叫 `spctl` 或 `codesign`
- 給瀏覽器加 `--no-sandbox`

放行由你自己在「系統設定 → 隱私權與安全性」按下去。一個會自己拆掉你系統防線的
安裝器，比它想省掉的那一次點擊危險得多。這是刻意的設計，不是還沒做。

## 下載的信任鏈

```
npm registry 的套件完整性
  → 套件內 runtime-manifest.json 釘選的 sha256
    → GitHub Release 下載到的位元組
```

- sha256 一律以**套件內釘選值**為準。CLI **不會**在執行期向 GitHub API 重新取得
  digest 當真值——那等於把信任鏈的兩端接成同一個來源
- 下載是匿名的。CLI 原始碼中不出現 `GITHUB_TOKEN` / `GH_TOKEN`，也不讀取任何
  環境憑證，更不會把它們放進 request header
- 校驗失敗會刪掉暫存檔並以結束碼 `5` 中止，**不重試**——那是完整性事件，不是網路抖動
- CLI 有**零 npm runtime dependency**（`dependencies` 恆為 `{}`），供應鏈面就是
  npm registry 本身加上 Node 內建模組

## 付款一律是 mock

`payment_method` 固定為 `"mock"`。

- 不會選票、不會建立訂單、不會送出訂單、不會付款
- 任何測試與 CI 都不得執行上述動作
- 需要真實網路的 `tests/live` 預設不收集（需 `--live`），且**不進入任何 CI 或
  發版流程**

信用卡欄位存在是為了讓流程能走到付款頁**之前**的那一步，不是為了替你刷卡。

## repo 公開後的暴露面

repo 是公開的，因此以下內容本身就是公開的：

- 全部原始碼，包含 `src/adapters/ticketing/*/selectors.py` 與
  `captcha_selectors.py`
- 全部 commit 歷史與 commit 訊息
- `CHANGELOG.md`

能做的控制是：Release notes 遮蔽（`scripts/release/redact_notes.mjs`）、commit
subject 的敏感詞前置攔截（`commit-hygiene` workflow），以及把新的敏感工程筆記寫進
不進版控的 `docs/internal/`。

遮蔽器**不能**追溯性地保護已經推上去的東西。真正有效的是「不要把新的敏感細節寫進
commit subject」——見 `CONTRIBUTING.md`。

`scripts/audit_public_release.py` 會掃索引與全歷史，確認沒有資料、金鑰、本機設定
或憑證形態的字串進過版控。它綠燈是切換可見性的前置條件。

## 回報問題

發現安全問題請直接聯絡 repo 擁有者，不要開公開 issue。
