from __future__ import annotations

import argparse
import asyncio
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
    )
    settings.screenshot_dir.mkdir(parents=True, exist_ok=True)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)

    db = Database(settings.db_path)
    await db.create_all()

    broker = SqliteTaskBroker(db, lease_ttl_s=settings.lease_ttl_s)
    outbox = OutboxWriter(db, flush_ms=settings.outbox_flush_ms)

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
