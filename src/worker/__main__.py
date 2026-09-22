from __future__ import annotations

import argparse
import asyncio
import dataclasses
from pathlib import Path

from broker.broker import SqliteTaskBroker
from broker.outbox import OutboxWriter
from storage.database import Database

from .loop import WorkerLoop
from .settings import WorkerSettings, default_worker_id


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Auto Ticket Standalone Worker")
    # 每個旗標的 default 都是 None：真正的預設值在 `WorkerSettings.from_env()`，
    # 這裡只負責「使用者有沒有顯式指定」。把預設值抄第二份在這裡，等於讓
    # argparse 永遠蓋過環境變數，打包安裝的路徑就再也傳不進來。
    parser.add_argument(
        "--worker-id",
        default=None,
        help="Unique worker identifier (default: auto-generated)",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Path to SQLite database file (default: $AUTO_TICKET_DB_PATH 或 data/auto-ticket.db)",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Browser profile name (default: live)",
    )
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Run browser in headless mode (default: --headless)",
    )
    parser.add_argument(
        "--poll-ms",
        type=int,
        default=None,
        help="Queue polling interval in milliseconds (default: 500)",
    )
    parser.add_argument(
        "--clock-tick-hz",
        type=float,
        default=None,
        help="Clock tick frequency in Hz (default: 1.0)",
    )
    parser.add_argument(
        "--screenshot-dir",
        default=None,
        help=(
            "Directory to store screenshots "
            "(default: $AUTO_TICKET_SCREENSHOT_DIR 或 data/screenshots)"
        ),
    )
    parser.add_argument(
        "--timeline-dir",
        default=None,
        help=(
            "Directory to store task timelines "
            "(default: $AUTO_TICKET_TIMELINE_DIR 或 data/timelines)"
        ),
    )
    parser.add_argument(
        "--vault-root",
        default=None,
        help=(
            "Directory holding the encrypted credential vault "
            "(default: $AUTO_TICKET_VAULT_ROOT 或 data/credentials)"
        ),
    )
    parser.add_argument(
        "--cdp-endpoint",
        default=None,
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
        default=None,
        help="Cloudflare 挑戰被動寬限秒數（預設 45 秒）",
    )
    parser.add_argument(
        "--ocr",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="啟用或停用圖片驗證碼 OCR（預設 --ocr 啟用）",
    )
    return parser.parse_args(args)


def settings_from_args(args: argparse.Namespace) -> WorkerSettings:
    """環境變數為底，顯式旗標覆寫。兩者都沒給時落回 dataclass 預設。"""
    overrides: dict[str, object] = {}
    for flag, field_name, cast in (
        ("db", "db_path", Path),
        ("screenshot_dir", "screenshot_dir", Path),
        ("timeline_dir", "timeline_dir", Path),
        ("vault_root", "vault_root", Path),
        ("profile", "profile", str),
        ("headless", "headless", bool),
        ("poll_ms", "poll_ms", int),
        ("clock_tick_hz", "clock_tick_hz", float),
        ("cdp_endpoint", "cdp_endpoint", str),
        ("challenge_grace_s", "challenge_grace_s", float),
        ("ocr", "ocr_enabled", bool),
    ):
        value = getattr(args, flag)
        if value is not None:
            overrides[field_name] = cast(value)
    return dataclasses.replace(WorkerSettings.from_env(), **overrides)


async def amain(args: argparse.Namespace) -> None:
    worker_id = args.worker_id or default_worker_id()
    settings = settings_from_args(args)
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
