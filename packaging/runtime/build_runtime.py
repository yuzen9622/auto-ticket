#!/usr/bin/env python3
"""組裝單一目標平台的 runtime artifact。

產物是一個**可重定位**的目錄，壓成 `auto-ticket-runtime-<target>-<version>.tar.gz`
後放上 GitHub Release，由 npm CLI 下載、驗雜湊、解壓到 `~/.auto-ticket/runtime/<version>/`。

刻意不建 venv：`pyvenv.cfg` 與 console script 的 shebang 都會把建置機的絕對路徑寫死，
解壓到別人家目錄就整組失效。改成直接呼叫 `python/bin/python3` 並用 `PYTHONPATH`
組出 import 路徑，整個目錄搬到哪都能跑。

也刻意不含 Playwright 瀏覽器（約 550MB）：那由 CLI 首次啟動時交給 Playwright 官方
安裝器處理，各平台的建置與校驗不該由我們重做一遍。

用法：
    python build_runtime.py --target darwin-arm64 --out dist-runtime/
    python build_runtime.py --assert-web-only --web-dir web/.next
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path


def _force_utf8_output() -> None:
    """Windows 主控台預設是 cp1252，編不了中文，一行 log 就能讓建置整個倒下。

    `PYTHONUTF8` 得在直譯器啟動前就設好才有用，在 `__main__` 裡設已經太遲，
    所以直接改串流本身。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


_force_utf8_output()

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGING_DIR = Path(__file__).resolve().parent

PYTHON_SERIES = "3.12"
ONNXRUNTIME_VERSION = "1.23.2"
MANIFEST_SCHEMA = 1

# 前端的 API base 是**建置期內聯**的字面值。CI 只要不小心設了 NEXT_PUBLIC_*，
# 錯誤的 base 就會被烤進 client chunk，而且發版前沒有任何執行期徵兆。
WEB_API_BASE = "http://127.0.0.1:8000"
WEB_WS_BASE = "ws://127.0.0.1:8000"

TARGETS: dict[str, dict[str, str]] = {
    "darwin-arm64": {"uv_platform": "aarch64-apple-darwin", "python_exe": "bin/python3"},
    "win32-x64": {"uv_platform": "x86_64-pc-windows-msvc", "python_exe": "python.exe"},
}

# 這些只會讓 artifact 變大、解壓變慢，對執行毫無貢獻。
PRUNE_DIR_NAMES = {"__pycache__", ".pytest_cache", ".ruff_cache", ".git"}
PRUNE_SUFFIXES = {".pyc", ".pyo"}


class BuildError(RuntimeError):
    pass


def log(message: str) -> None:
    print(f"[build-runtime] {message}", flush=True)


def project_version() -> str:
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if match is None:
        raise BuildError("pyproject.toml 讀不到 [project].version")
    return match.group(1)


def run(cmd: list[str], **kwargs: object) -> None:
    log("$ " + " ".join(cmd))
    proc = subprocess.run(cmd, check=False, **kwargs)  # type: ignore[arg-type]
    if proc.returncode != 0:
        raise BuildError(f"指令失敗（exit {proc.returncode}）: {' '.join(cmd)}")


def copy_tree(src: Path, dest: Path) -> None:
    """複製目錄並順手丟掉 bytecode 與工具快取。"""

    def ignore(_dir: str, names: list[str]) -> set[str]:
        dropped = {n for n in names if n in PRUNE_DIR_NAMES}
        dropped |= {n for n in names if Path(n).suffix in PRUNE_SUFFIXES}
        return dropped

    shutil.copytree(src, dest, symlinks=True, ignore=ignore, dirs_exist_ok=True)


# ----------------------------------------------------------------- Next.js

