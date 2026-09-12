from __future__ import annotations

from typing import Any

from .models import BrokerBase


async def create_broker_schema(engine: Any) -> None:
    """冪等建表。API Server 與 Worker 誰先啟動誰建，兩者都不得假設對方在線。"""
    async with engine.begin() as conn:
        await conn.run_sync(BrokerBase.metadata.create_all)
