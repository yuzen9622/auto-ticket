# 原生 CLI 發行模式研究：以成熟工具取代自建 launcher 拼裝

- 日期：2026-09-22
- 範圍：只評估發行與安裝架構；不改產品程式碼、既有計畫或 Cloudflare／Chrome CDP 的安全邊界。
- 目標平台：macOS Apple Silicon、macOS Intel、Windows x64。

## 結論

先前計畫的方向——npm 作為很小的跨平台入口、GitHub Releases 存放大體積 runtime、Next.js standalone 產出 Web、保留本機真 Chrome 的專用 CDP profile——是合理的；問題不在「有沒有 CLI」，而在把每個技術細節都做成獨立的 npm 模組。首版應採 **一個零 runtime dependency 的 npm launcher、五個內聚檔案/領域模組**，把 npm、Next.js、GitHub Releases、Node 標準庫、PyInstaller（若採用）與 release-please 已經擅長的部分交出去。

建議的首選是 **選項 A：薄 npm CLI + 已封裝的、按平台發佈 runtime 目錄**。Python runtime 的實際封裝保留可替換邊界：先以目前規劃的可重定位 runtime 目錄驗證 Playwright／OCR；若其建置與維護成本過高，再以 PyInstaller `onedir` 替換「Python 直譯器 + site-packages + app」那一層。不要首版改用 PyInstaller `onefile`，更不要為 launcher 採 Node SEA。

此判斷不改變既有的資料安全前提：Chrome 仍由產品端以 `~/.auto-ticket/chrome-profile` 的專用 profile 與 loopback CDP 使用；launcher 不應讀取、管理或清除使用者日常 Chrome profile。既有計畫也已把真 Chrome 與 Playwright Chromium 的用途分開，後者仍可能需要安裝。[既有計畫 §現況與目標、§4、§8](../../.pi/plans/native-npm-launcher-and-github-release-runtime.md)

---

## 第一方能力盤點

### 1. Next.js standalone 已經是 Web runtime 的打包器