def iter_text_candidates(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in {".js", ".mjs", ".json", ".txt", ".html"}:
            yield path


def assert_web_loopback_base(next_dir: Path) -> None:
    """斷言 client bundle 裡真的是 loopback base。

    首版把 API 埠固定在 8000，正因為 build 後改寫 Next 產物是條不受任何保證的路。
    既然不改寫，就必須在打包前確認烤進去的值是對的——這是唯一的攔截點。
    """
    if not next_dir.is_dir():
        raise BuildError(f"找不到 Next 產物目錄: {next_dir}")

    wanted = {WEB_API_BASE: False, WEB_WS_BASE: False}
    for path in iter_text_candidates(next_dir):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for literal, found in wanted.items():
            if not found and literal in text:
                wanted[literal] = True
        if all(wanted.values()):
            break

    missing = [literal for literal, found in wanted.items() if not found]
    if missing:
        raise BuildError(
            "Next 產物缺少 loopback base 字面值 "
            f"{missing}；多半是建置時設了 NEXT_PUBLIC_API_BASE_URL / "
            "NEXT_PUBLIC_WS_BASE_URL，把別的 base 內聯了進去。"
        )
    log(f"Next 產物 loopback base 斷言通過（{WEB_API_BASE} / {WEB_WS_BASE}）")


def stage_web(web_src: Path, dest: Path) -> None:
    standalone = web_src / ".next" / "standalone"
    if not standalone.is_dir():
        raise BuildError(
            f"找不到 {standalone}；請先在 web/ 跑 `pnpm run build`"
            "（next.config.ts 必須有 output: \"standalone\"）"
        )

    # standalone 在 monorepo 佈局下會把專案再包一層；server.js 在哪就以哪為根。
    roots = [standalone, *(p.parent for p in standalone.rglob("server.js"))]
    root = next((r for r in roots if (r / "server.js").is_file()), None)
    if root is None:
        raise BuildError(f"{standalone} 內找不到 server.js")

    copy_tree(root, dest)

    # Next 官方明載這兩份不會自動進 standalone，得自己搬。
    static_src = web_src / ".next" / "static"
    if not static_src.is_dir():
        raise BuildError(f"找不到 {static_src}")
    copy_tree(static_src, dest / ".next" / "static")

    public_src = web_src / "public"
    if public_src.is_dir():
        copy_tree(public_src, dest / "public")

    if not (dest / "server.js").is_file():
        raise BuildError("組裝後的 web/ 缺少 server.js")


# ----------------------------------------------------------------- Python

def resolve_python_dir(explicit: Path | None) -> Path:
    if explicit is not None:
        if not explicit.is_dir():
            raise BuildError(f"--python-dir 不存在: {explicit}")
        return explicit

    proc = subprocess.run(
        ["uv", "python", "find", PYTHON_SERIES],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise BuildError(
            f"找不到 CPython {PYTHON_SERIES}；先跑 `uv python install {PYTHON_SERIES}`"
        )
    exe = Path(proc.stdout.strip())
    # `uv python find` 給的是 `<root>/bin/python3.12`（Windows 為 `<root>\python.exe`）。
    root = exe.parent.parent if exe.parent.name == "bin" else exe.parent
    if not root.is_dir():
        raise BuildError(f"推導不出可重定位的 CPython 根目錄: {exe}")
    return root


def install_site_packages(target: str, dest: Path) -> None:
    requirements = PACKAGING_DIR / f"requirements-{target}.txt"
    if not requirements.is_file():
        raise BuildError(f"缺少鎖定檔: {requirements}")
    dest.mkdir(parents=True, exist_ok=True)
    run(
        [
            "uv",
            "pip",
            "install",
            # 任何套件沒有 wheel 就立刻紅燈。靜默掉進 sdist 編譯會產出一個
            # 「裝得起來、跑起來才炸」的 runtime，那比建置失敗難查一百倍。
            "--only-binary=:all:",
            "--target",
            str(dest),
            "--python-version",
            PYTHON_SERIES,
            "--python-platform",
            TARGETS[target]["uv_platform"],
            "-r",
            str(requirements),
        ]
    )


def stage_app(dest: Path) -> None:
    copy_tree(REPO_ROOT / "src", dest / "src")
    scripts_dir = dest / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / "scripts" / "serve_api.py", scripts_dir / "serve_api.py")
    # 遷移要用 runtime 自己的 Python 跑，所以 migrate_db.py 必須跟著進 artifact——
    # 使用者機器上沒有 repo，CLI 也只出貨 bin/ 與 src/。
    shutil.copy2(
        PACKAGING_DIR / "migrate_db.py", scripts_dir / "migrate_db.py"
    )


# ----------------------------------------------------------------- manifest

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_sha() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip() if proc.returncode == 0 else "unknown"


def write_manifest(root: Path, target: str, version: str) -> Path:
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": sha256_file(path),
            }
        )

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "version": version,
        "target": target,
        "python": PYTHON_SERIES,
        "onnxruntime": ONNXRUNTIME_VERSION,
        "gitSha": git_sha(),
        "builtAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "webApiBase": WEB_API_BASE,
        "files": files,
    }
    manifest_path = root / "MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    log(f"MANIFEST.json 收錄 {len(files)} 個檔案")
    return manifest_path


