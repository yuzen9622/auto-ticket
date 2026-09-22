# 安裝

```bash
npx @yuzen9622/auto-ticket
```

第一次執行會下載約 300MB 的 runtime 與約 150MB 的 Chromium，兩者都是可重入的獨立
階段：中斷了就再跑一次，不會留下半殘狀態。之後啟動不再下載。

## 支援平台

| 平台 | 支援 |
| --- | --- |
| macOS 13 Ventura 以上，Apple Silicon | ✅ |
| macOS 13 Ventura 以上，Intel | ✅ |
| Windows 10 1803 以上，x64 | ✅ |
| Linux | ❌ 首版不支援 |
| Windows on ARM | ❌ 首版不支援 |

macOS 的下界是 13，因為 OCR 依賴的 `onnxruntime` 唯一同時涵蓋三個目標平台的版本
（1.23.2）其 wheel 標的就是 `macosx_13_0`。

## 前置需求

| 需求 | 為什麼 |
| --- | --- |
| **Node.js 20.10 以上** | CLI 與前端的生產伺服器都用你自己的 Node 執行；runtime 不內嵌 Node |
| **Google Chrome** | 搶票一律借用你本機的真 Chrome。Playwright 自帶的瀏覽器過不了人機驗證 |
| **`tar`** | macOS 內建 `/usr/bin/tar`；Windows 10 1803 以上內建 `tar.exe` |
| 約 1.5GB 可用磁碟 | runtime + Chromium + 你的既有資料副本 |

`auto-ticket doctor` 會把以上逐條檢查並告訴你缺哪一項。

## 固定埠 8000 / 3000

首版把 API 固定在 `127.0.0.1:8000`、前端固定在 `127.0.0.1:3000`，**沒有**覆寫旗標。

這是刻意的取捨。前端的 API 位址是 **Next.js 建置期內聯**的字面值，要支援可變埠只有
兩條路：發佈後掃描改寫 `.next` 產物（不受 Next.js 任何保證，而且會讓完整性雜湊失去
意義），或是每個埠各發一份 build。首版選擇固定埠，把那條高風險的路整個關掉。

埠被佔用時 `auto-ticket start` 會以結束碼 `10` 中止，並印出是哪個埠、佔用它的 pid
與處置建議。先把佔用的程式關掉再重試：

```bash
# macOS
lsof -nP -iTCP:8000 -sTCP:LISTEN

# Windows
netstat -ano | findstr :8000
```

## macOS：第一次開啟被 Gatekeeper 擋下

首版**不做程式碼簽章與公證**。從網路下載的執行檔會帶上隔離屬性，macOS 可能顯示
「無法打開，因為無法驗證開發者」。手動放行：

1. 關掉該對話框
2. 「系統設定 → 隱私權與安全性」
3. 捲到底，在被擋下的項目旁按「仍要打開」
4. 回到終端機重跑 `npx @yuzen9622/auto-ticket`

**本工具不會、也不應該替你繞過這一步**：不會執行 `xattr -d com.apple.quarantine`、
不會動 `spctl`、不會 `codesign`。一個會自己拆掉你系統防線的安裝器，比它想省掉的
那一次點擊危險得多。

## 你的資料

第一次啟動會把既有的 `data/` **複製**（不是搬移）到 `~/.auto-ticket/data/`，來源
目錄完全不動。詳見 [MIGRATION.md](MIGRATION.md)。

## 常用指令

```bash
auto-ticket                 # 啟動全套（等同 start）
auto-ticket doctor          # 只讀診斷：平台、Node、tar、Chrome、埠、runtime、磁碟、OCR
auto-ticket version         # CLI / runtime / Python / onnxruntime 版本
auto-ticket migrate --dry-run   # 先看看遷移會做什麼
auto-ticket logs api -f     # 追某個行程的日誌
```

## 移除

```
~/.auto-ticket/          # runtime、瀏覽器、日誌與你的資料，整個刪掉即可
```

npm 的部分用 `npm uninstall -g @yuzen9622/auto-ticket`，或者你本來就只用 `npx`，
那就什麼都不用做。
