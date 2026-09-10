"""實站唯讀驗證套件的裝配。

**兩層，各自明確開啟**：

* `uv run pytest tests/live --live`
  只驗證**公開活動主頁**，不需要登入。
* `uv run pytest tests/live --live-registration`
  額外驗證**購票登記頁**，需要一個已登入的瀏覽器 profile（見下）。

預設不跑是靠 `pyproject.toml` 的 `--ignore=tests/live` 與根目錄 conftest 的旗標，
**不使用** skip／skipif／xfail——跳過標記會讓「沒跑」看起來像「跑過了」。
登記頁那一層在未開啟時是「不收集」，並且會在 header 印一行明確告知未驗證。

**登入是使用者自己的事**：本套件不實作任何登入流程。作法是先用同一個
`user_data_dir` 開一次有頭瀏覽器手動登入，cookie 會留在該 profile 內：

    AUTO_TICKET_LIVE_HEADLESS=0 uv run pytest tests/live --live-registration

環境變數：
* `AUTO_TICKET_LIVE_EVENT_URL`        公開活動主頁網址（第一層必需）
* `AUTO_TICKET_LIVE_REGISTRATION_URL` 購票登記頁網址（第二層必需）
* `AUTO_TICKET_LIVE_PROFILE`          瀏覽器 profile 名稱，預設 `live`
* `AUTO_TICKET_LIVE_HEADLESS`         設為 `0` 開有頭瀏覽器（用來手動登入）

安全邊界（不可放寬）：只讀。不填表、不選位、**嚴禁**送出訂單或付款。
單次只開一頁、看完即關、不輪詢。命中 Cloudflare 挑戰即失敗回報，**不繞過**。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from browser.context_factory import BrowserProfile, build_persistent_context_options

EVENT_URL_ENV = "AUTO_TICKET_LIVE_EVENT_URL"
REGISTRATION_URL_ENV = "AUTO_TICKET_LIVE_REGISTRATION_URL"
PROFILE_ENV = "AUTO_TICKET_LIVE_PROFILE"
HEADLESS_ENV = "AUTO_TICKET_LIVE_HEADLESS"
REGISTRATION_MODULE = "test_kktix_registration.py"


def pytest_ignore_collect(collection_path: Path, config: Any) -> bool | None:
    """登記頁那一層需要額外的旗標；未開啟時不收集（不是跳過）。"""
    if collection_path.name == REGISTRATION_MODULE and not config.getoption(
        "--live-registration", default=False
    ):
        return True
    return None


def _tier_notice(config: Any) -> str:
    if config.getoption("--live-registration", default=False):
        return "live 涵蓋範圍：活動主頁 ＋ 購票登記頁（皆為唯讀）"
    return (
        "live 涵蓋範圍：只有活動主頁。**購票登記頁未驗證**"
        "（需 --live-registration 與已登入的 profile）"
    )


def pytest_report_header(config: Any) -> list[str]:
    return [_tier_notice(config)]


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: Any) -> None:
    """`-q` 會吃掉 header，因此在總結區再講一次涵蓋範圍。

    「哪一層沒跑」必須在預設輸出就看得到，否則綠燈會被誤讀成「整條路徑都驗過了」。
    """
    if not config.getoption("--live", default=False):
        return
    terminalreporter.write_line(_tier_notice(config))


def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:
    """未帶 `--live` 時清空收集結果（第二道保險，主要靠 ignore）。"""
    if not config.getoption("--live", default=False):
        items[:] = []


def _required_env(name: str, what: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.fail(f"實站驗證需要環境變數 {name}（{what}）；未提供即視為未通過，不以跳過掩蓋")
    return value


@pytest.fixture(scope="session")
def live_event_url() -> str:
    return _required_env(EVENT_URL_ENV, "公開活動主頁網址")


@pytest.fixture(scope="session")
def live_registration_url() -> str:
    return _required_env(REGISTRATION_URL_ENV, "購票登記頁網址")


@pytest.fixture
async def live_page() -> AsyncIterator[Any]:
    """乾淨的無痕情境：驗證公開頁面不需要、也不該帶著登入狀態。"""
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=os.environ.get(HEADLESS_ENV, "1") != "0"
        )
        page = await browser.new_page()
        try:
            yield page
        finally:
            await page.close()
            await browser.close()


@pytest.fixture
async def live_profile_page() -> AsyncIterator[Any]:
    """帶著使用者自行登入狀態的 persistent context。

    用的是產品程式碼的 `build_persistent_context_options()`，因此這條路徑本身
    也在被實站驗證（UA、locale、timezone、啟動參數是否會被擋）。
    """
    from playwright.async_api import async_playwright

    profile = BrowserProfile(name=os.environ.get(PROFILE_ENV, "live"))
    options = build_persistent_context_options(profile)
    options["headless"] = os.environ.get(HEADLESS_ENV, "1") != "0"

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(profile.user_data_dir), **options
        )
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            yield page
        finally:
            await context.close()
