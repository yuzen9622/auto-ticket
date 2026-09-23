# auto-ticket

[![CI](https://github.com/yuzen9622/auto-ticket/actions/workflows/ci.yml/badge.svg)](https://github.com/yuzen9622/auto-ticket/actions/workflows/ci.yml)
[![npm version](https://img.shields.io/npm/v/%40yuzen9622/auto-ticket)](https://www.npmjs.com/package/@yuzen9622/auto-ticket)
[![License](https://img.shields.io/github/license/yuzen9622/auto-ticket)](LICENSE)

[English](README.md)

可全域安裝並在本機執行的購票流程應用程式。它會在你的電腦上啟動 API、Worker 與瀏覽器儀表板，協助你準備及監看購票任務；資料、憑證、瀏覽器自動化與日誌都保留在本機。

## 功能

- 以單一指令啟動 API、Worker 與 Web 儀表板。
- 在本機儀表板建立、監看與檢視購票任務。
- 將資料、截圖、時間軸與加密憑證存放在 `~/.auto-ticket/`。
- 使用專用 Chrome profile，絕不讀寫日常使用的 Chrome profile。
- 僅支援 mock payment；不會送出訂單或發生真實付款。

## 快速開始

用你慣用的套件管理工具全域安裝 `auto-ticket` 指令：

```bash
# npm
npm install -g @yuzen9622/auto-ticket

# pnpm
pnpm add -g @yuzen9622/auto-ticket

# Yarn（v1 classic）
yarn global add @yuzen9622/auto-ticket
```

安裝後啟動：

```bash
auto-ticket
```

pnpm 若提示沒有設定全域 bin 目錄，先執行一次 `pnpm setup` 再開新終端機。Yarn 2 以上沒有全域安裝，請改用 npm 或 pnpm。

第一次執行會下載對應平台的 runtime 與 Playwright Chromium，然後開啟 <http://127.0.0.1:3000> 儀表板；後續啟動會重用已安裝的 runtime。

## 系統需求

| 需求 | 說明 |
| --- | --- |
| 作業系統 | macOS 13 以上 Apple Silicon，或 Windows 10 1803 以上 x64 |
| Node.js | 20.10 以上 |
| 瀏覽器 | Google Chrome |
| 系統工具 | `tar` |
| 可用磁碟 | runtime、Chromium 與本機資料至少需要 1.5 GB |

不支援 Intel Mac、Linux 與 Windows on ARM。Apple Silicon 請使用原生 arm64 Node.js，不要以 Rosetta 執行 Node.js。

若安裝或啟動未如預期，先執行唯讀診斷：

```bash
auto-ticket doctor
```

平台注意事項、埠位衝突處理與 macOS Gatekeeper 指引，請見[安裝說明](docs/INSTALL.md)。

## 指令

| 指令 | 說明 |
| --- | --- |
| `auto-ticket` 或 `auto-ticket start` | 啟動 API、Worker 與儀表板；按 `Ctrl+C` 停止。 |
| `auto-ticket doctor` | 唯讀檢查平台、Node.js、`tar`、Chrome、埠位、runtime、磁碟與 OCR 設定。 |
| `auto-ticket version` | 顯示 CLI、runtime、Python 與 ONNX Runtime 版本。 |
| `auto-ticket migrate --dry-run` | 預覽既有資料遷移，不變更任何檔案。 |
| `auto-ticket logs api -f` | 持續顯示應用程式日誌；可將 `api` 改為 `worker` 或 `web`。 |
| `auto-ticket runtime list` | 列出本機 runtime 版本與可回收大小。 |

API 固定使用 `127.0.0.1:8000`，儀表板固定使用 `127.0.0.1:3000`。首版不支援變更埠位；若埠位被占用，請停止占用程式後重試。

## 本機資料與隱私

應用程式建立的資料都保存在 `~/.auto-ticket/`，包括資料庫、截圖、時間軸、日誌、瀏覽器 profile 與 runtime 檔案。Windows 同樣使用家目錄，不會使用 `%APPDATA%`。

API 與儀表板只監聽 loopback 位址；本工具不收集 telemetry、不上傳日誌，也不使用雲端保存你的資料。下載 Release artifact、Playwright Chromium 與連線至你指定的售票網站，是僅有的對外網路操作。

帳密保存在本機加密 vault。請勿刪除 `~/.auto-ticket/data/credentials/.vault_key`，否則 vault 內資料將永久無法復原。

第一次遷移既有 repo 的 `data/` 時，只會複製，不會搬移或覆寫。可先執行：

```bash
auto-ticket migrate --dry-run
```

完整資訊請參閱[資料遷移](docs/MIGRATION.md)、[Runtime 維運](docs/RUNTIME.md)與[安全性](docs/SECURITY.md)。

## 安全邊界

- 付款永遠是 mock-only；不會選票、建立訂單、送出訂單或進行真實付款。
- 自動化使用 `~/.auto-ticket/chrome-profile/` 的專用 Chrome profile，不會存取日常 Chrome profile。
- macOS 首次啟動可能需要手動通過 Gatekeeper。本工具絕不自行移除 quarantine 屬性，也不會變更系統安全性設定。
- runtime 下載會以已安裝套件內附的 SHA-256 digest 驗證。

## 開發

打包後的應用程式與 repo 開發流程互相獨立。開發 repo 時，請安裝 Python 3.12 以上、[uv](https://docs.astral.sh/uv/)、Node.js 20 以上與 pnpm。

```bash
pnpm run setup
pnpm run dev
```

服務啟動後開啟 <http://localhost:3000>。提交變更前請執行完整驗證：

```bash
pnpm run check
```

Commit 與安全性規範請見[貢獻指南](CONTRIBUTING.md)。

## 文件

- [安裝說明](docs/INSTALL.md)
- [Runtime 維運](docs/RUNTIME.md)
- [資料遷移](docs/MIGRATION.md)
- [安全性](docs/SECURITY.md)
- [法律聲明與使用條款](LEGAL_NOTICE.md)
- [發版流程](docs/RELEASING.md)
- [變更紀錄](CHANGELOG.md)

## 授權

[MIT](LICENSE)
