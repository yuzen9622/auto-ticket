from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy.exc import OperationalError

from worker.__main__ import create_schema


class FakeDatabase:
    def __init__(self, errors: list[Exception]) -> None:
        self.errors = errors
        self.calls = 0

    async def create_all(self) -> None:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)


def _operational(message: str) -> OperationalError:
    return OperationalError(
        "CREATE TABLE events", {}, sqlite3.OperationalError(message)
    )


async def test_retries_once_when_the_api_created_the_table_first() -> None:
    db = FakeDatabase([_operational("table events already exists")])

    await create_schema(db)  # type: ignore[arg-type]

    assert db.calls == 2


async def test_other_database_errors_still_surface() -> None:
    db = FakeDatabase([_operational("database is locked")])

    with pytest.raises(OperationalError):
        await create_schema(db)  # type: ignore[arg-type]
    assert db.calls == 1
