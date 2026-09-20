from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from broker.broker import SqliteTaskBroker
from broker.outbox import OutboxWriter
from storage.database import Database

from .loop import WorkerLoop
from .settings import WorkerSettings, default_worker_id


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Auto Ticket Standalone Worker")
    parser.add_argument(
        "--worker-id",
        default=None,
        help="Unique worker identifier (default: auto-generated)",
    )
    parser.add_argument(
        "--db",
        default="data/auto-ticket.db",
        help="Path to SQLite database file (default: data/auto-ticket.db)",
    )
    parser.add_argument(
        "--profile",
        default="live",
        help="Browser profile name (default: live)",
    )
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run browser in headless mode (default: --headless)",
    )
    parser.add_argument(
        "--poll-ms",
        type=int,
        default=500,
        help="Queue polling interval in milliseconds (default: 500)",
    )
    parser.add_argument(
        "--clock-tick-hz",
        type=float,
        default=1.0,
        help="Clock tick frequency in Hz (default: 1.0)",
    )
    parser.add_argument(
        "--screenshot-dir",
        default="data/screenshots",
        help="Directory to store screenshots (default: data/screenshots)",
    )
    parser.add_argument(
        "--cdp-endpoint",
        default=os.environ.get("AUTO_TICKET_CDP_ENDPOINT") or None,
        help=(
            "借用你自己的 Chrome（http://127.0.0.1:9222，僅 loopback）。"
            "KKTIX 的人機驗證擋 Playwright 自帶的瀏覽器，開視窗也過不了，"
            "要碰報名頁就必須用這個模式："
            "先以 --remote-debugging-port=9222 啟動你的 Chrome，再把端點給這裡"
        ),
    )
    parser.add_argument(
        "--challenge-grace-s",
        type=float,
        default=45.0,
        help="Cloudflare 挑戰被動寬限秒數（預設 45 秒）",
    )
    parser.add_argument(
        "--ocr",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="啟用或停用圖片驗證碼 OCR（預設 --ocr 啟用）",
    )
    return parser.parse_args(args)


async def amain(args: argparse.Namespace) -> None:
    worker_id = args.worker_id or default_worker_id()
    settings = WorkerSettings(
        db_path=Path(args.db),
        profile=args.profile,
        headless=bool(args.headless),
        poll_ms=args.poll_ms,
        clock_tick_hz=args.clock_tick_hz,
        screenshot_dir=Path(args.screenshot_dir),
        cdp_endpoint=args.cdp_endpoint,
        challenge_grace_s=args.challenge_grace_s,
        ocr_enabled=bool(args.ocr),
    )
    settings.screenshot_dir.mkdir(parents=True, exist_ok=True)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)

    db = Database(settings.db_path)
    await db.create_all()

    broker = SqliteTaskBroker(db, lease_ttl_s=settings.lease_ttl_s)
    outbox = OutboxWriter(db, flush_ms=settings.outbox_flush_ms)

    mode_str = (
        "Headless (背景執行)"
        if settings.headless
        else "Headed (有頭模式，領取任務時會自動開啟 Chrome 視窗)"
    )
    print("=" * 60)
    print("[*] Auto-Ticket Worker 已就緒！")
    print(f"    - Worker ID : {worker_id}")
    print(f"    - 運行模式  : {mode_str}")
    print(f"    - 資料庫    : {settings.db_path}")
    print(f"    - Profile   : {settings.profile}")
    ocr_str = "啟用" if settings.ocr_enabled else "停用"
    print(f"    - 驗證碼 OCR : {ocr_str}")
    print(f"    - 挑戰寬限  : {settings.challenge_grace_s} 秒")
    print("[*] 正在監聽任務隊列中...（建立並觸發任務後，瀏覽器才會開啟）")
    print("=" * 60)

    loop = WorkerLoop(
        db,
        broker,
        outbox,
        worker_id=worker_id,
        settings=settings,
    )
    try:
        await loop.run_forever()
    finally:
        await db.dispose()


def main() -> None:
    args = parse_args()
    asyncio.run(amain(args))


if __name__ == "__main__":
    main()
