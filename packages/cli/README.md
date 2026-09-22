# @yuzen9622/auto-ticket

auto-ticket 的原生本機 launcher。以 `npx` 取得後在你自己的機器上啟動 API、worker 與
web 三個行程；跑票的瀏覽器自動化與 OCR 全在本機執行，不上雲、不經第三方伺服器。

## 快速開始

```bash
npx @yuzen9622/auto-ticket
```

首次執行會：

1. 依平台（macOS Apple Silicon／macOS Intel／Windows x64）下載對應的 runtime 壓縮包並校驗 sha256。
2. 若偵測到舊版本（repo checkout 或前一個安裝）的資料，執行一次性資料遷移。
3. 安裝 Playwright Chromium（若尚未安裝）。
4. 以固定埠位啟動：API `http://127.0.0.1:8000`、Web `http://127.0.0.1:3000`。

## 支援平台

- macOS 13 Ventura 以上（Apple Silicon 原生、Intel 原生；**不支援 Rosetta 轉譯**）
- Windows 10 1803+ / Windows 11（x64）

不支援 Linux 與 Windows on ARM——執行時會直接報錯並說明原因。

## 指令

| 指令 | 說明 |
| --- | --- |
| `auto-ticket` / `auto-ticket start` | 啟動三個行程（前景監督，`Ctrl+C` 優雅關閉） |
| `auto-ticket doctor` | 只讀診斷：平台、Node 版本、`tar`、Chrome、埠位、runtime 完整性 |
| `auto-ticket runtime install\|path\|list` | 安裝／列印路徑／列出本機已安裝的 runtime 版本（`list` 不刪除任何東西） |
| `auto-ticket migrate [--from <path>] [--merge-missing] [--dry-run]` | 顯式資料遷移 |
| `auto-ticket logs [api\|worker\|web] [-n 200] [-f]` | 讀取日誌 |
| `auto-ticket version` | 印出 CLI／runtime／Python／onnxruntime 版本 |

`start` 支援 `--headed`、`--no-ocr`、`--skip-migration`、`--from <path>`。
**沒有** `--api-port` / `--web-port`：首版埠位固定為 8000（API）與 3000（web）。
**沒有** `runtime clean`：本機 runtime 版本目前只增不減。

## 本機目錄

所有資料落在 `~/.auto-ticket/`（Windows 同樣以 `os.homedir()` 為根），細節見
`docs/RUNTIME.md`。CLI 本身完全不讀寫你日常使用的 Chrome profile。

## 開發

```bash
cd packages/cli
npm install --save-dev vitest
npm test
```

本套件 `dependencies` 恆為 `{}`：只使用 Node built-ins 與系統 `tar`。