設定 `output: 'standalone'` 後，Next.js 會依檔案追蹤結果建立 `.next/standalone`，只帶生產所需檔案與選擇性的 `node_modules`，並產出可直接以 `node .next/standalone/server.js` 啟動的最小 server。這正符合「runtime asset 內含 Web、使用者的 Node 只負責啟動」的需求。[Next.js output](https://nextjs.org/docs/app/api-reference/config/next-config-js/output)

但官方明確指出 `public` 與 `.next/static` **不會**自動複製到 standalone 資料夾；若不是交給 CDN，發行組裝器必須主動複製到 `standalone/public` 與 `standalone/.next/static`。因此這是 CI build 的兩行複製工作，不是需要自訂 `web-config.mjs` 的功能。[Next.js output](https://nextjs.org/docs/app/api-reference/config/next-config-js/output)

**應交給成熟工具：** dependency tracing、standalone `server.js` 產出。  
**應自己寫：** 僅 build-stage 的 static/public 複製與一個 smoke test；不重做 Node module tracing。

### 2. npm 的 `bin` + `npm exec` 就是 CLI 發行通道

npm 的 `package.json#bin` 可把命令名映射到本地腳本；全域安裝時 Unix 會建立連結、Windows 會產生 `.cmd` shim，作為跨平台入口不需要自行寫 shell/bat 安裝器。[npm `bin`](https://docs.npmjs.com/cli/v11/configuring-npm/package-json#bin)

`npx` 在 npm 7 起實作為 `npm exec`；它可在本機或遠端 npm package 的環境中執行 bin。未安裝的 package 會存進 npm cache，非互動／CI 預設同意安裝，互動環境可由 `--yes` 消除提示。[npm exec](https://docs.npmjs.com/cli/v11/commands/npm-exec)

**應交給成熟工具：** package 下載、npm integrity、bin shim、cache 與 `npx` 的遠端執行。  
**應自己寫：** 只有 `bin/auto-ticket.mjs`（shebang 後 import 主程式）與少量指令解析；不要自建 installer、PATH 注入器或跨平台 shim。

### 3. GitHub Releases 足以承載按平台 runtime asset

GitHub 的 Release asset 回應包含 `browser_download_url`、檔案大小與 `digest`（範例為 `sha256:`）。官方下載說明允許直接取用 `browser_download_url`，或對 asset API 使用 `Accept: application/octet-stream`；客戶端必須可處理直接的 `200` 或重導向 `302`。[GitHub Release assets API](https://docs.github.com/en/rest/releases/assets?apiVersion=2022-11-28#download-a-release-asset)

因此 launcher 不需要 GitHub SDK、Octokit 或發行專用 SaaS。npm package 內的**版本化 manifest**應保留每個 target 的固定 URL、檔名、size 與 SHA-256；下載後以 manifest hash 驗證，不能只相信 HTTP 成功或重新向網路取得未釘選的 metadata。GitHub API 的 digest 可供 CI 交叉檢查，不取代隨 npm tarball 發佈的 hash pin。

**應交給成熟工具：** GitHub Release 儲存、CDN 導向、asset 上傳 API。  
**應自己寫：** 很小的「取 manifest 指定 URL → 串流下載到暫存檔 → 驗 hash → 解壓 → 完成才換名」流程。

### 4. Node 標準庫已覆蓋 launcher 的核心 I/O

在本專案所宣告的 Node 20.10 以上環境，global `fetch` 自 Node 18 起已不需 `--experimental-fetch` flag；目前官方文件也列出其瀏覽器相容的 `Request`、`Response`、`Headers` API。[Node globals: fetch](https://nodejs.org/api/globals.html#fetch)

- `node:crypto` 的 `createHash('sha256')` 支援以 stream 或 `update()`／`digest()` 計算 hash，官方亦提供以 `createReadStream` 計算 SHA-256 的範例。[Node crypto](https://nodejs.org/api/crypto.html#cryptocreatehashalgorithm-options)
- `node:fs/promises` 有 `mkdtemp` 與 `rename`；官方特別提醒非同步檔案操作要以 `await` 排序，避免 rename 尚未完成就讀取目標。這足以做 staging directory 與完成後換名，但「原子性」仍必須限定在同一檔案系統，並對 Windows 錯誤路徑測試。[Node fs](https://nodejs.org/api/fs.html)
- `child_process.spawn(command, args, options)` 原生接受 argv 陣列、`cwd`、`env`、`stdio`、`detached` 與 `windowsHide`；也支援 `AbortSignal`。它足夠啟動 Python、Next server、系統 `tar` 與 Playwright installer，但一律應傳 argv 陣列並維持 `shell: false`，不要串接使用者輸入的 shell command。[Node child_process](https://nodejs.org/api/child_process.html#child_processspawncommand-args-options)

**可刪除的第三方依賴類別：** HTTP client、雜湊套件、檔案複製／暫存套件、process runner、簡易 CLI framework。  
**例外：** Node 沒有內建 tar 解壓 API。首版可透過 `spawn()` 呼叫系統 `tar`，但必須在 `doctor` 檢查可用性且以 CI 覆蓋三個目標平台；若不願承擔系統 tar 差異，才引入一個專責、受維護的 tar library，而非自寫 archive parser。

### 5. PyInstaller 是可選的 Python runtime 封裝器，不是跨平台魔法

PyInstaller `onedir` 的 bootloader 會建立讓 Python 找到同資料夾中 modules/libraries 的執行環境，再啟動 Python interpreter；這適合將 API 與 worker 的 Python 執行依賴交給成熟工具。[PyInstaller operating mode](https://pyinstaller.org/en/stable/operating-mode.html#how-the-one-folder-program-works)

相反地，`onefile` 每次啟動都會將 archive 解壓到暫存資料夾，因此啟動較慢；官方也提醒檔案屬性目前不保留。對要啟動三個行程、保有外部資料目錄、還需安裝／定位 Playwright browser 的 local-first 服務，這是額外複雜度，非首版優勢。[PyInstaller operating mode](https://pyinstaller.org/en/stable/operating-mode.html#how-the-one-file-program-works)

PyInstaller 不能由單一 OS 產出另一個 OS 的 bundle：若要支援 macOS 與 Windows，官方要求各平台各自安裝 PyInstaller 並分別 bundle。這不是缺點，而是對目前三 target GitHub Actions matrix 的明確契約。[PyInstaller usage](https://pyinstaller.org/en/stable/usage.html#supporting-multiple-operating-systems)

**應交給成熟工具：** 若採 PyInstaller，Python interpreter、純 Python／native dependency 收集與 bootloader。  
**應自己寫：** PyInstaller spec／entry point、各平台 CI、動態 imports（例如 Playwright）及 OCR 實測 smoke test。PyInstaller 不會替專案決定 Chrome profile、資料遷移或三行程監督策略。

### 6. release-please 管版本與 Release PR，不管 artifact 發佈

release-please 依 Conventional Commit 歷史建立 Release PR，自動更新 changelog／版本檔，合併後 tag 並建立 GitHub Release；官方也明說它**不**處理 package manager publication。[release-please](https://github.com/googleapis/release-please)

因此它適合成為「版本單一事實來源」：同步 npm package manifest、Python version 與 changelog；tag workflow 再負責三平台 build、上傳 assets、驗 manifest 與 `npm publish`。不要把 runtime asset build、SHA 產生或 npm publish 邏輯塞入 release-please hook，否則會混淆責任與失敗重試。

---

## 適用於本案的發行架構選項

| 選項 | 組成 | 適用性 | 主要代價 | 判斷 |
| --- | --- | --- | --- | --- |
| **A. 薄 npm launcher + 可重定位 runtime 目錄** | npm `bin`／`npx` → Node built-ins 驗證並安裝 GitHub asset；asset 內含 Python runtime、app、Next standalone；Playwright browser 另由官方 installer 管理 | 最貼近現有設計與「乾淨機器 `npx`」目標；仍可保留真 Chrome CDP | 需自己維護少量 runtime 組裝、manifest、資料遷移與監督程式 | **首選** |
| **B. 薄 npm launcher + PyInstaller `onedir` Python artifact + Next standalone** | A 的 launcher 與 Web 不變；PyInstaller 取代 Python interpreter/site-packages 組裝 | 若 Python runtime 組裝與可重定位問題已成為瓶頸，可減少自訂 Python packaging | 仍需各 OS build；必須實測 Playwright 動態依賴、OCR native libs、browser installation；仍需使用者 Node 跑 Next | **可行的第二階段替換** |
| **C. 開發者安裝（wheel/source + Python/Node 前置）** | npm CLI 只協調現成 Python、Node、pip/uv 環境 | 內部開發、貢獻者、CI | 無法滿足乾淨一般使用者；Python、native wheel 與版本相容成本轉嫁給使用者 | **僅保留 dev flow，不作公開產品安裝** |

### 選項 A 的最小責任圖

```text
npx @yuzen9622/auto-ticket
  └─ npm bin（npm 負責下載與 shim）
       └─ launcher（Node built-ins）
            ├─ target / paths / manifest
            ├─ fetch GitHub Release asset → SHA-256 → staging → extract
            ├─ 首次資料「複製」遷移與 Chrome profile 邊界檢查
            └─ spawn API、worker、Next standalone；health probe、shutdown

GitHub Actions / release-please
  └─ version release PR → tag → 三 target build → GitHub Release assets → npm publish
```

關鍵是「asset 是不可由 launcher 任意組裝的版本化部署單位」：manifest 的 CLI 版本、runtime version、target 與 hash 必須同時相符。資料目錄與 browser profile 則完全放在 runtime 外，才能升級／回退而不搬移個資。

---

## 20+ 個 `packages/cli` 模組的直接處置

既有計畫把 21 個 source modules 分開列出（`cli` 至 `errors`）。以下是**架構建議，不是對既有 plan 的修改**；目標是將約 21 個實作檔收斂為五個內聚領域，功能與測試面不減少。[既有計畫「新增：npm CLI 套件」](../../.pi/plans/native-npm-launcher-and-github-release-runtime.md)

| 原清單 | 建議 | 原因／承接者 |
| --- | --- | --- |
| `bin/auto-ticket.mjs` | **保留** | 最薄 shebang，交給 npm `bin`。 |
| `cli.mjs`、`errors.mjs`、`platform.mjs`、`paths.mjs`、`manifest.mjs` | **合併為 `src/command.mjs`** | 都是輸入、target、路徑與 manifest 的純同步 boundary；以 exports 分段和單元測試取代檔案數。 |
| `download.mjs`、`verify.mjs`、`extract.mjs`、`runtime.mjs` | **合併為 `src/runtime-cache.mjs`** | 這四者共同實作一次 transactional install；使用 `fetch`、`crypto`、`fs`、`spawn(tar)`，不加 HTTP/hash/process dependency。保留函式級責任（download、verify、extract、ensure），不必各自一個 module。 |
| `migrate.mjs` | **保留為 `src/migration.mjs`** | SQLite backup、只複製、vault key 不外洩、冪等 marker 是本產品獨有資料契約，不能交給泛用 package。 |
| `env.mjs`、`ports.mjs`、`web-config.mjs`、`browsers.mjs` | **合併為 `src/host.mjs`；刪除 `web-config.mjs` 的 bundle 重寫策略** | env、port probe、Playwright install 都是啟動前置。`web-config` 為修改已建置 Next public bundle 而存在，風險高且把 build-time config 推到 runtime；首版固定 API/Web 預設埠，佔用時報錯。若必須支援可變 API port，改在 release build 產生明確 variant，不做 regex rewrite。 |
| `supervise.mjs`、`logger.mjs` | **合併為 `src/supervisor.mjs`** | 子行程生命週期與 stdout/stderr 轉發、健康檢查、輪替本來就是同一控制迴路；以 `spawn` 處理。 |
| `doctor.mjs` | **併入 `command.mjs` 的 `doctor` handler** | 首版只讀檢查（Node、target、Chrome、tar、磁碟／runtime）不值得自成 framework；複雜診斷再拆出。 |
| `vitest.config.mjs`、`test/**` | **保留** | 測試不是 runtime module。以 fake release server、竄改 hash、解壓失敗、遷移零覆寫、child cleanup 驗證合併後的 boundary。 |

建議最後的檔案形狀：

```text
packages/cli/
├── bin/auto-ticket.mjs
└── src/
    ├── command.mjs       # command/router + errors + target + paths + manifest + doctor
    ├── runtime-cache.mjs # download/verify/extract/ensure
    ├── migration.mjs     # 專案資料複製與 SQLite backup
    ├── host.mjs          # env/port/browser install
    └── supervisor.mjs    # spawn/readiness/log/shutdown
```

這不是「把所有程式塞一檔」：每個領域仍應暴露可注入的 I/O（例如 `fetchImpl`、`spawnImpl`、clock、homeDir），保有小單元測試；只是避免 20 多個只有數十行、互相循環 import 的檔案與 mock surface。

### 可立即刪除或延後的需求

1. **`web-config.mjs` 與非預設 API port 的 post-build text replacement：刪除。** Next public assets 是 build 產物；發布後掃 `.next/**` 的字串替換不可由 Next.js 保證，也會使 runtime cache 與檢核雜湊語意混亂。固定 loopback ports、偵測衝突、提供清晰診斷是較小且可驗證的首版範圍。
2. **獨立 log rotation framework：延後。** 首版 supervisor 可以前綴轉發並寫入單一檔案，啟動時以 size gate 告警或輪替；不必先引進 logging dependency 或為此多一個模組。
3. **自訂 CLI parser、retry、fetch、hash、filesystem、process libraries：刪除。** 由 Node 標準庫取代；重試僅留在 `runtime-cache` 的小函式。
4. **下載 GitHub token 邏輯：刪除。** 公開 Release asset 的使用者下載流程應依 manifest URL 進行；launcher 不需要把使用者的 `GITHUB_TOKEN` 傳入下載請求。私有發行若日後需要，應另設明確授權產品流程，而不是偷用環境 token。
5. **`runtime clean` 的自動清理：延後到有磁碟配額／rollback 保留規則後。** 安裝與啟動必須安全；清理是破壞性操作，首版可只列出可回收版本，要求明確 `--version`／`--yes` 才實作。

---

## 應自己寫與應交給工具的責任邊界

| 事項 | 責任歸屬 | 理由 |
| --- | --- | --- |
| `npx` 取得 CLI、跨平台 bin shim、npm package integrity | npm | npm 已提供 `bin`／`npm exec`。 |
| Web dependency tracing、`server.js`、standalone node_modules | Next.js | 官方 `output: 'standalone'` 已是這個用途。 |
| Release storage、asset URL、上傳 | GitHub Releases | 官方 asset API／下載 URL 已提供。 |
| HTTP、SHA-256、暫存檔、檔案換名、啟動子行程 | Node built-ins | `fetch`、`crypto`、`fs`、`child_process` 充分且少供應鏈。 |
| tar 格式解析 | 系統 tar 或單一成熟 tar library | 不自行解析 archive；Node 不內建 tar。 |
| Python interpreter/native dependencies（若採 B） | PyInstaller `onedir` | 交由 Python 成熟封裝器，但每平台 build。 |
| Conventional Commit → version/changelog/Release PR/tag | release-please | 工具明確涵蓋此工作。 |
| 產出三平台 asset、OCR smoke、asset manifest、npm publish | 專案 CI workflow | release-please 明確不處理 package-manager publication；此處才是產品版本契約。 |
| `~/.auto-ticket` 路徑、資料首次複製、SQLite 一致性 backup、vault 安全、Chrome profile 的唯一允許路徑 | 專案程式碼 | 這是 local-first 與安全約束，無泛用工具可安全猜測。 |
| API→worker→web 啟動順序、`/healthz` readiness、停止／孤兒行程處理 | 專案 `supervisor.mjs` | 依本案三行程與健康契約，保留最小自訂層。 |

---

## 不建議的替代方案

### Node SEA：不適合首版 launcher

Node 的 Single Executable Applications 現已提供 asset VFS，但該 VFS 功能在目前官方文件標為 Stability 1（Early development），且是 Node 26.9 新增能力。[Node SEA](https://nodejs.org/api/single-executable-applications.html#virtual-file-system-vfs-for-assets)

本案的 Next standalone 本來就要由 Node 執行，將很小的 launcher 再包成 SEA 沒有移除 Node 前置條件，反而增加一個不同的跨平台 binary build／簽章／除錯面。首版維持 npm `bin` 比較直接。

### PyInstaller `onefile`：不適合常駐 local service

`onefile` 的每次解壓、暫存資料夾、屬性限制，與 API/worker 常駐啟動和 Playwright 相關資產管理方向相反。若選 PyInstaller，優先 `onedir`；是否將 Python layer 改為 PyInstaller 應以三 OS 的 OCR + Playwright + 真 Chrome CDP smoke test 決定，而非只以生成單一檔案的表面便利決定。

---

## 建議的採用順序與驗收

1. **先收斂 CLI 表面：** 實作五個領域模組，不引入 npm runtime dependencies；先以 fake release asset 測 download/hash/extract/rollback。
2. **驗證 A 的 runtime：** 每個 target 在乾淨 runner 驗證 Next `server.js`、API health、worker 啟動、OCR 與專用 Chrome CDP profile；不跑真站操作。
3. **只在 A 的 Python 組裝持續製造跨平台痛點時，做 B 的 spike：** 每 OS 用 PyInstaller `onedir` 打包，驗 dynamic imports、OCR、Playwright browser 安裝與三行程關閉；三者皆過才替換 runtime internal layout。
4. **建立發版分工：** release-please 產生 Release PR 與 tag；tag workflow 產 assets／manifest，確認 hash 後 publish npm；Release asset 上傳完成後才讓使用者可取得對應版本。
5. **保留安全驗收：** runtime 安裝不得使用使用者 Chrome profile、不得搬移／覆寫原 repo `data/`、不得自動解除 macOS quarantine、不得在下載請求夾帶不必要 token。

---

## 一手來源

1. Next.js, `output` / standalone：<https://nextjs.org/docs/app/api-reference/config/next-config-js/output>
2. npm, `package.json#bin`：<https://docs.npmjs.com/cli/v11/configuring-npm/package-json#bin>
3. npm, `npm exec` / npx：<https://docs.npmjs.com/cli/v11/commands/npm-exec>
4. GitHub Docs, Release asset API：<https://docs.github.com/en/rest/releases/assets?apiVersion=2022-11-28#download-a-release-asset>
5. Node.js, global `fetch`：<https://nodejs.org/api/globals.html#fetch>
6. Node.js, `child_process`：<https://nodejs.org/api/child_process.html>
7. Node.js, `crypto`：<https://nodejs.org/api/crypto.html>
8. Node.js, `fs`：<https://nodejs.org/api/fs.html>
9. PyInstaller, operating mode：<https://pyinstaller.org/en/stable/operating-mode.html>
10. PyInstaller, usage / multi-OS builds：<https://pyinstaller.org/en/stable/usage.html>
11. Google APIs, release-please：<https://github.com/googleapis/release-please>
12. Node.js, Single Executable Applications：<https://nodejs.org/api/single-executable-applications.html>
