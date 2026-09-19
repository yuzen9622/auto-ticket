from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def _configure_sqlite(dbapi_conn: Any, _record: Any) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class Database:
    def __init__(self, db_path: str | Path, *, echo: bool = False) -> None:
        if str(db_path) in {":memory:", ""}:
            raise ValueError(
                "in-memory SQLite is unsupported: WAL requires a file-backed database"
            )
        self._db_path = Path(db_path).resolve()
        self._engine = create_async_engine(
            f"sqlite+aiosqlite:///{self._db_path}", echo=echo
        )
        event.listens_for(self._engine.sync_engine, "connect")(_configure_sqlite)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def engine(self) -> Any:
        return self._engine

    async def create_all(self) -> None:
        from storage import models  # noqa: F401

        def _migrate(sync_conn: Any) -> None:
            rows = sync_conn.exec_driver_sql("PRAGMA table_info(events)").fetchall()
            cols = {row[1] for row in rows}
            if cols and "sale_end_at" not in cols:
                sync_conn.exec_driver_sql(
                    "ALTER TABLE events ADD COLUMN sale_end_at DATETIME"
                )

        async with self._engine.begin() as conn:
            await conn.run_sync(models.Base.metadata.create_all)
            await conn.run_sync(_migrate)

    async def drop_all(self) -> None:
        from storage import models  # noqa: F401

        async with self._engine.begin() as conn:
            await conn.run_sync(models.Base.metadata.drop_all)

    async def dispose(self) -> None:
        await self._engine.dispose()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        session = self._session_factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
