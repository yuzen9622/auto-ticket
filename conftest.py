"""Repo 根目錄 pytest 外掛：實站套件的啟用旗標。

`--live` 必須在此註冊而非 `tests/live/conftest.py`：`pyproject.toml` 的 `addopts`
帶有 `--ignore=tests/live`，該目錄的 conftest 在預設情況下根本不會被載入，
旗標若定義在那裡，`pytest tests/live --live` 會直接以「未知參數」失敗。

啟用方式只有一種：`uv run pytest tests/live --live`。
不使用 skip／skipif／xfail 達成「預設不跑」——預設不跑是靠 ignore，不是靠跳過標記。
"""

from __future__ import annotations

from typing import Any

LIVE_PATH_FRAGMENT = "tests/live"


def pytest_addoption(parser: Any) -> None:
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="啟用實站唯讀驗證套件（需要網路與真實瀏覽器；絕不送出訂單或付款）",
    )
    parser.addoption(
        "--live-registration",
        action="store_true",
        default=False,
        help="另外啟用購票登記頁的唯讀驗證（需要已登入的瀏覽器 profile）；隱含 --live",
    )


def pytest_configure(config: Any) -> None:
    """帶 `--live`（或 `--live-registration`）時解除 addopts 對實站目錄的 ignore。"""
    if config.getoption("--live-registration"):
        config.option.live = True
    if not config.getoption("--live"):
        return
    ignore = list(config.option.ignore or [])
    config.option.ignore = [p for p in ignore if LIVE_PATH_FRAGMENT not in p.replace("\\", "/")]
