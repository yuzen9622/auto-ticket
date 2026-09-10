"""排程器驅動真實狀態機的端到端閉環。"""

from __future__ import annotations

import asyncio
import json
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from domain.preference import TicketPreference, TicketPriority
from domain.task import CreditCardProfile, PurchaseTaskSpec, UserContactProfile
from fsm.machine import PurchaseWorkflow
from scheduler.clock_sync import TimeReference
from scheduler.scheduler import WarmupContext, WarmupScheduler, WarmupStage
from telemetry.timeline import TimelineEventType, TimelineRecorder
from tests.netguard import netguard_autouse  # noqa: F401

BASE_WALL = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, wall: datetime = BASE_WALL, perf: float = 500.0,
                 tick: float = 0.0005) -> None:
        self.wall = wall
        self.perf = perf
        self.tick = tick

    def advance(self, seconds: float) -> None:
        self.wall += timedelta(seconds=seconds)
        self.perf += seconds

    def wall_clock(self) -> datetime:
        return self.wall

    def perf_counter(self) -> float:
        value = self.perf
        self.advance(self.tick)
        return value

    def sleeper(self) -> Any:
        async def _sleep(seconds: float) -> None:
            self.advance(max(0.0, seconds))
            await asyncio.sleep(0)

        return _sleep


class FakeJobScheduler:
    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.running = False
        self.shutdown_calls: list[bool] = []

    def start(self) -> None:
        self.running = True

    def add_job(self, func: Any, trigger: str, run_date: datetime, args: tuple[Any, ...],
                id: str, misfire_grace_time: int | None, coalesce: bool) -> Any:
        self.jobs[id] = {"func": func, "run_date": run_date, "args": args}
        return self.jobs[id]

    def remove_job(self, job_id: str) -> None:
        if job_id not in self.jobs:
            raise KeyError(job_id)
        del self.jobs[job_id]

    def shutdown(self, wait: bool = True) -> None:
        self.shutdown_calls.append(wait)
        self.running = False


class NullSynchronizer:
    """整合測試不做時鐘校準：回傳零樣本，杜絕任何對外請求。"""

    def __init__(self, server_url: str | None = None) -> None:
        self.server_url = server_url

    async def refresh(self) -> TimeReference:
        return TimeReference(offset_ms=0.0, samples=(), primary_source=None)


def make_spec(sale_at: datetime, task_id: str = "e2e-1") -> PurchaseTaskSpec:
    return PurchaseTaskSpec(
        task_id=task_id,
        event_title="Demo",
        event_url="https://tickets.example.com/events/1",
        sale_start_at=sale_at,
        ticket_preference=TicketPreference(priorities=[TicketPriority(price=100)]),
        contact_profile=UserContactProfile(name="n", phone="0912345678", email="a@b.co"),
        payment_profile=CreditCardProfile(
            card_number="4111111111111111", expiry_month="01", expiry_year="30",
            cvv="123", cardholder_name="N",
        ),
    )


class Harness:
    def __init__(self, clock: Clock, telemetry: TimelineRecorder,
                 jobs: FakeJobScheduler, scheduler: WarmupScheduler) -> None:
        self.clock = clock
        self.telemetry = telemetry
        self.jobs = jobs
        self.scheduler = scheduler
        self.stages: list[WarmupStage] = []

    def register(self, fail: set[WarmupStage] | None = None) -> None:
        fail = fail or set()

        def make(stage: WarmupStage) -> Any:
            async def handler(ctx: WarmupContext) -> None:
                self.stages.append(stage)
                await asyncio.sleep(0)
                if stage in fail:
                    raise RuntimeError(f"{stage.value} exploded")

            return handler

        for stage in WarmupStage:
            self.scheduler.register(stage, make(stage))

    def marks(self, name: str) -> list[Any]:
        return [e for e in self.telemetry.events_of(TimelineEventType.MARK) if e.name == name]

    def errors(self) -> list[str]:
        return [e.name for e in self.telemetry.events_of(TimelineEventType.ERROR)]

    def transitions(self) -> list[tuple[str, str, str]]:
        return [
            (e.detail["from_state"], e.detail["event"], e.detail["to_state"])
            for e in self.telemetry.events_of(TimelineEventType.TRANSITION)
        ]


