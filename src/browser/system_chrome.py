"""自動啟動使用者本機的 Chrome 並開好遠端偵錯埠。

票券平台的人機驗證擋的是 Playwright 自帶的 Chrome for Testing——那個 build 的自動化
指紋會被認出來，**開著視窗也過不了**。能過的是使用者機器上那顆真正的 Chrome。

所以這個模組負責把「請你自己開一個帶 --remote-debugging-port 的 Chrome」這件事
自動化掉：找到系統 Chrome、用固定的 user-data-dir 啟動（登入狀態因此能跨次保留）、
等埠真的活了再回報端點。使用者只需要在畫面上出現驗證時去點一下。

刻意**不用**使用者的預設 profile：那顆 Chrome 多半已經開著，再啟動一次只會把參數
丟給既有行程而拿不到偵錯埠；另開一份 profile 才能穩定拿到埠，也不會干擾他原本的瀏覽器。
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

from browser.cdp_attach import page_url_matches, parse_page_target

DEFAULT_DEBUG_PORT = 9222
DEFAULT_USER_DATA_DIR = Path.home() / ".auto-ticket" / "chrome-profile"

#: 依序嘗試；先找 Chrome，再退而求其次找同樣是 Chromium 核心的瀏覽器。
MACOS_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
)
LINUX_CANDIDATES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
    "brave-browser",
)
WINDOWS_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)


class SystemChromeError(RuntimeError):
    """找不到可用的系統瀏覽器，或它沒有在預期時間內開好偵錯埠。"""


@dataclass(frozen=True, slots=True)
class LaunchedChrome:
    endpoint: str
    binary: Path
    user_data_dir: Path
    #: None 代表沿用既有行程（埠本來就活著），不是我們啟動的，也不該由我們關掉。
    process: subprocess.Popen[bytes] | None = None

    @property
    def launched_by_us(self) -> bool:
        return self.process is not None


def find_system_chrome() -> Path | None:
    """找出本機那顆真正的 Chrome；找不到回 None。"""
    if sys.platform == "darwin":
        candidates: tuple[str, ...] = MACOS_CANDIDATES
    elif sys.platform.startswith("win"):
        candidates = WINDOWS_CANDIDATES
    else:
        candidates = LINUX_CANDIDATES

    for candidate in candidates:
        path = Path(candidate)
        if path.is_absolute():
            if path.exists():
                return path
            continue
        resolved = shutil.which(candidate)
        if resolved:
            return Path(resolved)
    return None


async def probe_debug_port(
    endpoint: str, *, client: httpx.AsyncClient | None = None
) -> bool:
    """偵錯埠是不是已經活著。"""
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=1.0)
    try:
        response = await http.get(f"{endpoint}/json/version")
        return response.status_code // 100 == 2
    except httpx.HTTPError:
        return False
    finally:
        if owns_client:
            await http.aclose()


async def ensure_page(
    endpoint: str,
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    fallback_urls: Sequence[str] | None = None,
) -> None:
    """確保瀏覽器裡有一個停在 `url` 的分頁；沒有就開一個。

    CDP attach 要求「恰好命中一個分頁」。自動啟動的瀏覽器一開始停在 about:blank，
    不先把活動頁開起來，attach 會以「命中 0 個頁籤」收場。
    若提供了 fallback_urls，且瀏覽器中已有符合 fallback 的分頁（例如登入後轉址的分頁），
    則視為已有合適分頁，不重複開啟新分頁。
    """
    target = parse_page_target(url, fallback_urls=fallback_urls)
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=5.0)
    try:
        try:
            response = await http.get(f"{endpoint}/json/list")
            pages = [
                item
                for item in response.json()
                if item.get("type") == "page"
                and page_url_matches(
                    target, str(item.get("url", "")), allow_fallback=True
                )
            ]
        except (httpx.HTTPError, ValueError):
            pages = []

        if pages:
            return

        await http.put(f"{endpoint}/json/new?{url}")
    finally:
        if owns_client:
            await http.aclose()


async def ensure_system_chrome(
    *,
    port: int = DEFAULT_DEBUG_PORT,
    user_data_dir: Path | None = None,
    binary: Path | None = None,
    initial_url: str | None = None,
    fallback_urls: Sequence[str] | None = None,
    startup_timeout_s: float = 30.0,
    poll_interval_s: float = 0.25,
) -> LaunchedChrome:
    """確保本機有一顆開好偵錯埠的真 Chrome，回傳可直接 attach 的端點。

    埠已經活著就沿用（使用者可能自己開過，或上一個任務留下的），不重複啟動。
    """
    endpoint = f"http://127.0.0.1:{port}"
    profile_dir = user_data_dir or DEFAULT_USER_DATA_DIR

    async with httpx.AsyncClient(timeout=1.0) as client:
        if await probe_debug_port(endpoint, client=client):
            if initial_url:
                await ensure_page(
                    endpoint, initial_url, client=client, fallback_urls=fallback_urls
                )
            return LaunchedChrome(
                endpoint=endpoint,
                binary=binary or Path("<existing>"),
                user_data_dir=profile_dir,
            )

        executable = binary or find_system_chrome()
        if executable is None:
            raise SystemChromeError(
                "找不到本機的 Chrome。人機驗證需要用你自己的瀏覽器才過得了，"
                "請安裝 Google Chrome，或自行啟動一顆帶 "
                f"--remote-debugging-port={port} 的瀏覽器後重試"
            )

        profile_dir.mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen(
            [
                str(executable),
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile_dir}",
                # 第一次啟動的各種初次執行畫面會蓋住驗證視窗，一律關掉。
                "--no-first-run",
                "--no-default-browser-check",
                initial_url or "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        deadline = asyncio.get_running_loop().time() + startup_timeout_s
        while asyncio.get_running_loop().time() < deadline:
            if await probe_debug_port(endpoint, client=client):
                if initial_url:
                    await ensure_page(
                        endpoint,
                        initial_url,
                        client=client,
                        fallback_urls=fallback_urls,
                    )
                return LaunchedChrome(
                    endpoint=endpoint,
                    binary=executable,
                    user_data_dir=profile_dir,
                    process=process,
                )
            if process.poll() is not None:
                raise SystemChromeError(
                    f"瀏覽器啟動後隨即結束（exit={process.returncode}）：{executable}"
                )
            await asyncio.sleep(poll_interval_s)

        process.terminate()
        raise SystemChromeError(
            f"等了 {startup_timeout_s} 秒，{executable} 仍未開好偵錯埠 {port}"
        )