def make_tarball(root: Path, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"{root.name}.tar.gz"
    if archive.exists():
        archive.unlink()
    log(f"打包 {archive.name}")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(root, arcname=root.name, recursive=True)

    digest = sha256_file(archive)
    (out_dir / f"{archive.name}.sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8"
    )
    log(f"sha256 {digest}  {archive.name}  ({archive.stat().st_size} bytes)")
    return archive


# ----------------------------------------------------------------- driver

def build(args: argparse.Namespace) -> int:
    target: str = args.target
    version: str = args.version or project_version()
    out_dir: Path = args.out.resolve()
    name = f"auto-ticket-runtime-{target}-{version}"

    with tempfile.TemporaryDirectory(prefix="auto-ticket-runtime-") as tmp:
        root = Path(tmp) / name
        root.mkdir(parents=True)

        log(f"目標 {target} / 版本 {version}")

        python_dir = resolve_python_dir(args.python_dir)
        log(f"複製可重定位 CPython：{python_dir}")
        copy_tree(python_dir, root / "python")
        expected_exe = root / "python" / TARGETS[target]["python_exe"]
        if not args.python_dir and not expected_exe.exists():
            log(f"警告：{expected_exe.relative_to(root)} 不存在（跨平台建置時屬正常）")

        if args.site_packages is not None:
            log(f"沿用既有 site-packages：{args.site_packages}")
            copy_tree(args.site_packages, root / "site-packages")
        else:
            install_site_packages(target, root / "site-packages")

        log("複製應用原始碼")
        stage_app(root / "app")

        log("組裝 Next standalone")
        assert_web_loopback_base(REPO_ROOT / "web" / ".next")
        stage_web(REPO_ROOT / "web", root / "web")

        write_manifest(root, target, version)
        archive = make_tarball(root, out_dir)

    log(f"完成：{archive}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=sorted(TARGETS), help="目標平台三元組")
    parser.add_argument("--version", default=None, help="預設取 pyproject 的版本")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "dist-runtime")
    parser.add_argument(
        "--python-dir",
        type=Path,
        default=None,
        help="可重定位 CPython 的根目錄（預設由 `uv python find` 推導）",
    )
    parser.add_argument(
        "--site-packages",
        type=Path,
        default=None,
        help="改用既有的 site-packages 目錄，跳過 uv pip install",
    )
    parser.add_argument(
        "--assert-web-only",
        action="store_true",
        help="只跑 Next 產物的 loopback base 斷言後結束",
    )
    parser.add_argument("--web-dir", type=Path, default=REPO_ROOT / "web" / ".next")
    args = parser.parse_args(argv)

    try:
        if args.assert_web_only:
            assert_web_loopback_base(args.web_dir.resolve())
            return 0
        if args.target is None:
            parser.error("需要 --target（或改用 --assert-web-only）")
        return build(args)
    except BuildError as exc:
        print(f"[build-runtime] 失敗：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
