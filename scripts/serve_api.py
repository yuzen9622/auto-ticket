#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from api.main import create_app
from api.settings import ApiSettings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Auto Ticket API Server")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind (default: 8000)",
    )
    parser.add_argument(
        "--db",
        default="data/auto-ticket.db",
        help="Path to SQLite database file (default: data/auto-ticket.db)",
    )
    parser.add_argument(
        "--screenshot-dir",
        default="data/screenshots",
        help="Directory to store and serve screenshots (default: data/screenshots)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = ApiSettings(
        db_path=Path(args.db),
        screenshot_dir=Path(args.screenshot_dir),
    )
    app = create_app(settings)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
