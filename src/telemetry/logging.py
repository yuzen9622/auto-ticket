"""統一 structlog 配置。

`configure_logging()` 為冪等：重複呼叫只會以最後一次的 level 生效，不會疊加 processor。
"""

from __future__ import annotations

import logging
from typing import Any

import structlog

DEFAULT_LEVEL = "INFO"


def configure_logging(level: str = DEFAULT_LEVEL) -> None:
    """設定 structlog 與標準 logging 的輸出層級與 processor 鏈。"""
    numeric_level = getattr(logging, str(level).upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", level=numeric_level, force=True)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None, **initial_values: Any) -> Any:
    """取得已綁定名稱的 structlog logger。"""
    logger = structlog.get_logger(name) if name is not None else structlog.get_logger()
    if initial_values:
        logger = logger.bind(**initial_values)
    return logger
