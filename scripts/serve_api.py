#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path

import uvicorn

from api.main import create_app
from api.settings import ApiSettings


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
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
    # 路徑類旗標的 default 一律 None：真正的預設值在 `ApiSettings.from_env()`。
    # 在這裡抄第二份預設值，會讓 argparse 永遠蓋掉環境變數，打包安裝時就再也
    # 指不到 `~/.auto-ticket/data`。
    parser.add_argument(
        "--db",
        default=None,
        help="Path to SQLite database file (default: $AUTO_TICKET_DB_PATH 或 data/auto-ticket.db)",
    )
    parser.add_argument(
        "--screenshot-dir",
        default=None,
        help=(
            "Directory to store and serve screenshots "
            "(default: $AUTO_TICKET_SCREENSHOT_DIR 或 data/screenshots)"
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
    return parser.parse_args(args)


def settings_from_args(args: argparse.Namespace) -> ApiSettings:
    """環境變數為底，顯式旗標覆寫。兩者都沒給時落回 dataclass 預設。"""
    overrides: dict[str, object] = {}
    for flag, field_name in (
        ("db", "db_path"),
        ("screenshot_dir", "screenshot_dir"),
        ("vault_root", "vault_root"),
    ):
        value = getattr(args, flag)
        if value is not None:
            overrides[field_name] = Path(value)
    return dataclasses.replace(ApiSettings.from_env(), **overrides)


def main() -> None:
    args = parse_args()
    app = create_app(settings_from_args(args))
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
