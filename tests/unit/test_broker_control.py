from __future__ import annotations

import pytest

from broker.control import ControlAction, consume, list_signals, publish
from broker.schema import create_broker_schema
from storage.database import Database
from tests.netguard import netguard_autouse  # noqa: F401


@pytest.fixture
async def broker_db(db: Database) -> Database:
    await create_broker_schema(db.engine)
    return db


async def test_publish_and_consume(broker_db: Database) -> None:
    s1 = await publish(broker_db, task_id="t1", action=ControlAction.PAUSE)
    s2 = await publish(
        broker_db,
        task_id="t1",
        action=ControlAction.FORCE_TRANSITION,
        payload={"target_state": "READY"},
    )
    assert s1 > 0
    assert s2 > s1

    # 首次 consume
    signals = await consume(broker_db, task_id="t1")
    assert len(signals) == 2
    assert signals[0].id == s1
    assert signals[0].action == ControlAction.PAUSE
    assert signals[0].consumed_at is not None
    assert signals[1].id == s2
    assert signals[1].action == ControlAction.FORCE_TRANSITION
    assert signals[1].payload == {"target_state": "READY"}

    # 再次 consume 應為空（已消費）
    signals2 = await consume(broker_db, task_id="t1")
    assert len(signals2) == 0

    # list_signals 仍能查詢歷史
    history = await list_signals(broker_db, task_id="t1")
    assert len(history) == 2


async def test_consume_task_isolation(broker_db: Database) -> None:
    await publish(broker_db, task_id="t1", action=ControlAction.PAUSE)
    await publish(broker_db, task_id="t2", action=ControlAction.EMERGENCY_STOP)

    t1_signals = await consume(broker_db, task_id="t1")
    assert len(t1_signals) == 1
    assert t1_signals[0].action == ControlAction.PAUSE

    t2_signals = await consume(broker_db, task_id="t2")
    assert len(t2_signals) == 1
    assert t2_signals[0].action == ControlAction.EMERGENCY_STOP


async def test_consume_after_id(broker_db: Database) -> None:
    s1 = await publish(broker_db, task_id="t1", action=ControlAction.PAUSE)
    s2 = await publish(broker_db, task_id="t1", action=ControlAction.RESUME)

    # 略過 s1，只消費 s2
    signals = await consume(broker_db, task_id="t1", after_id=s1)
    assert len(signals) == 1
    assert signals[0].id == s2
    assert signals[0].action == ControlAction.RESUME
