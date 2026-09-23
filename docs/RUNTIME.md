# Runtime 版面配置與維運

## 本機目錄

一切都在 `~/.auto-ticket/`，Windows 也是 `os.homedir()` 底下的同一個位置
（**不是** `%APPDATA%`）。Python 端用 `Path.home()`、Node 端用 `os.homedir()`，
兩邊必須指到同一個根，否則兩個行程會各自對著不同的資料夾工作。

```
~/.auto-ticket/
├── data/
│   ├── auto-ticket.db (+ -wal / -shm)
│   ├── credentials/.vault_key     (POSIX 0600)
│   ├── screenshots/
│   ├── timelines/
│   └── .migrated.json             遷移標記，冪等的依據
├── chrome-profile/                真 Chrome 的 CDP user-data-dir（不得更名）
├── browser-profiles/              Playwright persistent context（live / hydrate）
├── ms-playwright/                 PLAYWRIGHT_BROWSERS_PATH
├── runtime/
│   ├── .tmp/                      下載與解壓暫存，啟動時清理
│   └── <version>/                 解壓後的 runtime
├── logs/                          api.log / worker.log / web.log / launcher.log
└── state/supervisor.json          pid、埠、版本、啟動時間
```

`chrome-profile/` 是**唯一**允許的 Chrome user-data-dir。工具絕不讀寫你日常的
預設 Chrome profile（macOS 的 `~/Library/Application Support/Google/Chrome`、
Windows 的 `%LOCALAPPDATA%\Google\Chrome\User Data`），也不啟動、不終止、不清理
你自己開的 Chrome。

## runtime artifact

`runtime/<version>/` 是從 GitHub Release 下載並驗過 sha256 的解壓結果：

```
MANIFEST.json      schema / version / target / python / onnxruntime / gitSha / builtAt / files[]
python/            可重定位 CPython 3.12
site-packages/     所有 Python 相依（含 ddddocr 的 ONNX 模型）
app/src/           應用原始碼
app/scripts/serve_api.py
app/scripts/migrate_db.py
web/               Next standalone：server.js + .next/ + .next/static/ + public/
```

**不含 Playwright 瀏覽器**。那由 `auto-ticket start` 首次執行時交給 Playwright
官方安裝器處理，落在 `~/.auto-ticket/ms-playwright`。

CLI 與 runtime **嚴格同版**：CLI 只接受 `MANIFEST.json` 的 `version` 等於自己
`package.json` 版本的 runtime，不符即以結束碼 `4` 中止。

## 三個行程

| 行程 | 指令 | 就緒條件 | 逾時 |
| --- | --- | --- | --- |
| api | `python -s <runtime>/app/scripts/serve_api.py --host 127.0.0.1 --port 8000` | `GET /healthz` 回 200 且 JSON 可解析 | 90s |
| worker | `python -s -m worker` | stdout 出現就緒 banner；無 banner 則以存活 10s 為準 | — |
| web | `node <runtime>/web/server.js`（`PORT=3000` / `HOSTNAME=127.0.0.1`） | `GET /` 回應碼 < 500 | 60s |

啟動序固定 api → worker → web，關閉反向。任一階段就緒逾時，會印出該行程日誌
末 40 行、關掉所有已啟動的行程，並以結束碼 `7` 結束。

Web 用的是**你自己的 Node**，runtime 不內嵌 Node。

## Python 啟動方式

不建 venv：`pyvenv.cfg` 與 console script 的 shebang 會把建置機的絕對路徑寫死，
搬到別人家目錄就整組失效。改為直接呼叫 `python/bin/python3`（Windows 為
`python\python.exe`），import 路徑全部由環境變數組出：

```
PYTHONPATH        = <runtime>/site-packages : <runtime>/app/src
PYTHONNOUSERSITE  = 1
PYTHONUTF8        = 1
```

一律帶 `-s`（排除 user site-packages）。**不得用 `-E`**——它會連 `PYTHONPATH`
一起忽略，import 路徑就空了。

