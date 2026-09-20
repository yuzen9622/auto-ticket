#!/usr/bin/env python
"""開一個有頭瀏覽器，把登入狀態留在 persistent profile 裡。

兩種模式：
* 預設（人工）：只把瀏覽器開在指定的 `user_data_dir` 上，登入由使用者自己完成。
* `--auto-login`：從環境變數讀入憑證並自動填表。憑證**只從環境變數進來**，
  不接受命令列參數（命令列會留在 shell 歷史），也絕不寫入任何檔案或輸出。

關閉後該 profile 帶著 cookie，後續 `run_purchase.py --profile <name>` 與
`pytest tests/live --live-registration` 都會沿用同一份登入狀態。

用法：
    uv run python scripts/login.py --profile live
    # 在開啟的瀏覽器裡登入完成後，回到終端機按 Enter

    AUTO_TICKET_KKTIX_USERNAME=... AUTO_TICKET_KKTIX_PASSWORD=... \\
        uv run python scripts/login.py --profile live --auto-login
"""

# pyright: reportMissingImports=false
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from browser.context_factory import (  # noqa: E402
    BrowserProfile,
    build_persistent_context_options,
)

DEFAULT_URL = "https://kktix.com/users/sign_in"
# KKTIX 的 `load` 事件常常因為長尾資源遲遲不觸發；等它只會白等 30 秒。
NAVIGATION_WAIT_UNTIL = "domcontentloaded"
NAVIGATION_TIMEOUT_MS = 30000

ENV_USERNAME = "AUTO_TICKET_KKTIX_USERNAME"
ENV_KEY = "AUTO_TICKET_KKTIX_PASSWORD"


def credentials_from_env(
    environ: Mapping[str, str] | None = None,
) -> tuple[str, str] | None:
    """兩個環境變數都有值才算數；只填一半一律視為未提供，不拿空字串去試登入。"""
    source = os.environ if environ is None else environ
    user = source.get(ENV_USERNAME, "").strip()
    key = source.get(ENV_KEY, "").strip()
    if not user or not key:
        return None
    return user, key


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="login",
        description="開有頭瀏覽器供使用者自行登入，登入狀態留在 persistent profile",
    )
    parser.add_argument("--profile", default="live", help="profile 名稱（預設 live）")
    parser.add_argument(
        "--url", default=DEFAULT_URL, help=f"起始網址（預設 {DEFAULT_URL}）"
    )
    parser.add_argument(
        "--auto-login",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=f"從 {ENV_USERNAME} / {ENV_KEY} 自動填表登入（預設關閉，改由人工登入）",
    )
    return parser


KKTIX_AUTH_COOKIE_NAMES = {
    "user_id_v2",
    "user_display_name_v2",
    "user_avatar_url_v2",
    "user_path_v2",
    "user_time_zone_offset_v2",
    "user_time_zone",
    "user_time_zone_v2",
}


def summarize_cookies(
    cookies: Sequence[Any], host_fragment: str = "kktix"
) -> tuple[int, bool]:
    """只回報數量與「是否看起來有 session」——**絕不印出 cookie 內容**。"""
    relevant = [c for c in cookies if host_fragment in str(c.get("domain", ""))]
    has_session = any(
        "session" in str(c.get("name", "")).lower()
        or "token" in str(c.get("name", "")).lower()
        or str(c.get("name", "")) in KKTIX_AUTH_COOKIE_NAMES
        for c in relevant
    )
    return len(relevant), has_session


async def attempt_auto_login(page: Any) -> bool:
    """自動填入環境變數憑證。失敗只回報狀態，交由使用者接手手動登入。"""
    pair = credentials_from_env()
    if pair is None:
        print("[warn] 自動登入所需的两個環境變數未完整設定；跳過自動登入。")
        return False
    user, key = pair
    from adapters.payment.mock import MockPaymentProvider
    from adapters.ticketing.kktix.adapter import KKTIXAdapter
    from telemetry.timeline import TimelineRecorder

    telemetry = TimelineRecorder()
    adapter = KKTIXAdapter(telemetry=telemetry, payment=MockPaymentProvider())
    ok = await adapter.login(page, user, key)
    print(f"[auto-login] {'成功' if ok else '失敗，請改由人工登入'}")
    return ok


async def run(args: argparse.Namespace) -> int:
    from playwright.async_api import async_playwright

    profile = BrowserProfile(name=args.profile, headless=False)
    options = build_persistent_context_options(profile)
    print(f"profile     : {profile.name}")
    print(f"user_data_dir: {profile.user_data_dir}")
    print(f"起始網址     : {args.url}\n")

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(profile.user_data_dir), **options
        )
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(
                args.url,
                wait_until=NAVIGATION_WAIT_UNTIL,
                timeout=NAVIGATION_TIMEOUT_MS,
            )
        except Exception as exc:
            # 這支腳本的目的是把瀏覽器開起來讓人登入；開頁不順不該讓人連登都登不了。
            print(
                f"[warn] 自動開啟起始頁失敗（{type(exc).__name__}）；請在瀏覽器網址列自行前往。"
            )
        if args.auto_login:
            await attempt_auto_login(page)
        print("瀏覽器已開啟。請在裡面完成登入，然後回到這裡按 Enter。")
        await asyncio.to_thread(input, "登入完成後按 Enter> ")
        count, has_session = summarize_cookies(await context.cookies())
        await context.close()

    print(
        f"\n已關閉並保存 profile。kktix 相關 cookie：{count} 個"
        f"{'（含 session／token）' if has_session else '（未偵測到 session／token，登入可能未完成）'}"
    )
    print(
        f"\n接下來可用：\n  uv run python scripts/run_purchase.py --task <task.json> --profile {profile.name}"
    )
    return 0 if has_session else 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(build_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
