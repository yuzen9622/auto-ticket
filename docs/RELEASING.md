# 發版

## 拓撲

```
PR ──► ci.yml            invariants / pytest / audit / typecheck / lint / web test / contract / cli test
   └─► commit-hygiene.yml  commitlint + 敏感詞 subject 阻擋

main ─► release-please.yml ──► Release PR（CHANGELOG.md + 版本號寫入 4 處）
   merge ─► tag vX.Y.Z + GitHub Release（草稿）
         └─► release-runtime.yml
              ├─ build matrix（3 targets）→ smoke_ocr → 上傳 tar.gz + .sha256
              ├─ build_manifest.mjs   → packages/cli/runtime-manifest.json
              ├─ build_manifest --verify（雜湊交叉檢查）
              ├─ redact_notes.mjs     → 覆寫 Release body
              ├─ npm publish --provenance --access public
              └─ 將 Release 由草稿轉正式
         └─► clean-machine.yml   三 OS 乾淨 runner 跑 npx 煙霧測試
```

**責任切分**：release-please 只做「Conventional Commit → 版本 / CHANGELOG /
Release PR / tag」，它明確不處理 package manager publication。runtime asset build、
sha256、manifest 組裝與 `npm publish` 因此全部放在 tag 觸發的 `release-runtime.yml`，
不塞進 release-please 的設定或 hook——否則失敗重試與責任歸屬會混成一團。

## 版本的單一事實來源

release-please 以 `release-type: simple` 管理根版本，透過 `extra-files` 同步寫入：

- `packages/cli/package.json`（`$.version`）
- `packages/cli/runtime-manifest.json`（`$.version`）
- `pyproject.toml`（`# x-release-please-version` 標註）
- `src/api/settings.py`（同上標註，`/healthz` 回報的就是它）

CLI 啟動時會斷言 `runtime-manifest.json` 的 `version` 等於 `package.json` 的
`version`，不符即 `MANIFEST_MISMATCH`（結束碼 `4`）——那代表套件被竄改或建置有誤。

## 建置矩陣

| target | runner |
| --- | --- |
| `darwin-arm64` | `macos-14` |
| `darwin-x64` | `macos-15-intel` |
| `win32-x64` | `windows-2022` |

`macos-15-intel` 若在此帳號不可用，改成 `macos-13` 並**在這裡記錄實際使用的 runner**。

> 實際使用中的 runner：（首次發版時填寫）

跨平台建置不可行：wheel 可以用 `--python-platform` 解析，但可重定位的 CPython
必須是該平台自己的，所以三個 target 各開一個 runner。Next standalone 產物與平台
無關，但三個 job 各自 build 一次（約 +2 分鐘/job）換取零跨 job 耦合。

## 發版鐵律

**已發佈的 Release asset 不得刪除或重傳。**

npm 套件裡釘選的 sha256 指向那個 tag 的位元組。重傳一次，所有已安裝該版本的使用者
就永久卡在校驗失敗。有問題一律**往前發修補版**。

同理不使用 `npm unpublish`；要改 `latest` 指向請用 `npm dist-tag`。

## OCR 是硬門檻

每個 target 在打包前都要跑：

```bash
python packaging/runtime/smoke_ocr.py --image tests/fixtures/captcha_sample.png
```

預期輸出：

```
onnxruntime=1.23.2
providers=['CPUExecutionProvider']
ddddocr=ok classification_len>0
```

任一平台失敗即**中止該版發佈**，不得以「Intel 先跳過」放行。理由見
`packaging/runtime/README.md`：onnxruntime 是 native extension，wheel 裝得進去卻
import 失敗是這條鏈最常見的失敗形態，只有真的推論一次才看得出來。

## Release notes 遮蔽

`scripts/release/redact_notes.mjs` 只保留 `Features` / `Bug Fixes` /
`Performance Improvements` 三個 section，並對保留的每一行套用
`scripts/release/sensitive-terms.mjs` 的 denylist，命中者整行換成通用字樣。

遮蔽器**只保護 Release notes 這一頁**。repo 是公開的，commit 歷史與
`src/adapters/ticketing/*/selectors.py` 本身就是公開內容，遮蔽器無法追溯性地保護
它們。真正的長期控制是 `commit-hygiene` 在 PR 階段擋下敏感 subject——見
`CONTRIBUTING.md`。深度工程筆記放 `docs/internal/`，該目錄不進版控。

## 人工前置條件

這些有外部副作用或涉及帳號權限，必須由專案擁有者親自操作：

| # | 動作 | 阻擋 |
| --- | --- | --- |
| P1 | 跑 `uv run python scripts/audit_public_release.py` 並確認 0 finding | P2 |
| P2 | GitHub → Settings → 將 repo 由 private 改為 **public** | 任何 Release 下載、npm provenance。**不可逆** |
| P3 | Settings → Actions → 勾選 "Allow GitHub Actions to create and approve pull requests" | release-please 開不出 PR |
| P4 | Settings → Actions → Workflow permissions 設為 **Read and write** | 上傳 release asset |
| P5 | 建立／確認 npm 帳號並啟用 2FA，確保 scope `@yuzen9622` 可用 | npm publish |
| P6 | npm → Access Tokens → 建 **Granular Access Token**（僅 `@yuzen9622/auto-ticket` 的 read+write），存為 repo secret `NPM_TOKEN` | npm publish。勿用 classic token |
| P7 | 確認 `macos-15-intel` runner 標籤可用 | `darwin-x64` 建置 |
| P8 | `main` 設分支保護，要求 `ci` 與 `commit-hygiene` 通過 | 建議，非阻擋 |

**明確不需要**：Apple Developer Program 帳號、codesign 憑證、notarization 憑證。
首版不做簽章與公證，macOS 使用者以「系統設定 → 隱私權與安全性 → 仍要打開」手動放行。

## 轉 public 前的稽核

```bash
uv run python scripts/audit_public_release.py
uv run python scripts/audit_public_release.py --json    # 給自動化用
```

它做三層檢查：目前索引有沒有不該追蹤的路徑、全歷史有沒有「加進來又刪掉」的同類
路徑、全歷史 patch 內容有沒有憑證形態的字串（Fernet key、Bearer token、GitHub /
npm / AWS token、私鑰、cookie 值、個人 email、絕對家目錄路徑）。

測試與樣板檔的假資料會被當雜訊濾掉——不濾的話報告會被幾十條假紅淹沒，真的那一條
反而沒人看。

**這支腳本綠燈是人工切換 repo 可見性的前置條件。**