## 環境變數

三個子行程共用這組基底，由 CLI 組出：

| 變數 | 值 |
| --- | --- |
| `AUTO_TICKET_DB_PATH` | `~/.auto-ticket/data/auto-ticket.db` |
| `AUTO_TICKET_SCREENSHOT_DIR` | `~/.auto-ticket/data/screenshots` |
| `AUTO_TICKET_VAULT_ROOT` | `~/.auto-ticket/data/credentials` |
| `AUTO_TICKET_TIMELINE_DIR` | `~/.auto-ticket/data/timelines` |
| `AUTO_TICKET_BROWSER_PROFILE_ROOT` | `~/.auto-ticket/browser-profiles` |
| `AUTO_TICKET_CORS_ORIGINS` | `http://127.0.0.1:3000,http://localhost:3000` |
| `PLAYWRIGHT_BROWSERS_PATH` | `~/.auto-ticket/ms-playwright` |

這些變數在開發流程裡**都可以不設**：未設時一律落回 repo 相對的 `data/...` 與
`.browser_profiles`，行為與以前完全相同。

另有兩個旁路，給 CI 與離線環境用：

| 變數 | 用途 |
| --- | --- |
| `AUTO_TICKET_RUNTIME_URL` | 覆寫下載 baseUrl，指向本機 http server |
| `AUTO_TICKET_RUNTIME_DIR` | 直接指向已解壓的 runtime，完全跳過下載 |

## 固定埠 8000 / 3000

首版沒有埠位旗標，理由見 [INSTALL.md](INSTALL.md)。被佔用時 `start` 以結束碼 `10`
中止並具名報出佔用者；`doctor` 跑同一份檢查但只報告、不中止。

## 日誌

每個子行程的 stdout/stderr 同時做兩件事：加上 `[api]` / `[worker]` / `[web]` 前綴
轉發到終端機，以及 append 進 `~/.auto-ticket/logs/<name>.log`。

**首版沒有輪替**。啟動時若單一檔案超過 50MB，只會在終端機與 `launcher.log` 印一行
告警並提示手動刪除；不會自動改名、不會自動刪除。日誌可以安全地手動刪掉：

```bash
rm ~/.auto-ticket/logs/*.log
```

## 升級與回滾

升級就是用原本的套件管理工具重裝最新版，例如 `npm install -g @yuzen9622/auto-ticket@latest`
（pnpm：`pnpm add -g @yuzen9622/auto-ticket@latest`；Yarn：`yarn global add @yuzen9622/auto-ticket@latest`），
下次啟動會下載對應版本的 runtime。

回滾同理：`npm install -g @yuzen9622/auto-ticket@<舊版>`。該版的 runtime 若還在本機就秒開，
否則重新下載那個 tag 的 asset。**已發佈的 Release asset 不會被刪除或重傳**——重傳
會讓已發佈 npm 套件裡釘選的 sha256 對不上，那是發版鐵律（見 RELEASING.md）。

舊版 runtime 目錄**一律保留**，首版沒有自動清理。查看佔用：

```bash
auto-ticket runtime list      # 只讀列出本機版本與可回收大小
```

要回收就手動刪目錄，那是安全的：

```bash
rm -rf ~/.auto-ticket/runtime/<舊版本>
```

`runtime clean` 這個破壞性指令首版刻意不提供——在定義清楚「保留幾版、怎麼保護目前
使用中與上一個可回退版本」之前，一個會刪東西的指令弊大於利。

## 單一實例

`state/supervisor.json` 記著 pid。啟動時若該 pid 還活著就拒絕啟動（結束碼 `8`），
避免兩個 worker 搶同一個 SQLite broker 與同一個 9222 埠。正常結束會移除這個檔。

行程被強制砍掉而留下這個檔時，`doctor` 會指出殘留；確認沒有行程在跑後刪掉即可。
