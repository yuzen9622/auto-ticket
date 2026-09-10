"""實站唯讀驗證套件的裝配。

**啟用方式只有一種**：`uv run pytest tests/live --live`。
預設不跑是靠 `pyproject.toml` 的 `--ignore=tests/live` 與根目錄 conftest 的旗標，
**不使用** skip／skipif／xfail——跳過標記會讓「沒跑」看起來像「跑過了」。

安全邊界（不可放寬）：
* 只讀。全程 `submit=False`、付款一律 `MockPaymentProvider`。
* **嚴禁**呼叫 `submit_order()` / `execute_payment()` / 任何送出訂單或付款的動作。
* 單次只開一頁、看完即關、不輪詢——避免被判定為爬蟲。
* 命中 Cloudflare 挑戰即失敗回報，**不繞過**。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import pytest

EVENT_URL_ENV = "AUTO_TICKET_LIVE_EVENT_URL"


def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:
    """未帶 `--live` 時清空收集結果（第二道保險，主要靠 ignore）。"""
    if not config.getoption("--live", default=False):
        items[:] = []


@pytest.fixture(scope="session")
def live_event_url() -> str:
    url = os.environ.get(EVENT_URL_ENV, "").strip()
    if not url:
        pytest.fail(
            f"實站驗證需要環境變數 {EVENT_URL_ENV}（活動主頁網址）；"
            "未提供即視為未通過，不以跳過掩蓋"
        )
    return url


@pytest.fixture
async def live_page() -> AsyncIterator[Any]:
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            yield page
        finally:
            await page.close()
            await browser.close()
