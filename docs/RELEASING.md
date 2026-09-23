# 發版

## 自己發一版：照這個順序做

```bash
# 1. 寫程式，然後用 Conventional Commits 提交。
#    前綴決定版號，這是唯一的版號控制方式——不要手改 package.json。
git commit -m "fix(cli): 修好某件事"        # → patch   0.4.0 → 0.4.1
git commit -m "feat(cli): 加了某個功能"      # → minor   0.4.0 → 0.5.0
git commit -m "feat(cli)!: 改掉某個介面"     # → major   0.4.0 → 1.0.0
git commit -m "chore: 整理"                 # → 不發版

# 2. 推上 main，等 CI 綠（約 2 分鐘）
git push origin main
gh run list --branch main --limit 2

# 3. release-please 會自動開一個 "chore(main): release X.Y.Z" 的 PR。
#    確認它的版號與 CHANGELOG 沒問題後合併——合併的瞬間就會打 tag。
gh pr list
gh pr merge <PR 編號> --squash

# 4. 手動派送建置與發佈（見下方的警告，這一步不會自己跑）
gh workflow run release-runtime.yml -f tag=v<X.Y.Z>
gh run list --workflow=release-runtime.yml --limit 1

# 5. 等它全綠（約 5 分鐘），再等 npm registry 傳播（約 1～2 分鐘）
npm view @yuzen9622/auto-ticket version

# 6. 驗一次真的能裝
npx --yes @yuzen9622/auto-ticket@<X.Y.Z> version
```

**只有第 3、4 步需要你判斷**，其餘都是等待。整條鏈約 10 分鐘。

### 幾個只有踩過才知道的地方

- **第 4 步絕對不能省**。合併 release PR 之後看起來「什麼都沒發生」是正常的，
  原因見下一節：`GITHUB_TOKEN` 打的 tag 不會觸發 workflow。
- **版號不是你決定的，是 commit 前綴決定的**。想發 patch 卻寫了 `feat:`，
  release-please 就會開 minor 的 PR。要改只能改 commit message 重推。
- **發錯了不要刪、不要 unpublish**，往前發一版修掉。理由見「發版鐵律」。
- **`npm deprecate` 要 2FA**，CI 的 token 做不到，只能你本機手動跑：
  ```bash
  npm login          # 一次就好
  npm deprecate "@yuzen9622/auto-ticket@<壞掉的版本>" "說明與建議改用的版本"
  ```
- **發佈用的是 CI 的 `NPM_TOKEN`**，本機沒登入也能發版；只有 `deprecate`／
  `dist-tag` 這類帳號層操作才需要本機登入。

### 出事了怎麼辦

| 症狀 | 原因與處置 |
| --- | --- |
| 合併 release PR 後沒有任何建置 | 忘了第 4 步，補跑 `gh workflow run` |
| release-runtime 紅了，但 tag 已經打出去 | 修好後重跑同一個 `-f tag=`，asset 上傳是冪等的（已存在就跳過） |
| npm 上看不到新版本 | registry 傳播延遲，等 1～2 分鐘再看；超過 5 分鐘才需要查 |
| 使用者回報校驗失敗 | **不要重傳 asset**，往前發修補版 |

## 拓撲

```
PR ──► ci.yml            invariants / pytest / audit / typecheck / lint / web test / contract / cli test
   └─► commit-hygiene.yml  commitlint + 敏感詞 subject 阻擋

main ─► release-please.yml ──► Release PR（CHANGELOG.md + 版本號寫入 4 處）
   merge ─► tag vX.Y.Z + GitHub Release（草稿）
         └─► release-runtime.yml
              ├─ build matrix（2 targets）→ smoke_ocr → 上傳 tar.gz + .sha256
              ├─ build_manifest.mjs   → packages/cli/runtime-manifest.json
              ├─ build_manifest --verify（雜湊交叉檢查）
              ├─ redact_notes.mjs     → 覆寫 Release body
              ├─ npm publish --provenance --access public
              └─ 將 Release 由草稿轉正式
         └─► clean-machine.yml   三 OS 乾淨 runner 跑 npx 煙霧測試
```

> **合併 release PR 之後要手動派送 `release-runtime`。** release-please 用
> `GITHUB_TOKEN` 建 tag，而 GitHub 為了擋遞迴，**`GITHUB_TOKEN` 觸發的事件不會啟動
> 新的 workflow run**——所以 `push: tags` 那個觸發器在這條路徑上不會生效。合併後跑：
>
> ```bash
> gh workflow run release-runtime.yml -f tag=v<X.Y.Z>
> ```
>
> 失敗的樣貌是「什麼都沒發生」：tag 與 draft release 都在，但沒有任何建置。

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
| `win32-x64` | `windows-2022` |

**`darwin-x64`（Intel Mac）首版不發**：`cryptography` 自 49.0.0 起不再提供涵蓋
x86_64 的 macOS wheel，最後一個有的是 48.0.1。要支援它就得讓 Intel 使用者的加密庫
停在更舊的版本，這在保管憑證的那一層不值得。理由寫在 `docs/INSTALL.md`，
`resolveTarget` 會給出具名錯誤（結束碼 2）。

跨平台建置不可行：wheel 可以用 `--python-platform` 解析，但可重定位的 CPython
必須是該平台自己的，所以兩個 target 各開一個 runner。Next standalone 產物與平台
無關，但兩個 job 各自 build 一次換取零跨 job 耦合。

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
| P7 | `main` 設分支保護，要求 `ci` 與 `commit-hygiene` 通過 | 建議，非阻擋 |

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