@pytest.fixture
async def harness() -> Any:
    clock = Clock()
    telemetry = TimelineRecorder(perf_counter=clock.perf_counter, wall_clock=clock.wall_clock)
    jobs = FakeJobScheduler()
    scheduler = WarmupScheduler(
        telemetry,
        job_scheduler=jobs,
        clock_synchronizer_factory=NullSynchronizer,
        wall_clock=clock.wall_clock,
        perf_counter=clock.perf_counter,
        sleep=clock.sleeper(),
    )
    await scheduler.start()
    yield Harness(clock, telemetry, jobs, scheduler)
    await scheduler.shutdown()


async def test_warmup_drives_real_workflow_to_sale_open(harness: Harness) -> None:
    harness.register()
    spec = make_spec(harness.clock.wall + timedelta(minutes=2))
    workflow = PurchaseWorkflow(spec, harness.telemetry)
    await harness.scheduler.schedule(spec, fsm=workflow)

    assert workflow.current_state_id == "WAITING_FOR_SALE"
    for stage in (WarmupStage.NAVIGATE_PAGE, WarmupStage.ENTER_READY, WarmupStage.SPIN_WAIT):
        await harness.scheduler._dispatch_stage(spec.task_id, stage)
    assert await harness.scheduler.wait_until_finished(spec.task_id, timeout=2.0) is True

    assert workflow.current_state_id == "SALE_OPEN"
    assert harness.transitions() == [
        ("IDLE", "prepare_session", "PREPARING"),
        ("PREPARING", "session_ready", "WAITING_FOR_SALE"),
        ("WAITING_FOR_SALE", "sale_triggered", "SALE_OPEN"),
    ]
    assert harness.errors() == []


async def test_check_session_failure_drives_real_workflow_to_failed(harness: Harness) -> None:
    harness.register(fail={WarmupStage.CHECK_SESSION})
    spec = make_spec(harness.clock.wall + timedelta(minutes=2))
    workflow = PurchaseWorkflow(spec, harness.telemetry)
    plan = await harness.scheduler.schedule(spec, fsm=workflow)

    assert workflow.current_state_id == "FAILED"
    assert plan.aborted is True
    assert plan.finished.is_set()
    assert harness.jobs.jobs == {}
    assert ("PREPARING", "abort_failed", "FAILED") in harness.transitions()
    assert all(event != "sale_triggered" for _, event, _ in harness.transitions())


async def test_late_schedule_readiness_first_reaches_sale_open(harness: Harness,
                                                               tmp_path: Path) -> None:
    """T-300ms 建立且 FSM 為 IDLE：沿合法路徑補齊後開賣，誤差如實記錄。"""
    harness.register()
    spec = make_spec(harness.clock.wall + timedelta(milliseconds=300))
    workflow = PurchaseWorkflow(spec, harness.telemetry)

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        await harness.scheduler.schedule(spec, fsm=workflow)
        assert await harness.scheduler.wait_until_finished(spec.task_id, timeout=2.0) is True

    assert workflow.current_state_id == "SALE_OPEN"
    assert harness.transitions() == [
        ("IDLE", "prepare_session", "PREPARING"),
        ("PREPARING", "session_ready", "WAITING_FOR_SALE"),
        ("WAITING_FOR_SALE", "sale_triggered", "SALE_OPEN"),
    ]
    assert harness.errors() == []
    assert len(harness.marks("sale_readiness_catch_up")) == 1
    assert len(harness.marks("sale_readiness_recovered")) == 1

    dest = tmp_path / "timeline.json"
    harness.telemetry.export_json(dest)
    payload = json.loads(dest.read_text(encoding="utf-8"))
    trigger_rows = [
        row for row in payload
        if row["event_type"] == "STAGE" and row["name"] == WarmupStage.TRIGGER_PURCHASE.value
    ]
    assert len(trigger_rows) == 1
    assert trigger_rows[0]["detail"]["sale_time_error_ms"] > 0

    names = [row["name"] for row in payload]
    assert names.index("sale_readiness_catch_up") < names.index("sale_readiness_recovered")
    assert names.index("sale_readiness_recovered") < names.index(
        WarmupStage.TRIGGER_PURCHASE.value
    )


