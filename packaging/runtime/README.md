# Runtime 打包

這裡產生的是使用者真正下載的東西：一個**可重定位**的目錄，內含 CPython 3.12、
所有 Python 相依、應用原始碼與 Next 生產產物，壓成 `tar.gz` 後放上 GitHub Release。
npm CLI（`packages/cli/`）負責下載、驗 sha256、解壓到 `~/.auto-ticket/runtime/<version>/`。

## 檔案

| 檔案 | 作用 |
| --- | --- |
| `constraints.txt` | 需要人工釘死的版本，目前只有 `onnxruntime==1.23.2` |
| `requirements.in` | 來源清單，逐條對應 `pyproject.toml` 的 `[project].dependencies` |
| `requirements-<target>.txt` | `uv pip compile` 的鎖定產物，**進版控** |
| `build_runtime.py` | 組裝目錄、斷言 Next 產物、算 sha256、打包 |
| `migrate_db.py` | 用 `sqlite3.Connection.backup()` 做一致性 DB 複製，由 CLI 呼叫 |
| `smoke_ocr.py` | OCR 實機驗收，三平台的發版硬門檻 |

## 為什麼 onnxruntime 要釘 1.23.2

`ddddocr` 依賴**未釘版**的 `onnxruntime`。1.24 起 PyPI 不再發 macOS x86_64 wheel，
1.30 更只剩 arm64。解析器在 Intel Mac 上會自己挑到沒有 wheel 的版本，接著掉進
sdist 編譯——不是失敗，就是產出一個裝得起來、跑起來才炸的 runtime。

1.23.2 是唯一同時提供 `macosx_13_0_arm64` / `macosx_13_0_x86_64` / `win_amd64`（cp312）
的版本，所以三個目標平台共用它，也因此**最低支援 macOS 13 Ventura**。

`pyproject.toml` 另有一條同義的 marker 約束作為原始碼層防呆，但它只對外部解析器
（例如有人直接 `pip install`）有效；dev 的 `uv.lock` 靠 `[tool.uv].environments`
把 Intel Mac 排除在通用解析之外，否則那條上界會把 arm64 dev 機（CPython 3.14）
一起拖到裝不起來的版本。

## 重新產生鎖定檔

三個平台各跑一次。注意 `--python-platform` 要用 uv 認得的 target triple，
不是 `macos-aarch64` 這種寫法：

```bash
uv pip compile packaging/runtime/requirements.in \
  -c packaging/runtime/constraints.txt \
  --python-version 3.12 \
  --python-platform aarch64-apple-darwin \
  -o packaging/runtime/requirements-darwin-arm64.txt

uv pip compile packaging/runtime/requirements.in \
  -c packaging/runtime/constraints.txt \
  --python-version 3.12 \
  --python-platform x86_64-apple-darwin \
  -o packaging/runtime/requirements-darwin-x64.txt

uv pip compile packaging/runtime/requirements.in \
  -c packaging/runtime/constraints.txt \
  --python-version 3.12 \
  --python-platform x86_64-pc-windows-msvc \
  -o packaging/runtime/requirements-win32-x64.txt
```

改完之後三份鎖定檔的 ORT 必須仍是同一個版本：

```bash
grep -h '^onnxruntime==' packaging/runtime/requirements-*.txt | sort -u
# 預期恰好一行
```

## 本機建置

```bash
uv python install 3.12
pnpm --dir web install --frozen-lockfile
pnpm --dir web build          # 不要設任何 NEXT_PUBLIC_*
uv run python packaging/runtime/build_runtime.py --target darwin-arm64 --out dist-runtime/
```

產物：`dist-runtime/auto-ticket-runtime-darwin-arm64-<version>.tar.gz` 與同名 `.sha256`。

跨平台建置**不可行**：wheel 可以用 `--python-platform` 解析，但可重定位的 CPython
必須是該平台的。CI 因此為三個 target 各開一個 runner。

## artifact 內部結構

```
auto-ticket-runtime-<target>-<version>/
├── MANIFEST.json      # schema/version/target/python/onnxruntime/gitSha/builtAt/webApiBase/files[]
├── python/            # 可重定位 CPython 3.12（python-build-standalone）
├── site-packages/     # uv pip install --target 的產物，含 ddddocr 的 .onnx 模型
├── app/
│   ├── src/
│   └── scripts/{serve_api.py, migrate_db.py}
└── web/               # Next standalone：server.js + .next/ + .next/static/ + public/
```

**不含 Playwright 瀏覽器**。那約 550MB，且各平台的建置與校驗由 Playwright 官方
安裝器負責比我們自己重做可靠；CLI 首次啟動時以 runtime 內的 Python 執行
`python -m playwright install chromium`，落到 `~/.auto-ticket/ms-playwright`。

## 為什麼不建 venv

`pyvenv.cfg` 與 console script 的 shebang 都會寫死建置機的絕對路徑，解壓到別人
家目錄就整組失效。改為直接呼叫 `python/bin/python3`（Windows 為 `python\python.exe`）
並用環境變數組出 import 路徑：

```
PYTHONPATH        = <runtime>/site-packages : <runtime>/app/src
PYTHONNOUSERSITE  = 1
PYTHONUTF8        = 1
```

啟動一律帶 `-s`（排除 user site-packages）。**不得用 `-E`**——它會連 `PYTHONPATH`
一起忽略，整個 import 路徑就空了。

## Next 產物的 loopback 斷言

`NEXT_PUBLIC_*` 是**建置期內聯**：`web/lib/config.ts` 的預設值
`http://127.0.0.1:8000` / `ws://127.0.0.1:8000` 會被烤進 client chunk。
CI 建置時**不設定**這兩個環境變數，`build_runtime.py` 在打包前斷言字面值存在，
否則紅燈。

首版固定埠 8000/3000，所以發佈後**不對 Next 產物做任何字串改寫**——那不受
Next.js 任何保證，而且會讓 runtime 目錄不再等於 manifest 所校驗的位元。
單獨驗這一條：

```bash
uv run python packaging/runtime/build_runtime.py --assert-web-only --web-dir web/.next
```
