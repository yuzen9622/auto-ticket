# 開發與提交規範

## Commit 訊息：Conventional Commits

版本號、`CHANGELOG.md` 與 Release PR 全部由 release-please 依 commit type 推導，
所以 commit 訊息不是風格問題，寫錯 type 會直接發錯版號。

```
<type>(<scope>): <subject>

[body]

[footer]
```

| type | 版本影響 | 用在 |
| --- | --- | --- |
| `feat` | minor | 新功能 |
| `fix` | patch | 修 bug |
| `perf` | patch | 效能改善 |
| `refactor` / `test` / `docs` / `chore` / `ci` / `build` | 無 | 不影響使用者行為的變更 |

破壞性變更在 type 後加 `!`（`feat!: …`）或在 footer 寫 `BREAKING CHANGE: …`，
release-please 會升 major。

`feat` / `fix` / `perf` 三種 subject 會原樣進入公開的 Release notes，請用
**使用者看得懂的效果**描述，不要寫內部實作名詞。

## Commit subject 的敏感詞政策

repo 是公開的，commit 歷史一旦推上去就追不回來。以下字詞**不得出現在 commit
subject 或 scope**，`commit-hygiene` workflow 會在 PR 上直接擋下：

```
selector, xpath, css, captcha, 驗證碼, cloudflare, turnstile, anti-bot, 反偵測,
風控, fingerprint, webdriver, stealth, bypass, 繞過, rate-limit, backoff, retry,
warmup, 預熱, T-<數字>
```

清單的唯一事實來源是 `scripts/release/sensitive-terms.mjs`，同一份清單也用於
Release notes 遮蔽器（`scripts/release/redact_notes.mjs`）。

擋的不是祕密，是「我們在盯哪裡」這類會讓票務平台據以調整的工程細節。改寫方式：

| 不要寫 | 改寫成 |
| --- | --- |
| `fix(kktix): update sold-out selector` | `fix(kktix): 修正售票狀態判讀` |
| `feat: add captcha OCR retry` | `feat: 提高驗證流程的成功率` |
| `perf: shorten warmup to T-3min` | `perf: 縮短開賣前的準備時間` |

需要記錄完整工程細節時，寫進 `docs/internal/`——該目錄在 `.gitignore` 內，不進版控。
`docs/` 底下只有 `INSTALL.md`、`RUNTIME.md`、`MIGRATION.md`、`RELEASING.md`、
`SECURITY.md` 五份是追蹤的公開文件。

## 本機驗證

送 PR 前跑：

```bash
pnpm run check
```

它串接不變式守門員、pytest、TypeScript typecheck、lint、前端測試、契約測試與
CLI 測試；CI 跑的是同一組。

實站測試（`tests/live`）預設不收集，需要 `--live` 才解鎖，且**不得**進入任何
CI 或發版流程。

## 安全底線

- 付款一律 `payment_method="mock"`；任何測試與 CI 不得選票、建單、送出或付款。
- 不得讀寫使用者日常的預設 Chrome profile；`~/.auto-ticket/chrome-profile` 是唯一
  允許的 user-data-dir。
- 不得提交 `data/`、`.vault_key`、`.env`、`task.json` 或任何工具輸出目錄；
  `scripts/audit_public_release.py` 會掃索引與全歷史。