async def test_trigger_failure_is_fail_closed_end_to_end(harness: Harness) -> None:
    harness.register(fail={WarmupStage.TRIGGER_PURCHASE})
    spec = make_spec(harness.clock.wall + timedelta(milliseconds=300))
    workflow = PurchaseWorkflow(spec, harness.telemetry)
    plan = await harness.scheduler.schedule(spec, fsm=workflow)
    assert await harness.scheduler.wait_until_finished(spec.task_id, timeout=2.0) is True

    assert workflow.current_state_id == "FAILED"
    assert harness.jobs.jobs == {}
    assert all(sp.job_id is None for sp in plan.stages)
    assert ("SALE_OPEN", "abort_failed", "FAILED") in harness.transitions()
    aborted = harness.marks("schedule_aborted")
    assert aborted and aborted[-1].detail["reason"] == "stage_failed:TRIGGER_PURCHASE"


async def test_abort_is_legal_from_payment_required(harness: Harness) -> None:
    """真實 FSM 走到 PAYMENT_REQUIRED 時中止仍屬合法轉移。"""
    harness.register()
    spec = make_spec(harness.clock.wall + timedelta(minutes=2))
    workflow = PurchaseWorkflow(spec, harness.telemetry)
    await harness.scheduler.schedule(spec, fsm=workflow)
    schedule = harness.scheduler._active_tasks[spec.task_id]

    workflow.sale_triggered()
    workflow.page_loaded()
    workflow.ticket_reserved()
    workflow.seat_confirmed()
    workflow.form_submitted()
    assert workflow.current_state_id == "PAYMENT_REQUIRED"

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        harness.scheduler._abort_schedule(schedule, reason="operator_abort")

    assert workflow.current_state_id == "FAILED"
    assert ("PAYMENT_REQUIRED", "abort_failed", "FAILED") in harness.transitions()
    assert harness.errors() == []
    assert harness.marks("fsm_abort_not_allowed") == []


async def test_cancel_stops_pipeline_and_timeline_exports(harness: Harness,
                                                          tmp_path: Path) -> None:
    harness.register()
    spec = make_spec(harness.clock.wall + timedelta(minutes=2))
    workflow = PurchaseWorkflow(spec, harness.telemetry)
    plan = await harness.scheduler.schedule(spec, fsm=workflow)

    assert harness.scheduler.cancel(spec.task_id) is True
    assert harness.jobs.jobs == {}
    assert plan.finished.is_set()
    assert workflow.current_state_id == "WAITING_FOR_SALE"

    await harness.scheduler._dispatch_stage(spec.task_id, WarmupStage.SPIN_WAIT)
    assert WarmupStage.TRIGGER_PURCHASE not in harness.stages

    dest = tmp_path / "cancelled.json"
    harness.telemetry.export_json(dest)
    payload = json.loads(dest.read_text(encoding="utf-8"))
    assert any(row["name"] == "task_cancelled" for row in payload)
    assert all(
        row["name"] != WarmupStage.TRIGGER_PURCHASE.value or row["event_type"] != "STAGE"
        for row in payload
    )
    sequences = [row["sequence"] for row in payload]
    assert sequences == sorted(sequences)
