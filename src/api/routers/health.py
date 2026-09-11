from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from api.deps import get_broker, get_db, get_settings
from api.settings import ApiSettings
from broker.broker import SqliteTaskBroker
from broker.models import BrokerJobModel
from storage.database import Database

router = APIRouter(tags=["health"])


@router.get("/healthz")
@router.get("/health")
@router.get("/api/v1/health")
async def health_check(
    settings: ApiSettings = Depends(get_settings),
    db: Database = Depends(get_db),
    broker: SqliteTaskBroker = Depends(get_broker),
) -> dict[str, Any]:
    broker_ok = False
    worker_seen_at: datetime | None = None
    try:
        async with db.session() as session:
            stmt = select(func.max(BrokerJobModel.heartbeat_at))
            worker_seen_at = (await session.execute(stmt)).scalar()
        broker_ok = True
    except Exception:
        broker_ok = False

    return {
        "status": "ok" if broker_ok else "degraded",
        "version": settings.version,
        "broker_ok": broker_ok,
        "worker_seen_at": (
            worker_seen_at.isoformat() if worker_seen_at is not None else None
        ),
    }
