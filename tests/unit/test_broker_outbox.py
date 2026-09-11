from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from broker.models import OutboxEventModel
from broker.outbox import OutboxReader, OutboxWriter
from broker.schema import create_broker_schema
from storage.database import Database
from tests.netguard import netguard_autouse  # noqa: F401


@pytest.fixture
async def outbox_db(db: Database) -> Database:
        await create_broker_schema(db.engine)
        return db


async def test_outbox_writer_thread_safe_publish(outbox_db: Database) -> None:
        writer = OutboxWriter(outbox_db, flush_ms=50)

        # 驗證從非 event loop 執行緒呼叫 publish 不會拋出例外
        def worker_thread(task_id: str, count: int) -> None:
                for i in range(count):
                        writer.publish(
                                task_id=task_id,
                                type="TASK_LOG",
                                payload={"index": i},
                                ephemeral=False,
                        )

        await asyncio.to_thread(worker_thread, "task_thread_1", 10)
        await asyncio.to_thread(worker_thread, "task_thread_2", 10)

        flushed = await writer.flush()
        assert flushed == 20

        reader = OutboxReader(outbox_db, start_at_end=False)
        await reader.initialise()
        records = await reader.poll(limit=100)
        assert len(records) == 20
        assert records[0].task_id in ("task_thread_1", "task_thread_2")

        await writer.aclose()


async def test_outbox_reader_start_at_end(outbox_db: Database) -> None:
        writer = OutboxWriter(outbox_db)
        writer.publish(task_id="t1", type="TASK_LOG", payload={"n": 1})
        await writer.flush()

        # 1. start_at_end=True: 不拉已存在的歷史
        reader_end = OutboxReader(outbox_db, start_at_end=True)
        await reader_end.initialise()
        assert len(await reader_end.poll()) == 0

        # 寫入新事件後再 poll
        writer.publish(task_id="t1", type="TASK_LOG", payload={"n": 2})
        await writer.flush()
        new_records = await reader_end.poll()
        assert len(new_records) == 1
        assert new_records[0].payload == {"n": 2}

        # 2. start_at_end=False: 從頭讀起
        reader_start = OutboxReader(outbox_db, start_at_end=False)
        await reader_start.initialise()
        all_records = await reader_start.poll()
        assert len(all_records) == 2

        await writer.aclose()


async def test_outbox_reader_replay_and_prune_ephemeral(outbox_db: Database) -> None:
        writer = OutboxWriter(outbox_db)
        writer.publish(
                task_id="t_replay", type="STATE_CHANGED", payload={"st": "PREPARE"}
        )
        writer.publish(
                task_id="t_replay",
                type="CLOCK_TICK",
                payload={"tick": 1},
                ephemeral=True,
        )
        writer.publish(task_id="t_other", type="TASK_LOG", payload={"other": True})
        await writer.flush()

        reader = OutboxReader(outbox_db)
        # replay 只取特定 task_id
        replayed = await reader.replay("t_replay", limit=10)
        assert len(replayed) == 2
        assert [r.type for r in replayed] == ["STATE_CHANGED", "CLOCK_TICK"]

        # 手動將 CLOCK_TICK 的時間改為 120 秒前以測試 prune_ephemeral
        old_time = datetime.now(timezone.utc) - timedelta(seconds=120)
        async with outbox_db.session() as session:
                from sqlalchemy import update

                await session.execute(
                        update(OutboxEventModel)
                        .where(OutboxEventModel.type == "CLOCK_TICK")
                        .values(created_at=old_time)
                )

        # prune older_than_s=60
        pruned = await reader.prune_ephemeral(older_than_s=60.0)
        assert pruned == 1

        # 驗證 CLOCK_TICK 被刪，STATE_CHANGED 保留
        replayed_after = await reader.replay("t_replay", limit=10)
        assert len(replayed_after) == 1
        assert replayed_after[0].type == "STATE_CHANGED"

        await writer.aclose()
