"""[FSM-STAGE-DRIVE][FSM-STAGE-FAIL-CLOSED][FROZEN-1][REBASE-NO-EXPIRED-JOB]
[LATE-SCHED-DYNAMIC-REANCHOR][SCHED-SHUTDOWN-DRAIN][STAGE-SERIALIZED][CLOCK-SYNC-PER-TASK]
預熱排程器單元測試。
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from domain.preference import TicketPreference, TicketPriority
from domain.task import CreditCardProfile, PurchaseTaskSpec, UserContactProfile
from scheduler.clock_sync import ClockOffsetUpdate, ClockSample, ClockSource, TimeReference
from scheduler.scheduler import (
    DEFAULT_STAGE_OFFSETS,
    PRE_SALE_STAGES,
    TaskSchedule,
    WarmupContext,
    WarmupScheduler,
    WarmupStage,
)
from telemetry.timeline import TimelineEventType, TimelineRecorder
from tests.netguard import netguard_autouse  # noqa: F401

BASE_WALL = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
PRE_SALE = (
    WarmupStage.PREPARE_BROWSER,
    WarmupStage.CHECK_SESSION,
    WarmupStage.NAVIGATE_PAGE,
    WarmupStage.ENTER_READY,
)
FSM_TRANSITIONS = {
    ("IDLE", "prepare_session"): "PREPARING",
    ("PREPARING", "session_ready"): "WAITING_FOR_SALE",
    ("WAITING_FOR_SALE", "sale_triggered"): "SALE_OPEN",
}
FSM_FINAL = {"COMPLETED", "SOLD_OUT", "TIMEOUT", "FAILED"}


# --------------------------------------------------------------- fakes
class Clock:
    """wall 與 perf 同步前進的受控時鐘；每次讀取 perf 前進一個 tick，讓 tight-loop 會收斂。"""

    def __init__(self, wall: datetime = BASE_WALL, perf: float = 1000.0, tick: float = 0.0005) -> None:
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
    def __init__(self, *, add_error: Exception | None = None) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.added: list[dict[str, Any]] = []
        self.removed: list[str] = []
        self.add_error = add_error
        self.running = False
        self.shutdown_calls: list[bool] = []

    def start(self) -> None:
        self.running = True

    def add_job(self, func: Any, trigger: str, run_date: datetime, args: tuple[Any, ...],
                id: str, misfire_grace_time: int | None, coalesce: bool) -> Any:
        if self.add_error is not None:
            raise self.add_error
        record = {
            "func": func, "trigger": trigger, "run_date": run_date, "args": args,
            "id": id, "misfire_grace_time": misfire_grace_time, "coalesce": coalesce,
        }
        self.jobs[id] = record
        self.added.append(record)
        return record

    def remove_job(self, job_id: str) -> None:
        self.removed.append(job_id)
        if job_id not in self.jobs:
            raise KeyError(job_id)
        del self.jobs[job_id]

    def shutdown(self, wait: bool = True) -> None:
        self.shutdown_calls.append(wait)
        self.running = False


class FakeFsm:
    """最小合法 FSM 替身：只實作排程器會用到的預熱路徑與中止路徑。"""

    def __init__(self, state: str = "IDLE", *, abort_allowed: bool = True,
                 frozen: bool = False, raise_on: tuple[str, ...] = ()) -> None:
        self.state = state
        self.events: list[str] = []
        self.abort_allowed = abort_allowed
        self.frozen = frozen
        self.raise_on = raise_on

    @property
    def current_state_id(self) -> str:
        return self.state

    def can_send(self, event_name: str) -> bool:
        if self.state in FSM_FINAL:
            return False
        if event_name == "abort_failed":
            return self.abort_allowed
        return (self.state, event_name) in FSM_TRANSITIONS

    def _send(self, event_name: str) -> None:
        if event_name in self.raise_on:
            self.events.append(event_name)
            raise RuntimeError(f"TransitionNotAllowed: {event_name}")
        if event_name == "abort_failed":
            if not self.abort_allowed or self.state in FSM_FINAL:
                raise RuntimeError("TransitionNotAllowed: abort_failed")
            self.events.append(event_name)
            if not self.frozen:
                self.state = "FAILED"
            return
        target = FSM_TRANSITIONS.get((self.state, event_name))
        if target is None:
            raise RuntimeError(f"TransitionNotAllowed: {event_name} from {self.state}")
        self.events.append(event_name)
        if not self.frozen:
            self.state = target

    def prepare_session(self) -> None:
        self._send("prepare_session")

    def session_ready(self) -> None:
        self._send("session_ready")

    def sale_triggered(self) -> None:
        self._send("sale_triggered")

    def abort_failed(self) -> None:
        self._send("abort_failed")


class FakeSynchronizer:
    def __init__(self, server_url: str | None = None, *, offset_ms: float = 0.0,
                 error: Exception | None = None) -> None:
        self.server_url = server_url
        self.offset_ms = offset_ms
        self.error = error
        self.refresh_calls = 0

    async def refresh(self) -> TimeReference:
        self.refresh_calls += 1
        if self.error is not None:
            raise self.error
        return TimeReference(
            offset_ms=self.offset_ms,
            samples=(
                ClockSample(source=ClockSource.NTP, offset_ms=self.offset_ms, rtt_ms=10.0),
                ClockSample(source=ClockSource.SERVER_HEADER, offset_ms=self.offset_ms + 1, rtt_ms=20.0),
            ),
            primary_source=ClockSource.NTP,
        )


class SyncFactory:
    def __init__(self, **kwargs: Any) -> None:
        self.calls: list[str | None] = []
        self.created: list[FakeSynchronizer] = []
        self.kwargs = kwargs

    def __call__(self, server_url: str | None) -> FakeSynchronizer:
        self.calls.append(server_url)
        sync = FakeSynchronizer(server_url, **self.kwargs)
        self.created.append(sync)
        return sync


class Recorder:
    """記錄 handler 進出順序，用來證明階段沒有併發交錯。"""

    def __init__(self) -> None:
        self.trace: list[tuple[str, str]] = []
        self.calls: dict[WarmupStage, int] = {}

    def note(self, kind: str, stage: WarmupStage) -> None:
        self.trace.append((kind, stage.value))

    def count(self, stage: WarmupStage) -> int:
        return self.calls.get(stage, 0)


def make_spec(task_id: str = "task-1", *, sale_at: datetime = BASE_WALL,
              event_url: str = "https://one.example.com/events/1") -> PurchaseTaskSpec:
    return PurchaseTaskSpec(
        task_id=task_id,
        event_title="Demo",
        event_url=event_url,
        sale_start_at=sale_at,
        ticket_preference=TicketPreference(priorities=[TicketPriority(price=100)]),
        contact_profile=UserContactProfile(name="n", phone="0912345678", email="a@b.co"),
        payment_profile=CreditCardProfile(
            card_number="4111111111111111", expiry_month="01", expiry_year="30",
            cvv="123", cardholder_name="N",
        ),
    )


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def telemetry() -> TimelineRecorder:
    return TimelineRecorder()


@pytest.fixture
def jobs() -> FakeJobScheduler:
    return FakeJobScheduler()


@pytest.fixture
def factory() -> SyncFactory:
    return SyncFactory()


@pytest.fixture
async def scheduler(clock: Clock, telemetry: TimelineRecorder, jobs: FakeJobScheduler,
                    factory: SyncFactory) -> Any:
    sched = WarmupScheduler(
        telemetry,
        job_scheduler=jobs,
        clock_synchronizer_factory=factory,
        wall_clock=clock.wall_clock,
        perf_counter=clock.perf_counter,
        sleep=clock.sleeper(),
    )
    await sched.start()
    yield sched
    await sched.shutdown()


def register_recording_handlers(
    sched: WarmupScheduler, recorder: Recorder, *,
    cost: dict[WarmupStage, float] | None = None,
    fail: set[WarmupStage] | None = None,
    clock: Clock | None = None,
    gate: dict[WarmupStage, asyncio.Event] | None = None,
) -> None:
    cost = cost or {}
    fail = fail or set()
    gate = gate or {}

    def make(stage: WarmupStage) -> Any:
        async def handler(ctx: WarmupContext) -> None:
            recorder.calls[stage] = recorder.calls.get(stage, 0) + 1
            recorder.note("enter", stage)
            try:
                if stage in gate:
                    await gate[stage].wait()
                seconds = cost.get(stage, 0.0)
                if seconds and clock is not None:
                    clock.advance(seconds)
                await asyncio.sleep(0)
                if stage in fail:
                    raise RuntimeError(f"handler {stage.value} exploded")
            finally:
                recorder.note("exit", stage)

        return handler

    for stage in WarmupStage:
        sched.register(stage, make(stage))


def marks(telemetry: TimelineRecorder, name: str) -> list[Any]:
    return [e for e in telemetry.events_of(TimelineEventType.MARK) if e.name == name]


def stage_event(telemetry: TimelineRecorder, stage: WarmupStage) -> Any:
    hits = [e for e in telemetry.events_of(TimelineEventType.STAGE) if e.name == stage.value]
    return hits[-1] if hits else None


async def drain(times: int = 40) -> None:
    for _ in range(times):
        await asyncio.sleep(0)


# =================================================================== 基本排程
async def test_schedule_builds_six_stages_with_declared_offsets(scheduler: WarmupScheduler) -> None:
    plan = await scheduler.schedule(make_spec(sale_at=BASE_WALL + timedelta(hours=1)))
    assert [s.stage for s in plan.stages] == list(WarmupStage)
    for sp in plan.stages:
        assert plan.target_local_at - sp.planned_local_at == DEFAULT_STAGE_OFFSETS[sp.stage]


async def test_schedule_requires_start(clock: Clock, telemetry: TimelineRecorder) -> None:
    sched = WarmupScheduler(telemetry, job_scheduler=FakeJobScheduler(),
                            wall_clock=clock.wall_clock, perf_counter=clock.perf_counter)
    with pytest.raises(RuntimeError):
        await sched.schedule(make_spec(sale_at=BASE_WALL + timedelta(hours=1)))


async def test_schedule_rejects_naive_datetime(scheduler: WarmupScheduler) -> None:
    with pytest.raises(ValueError):
        # 刻意傳入 naive datetime 以驗證守衛，故此處必須關掉 DTZ001
        await scheduler.schedule("t-naive", datetime(2026, 3, 1, 12, 0))  # noqa: DTZ001


async def test_schedule_requires_sale_time_for_str_task(scheduler: WarmupScheduler) -> None:
    with pytest.raises(ValueError):
        await scheduler.schedule("t-no-time")


async def test_schedule_rejects_duplicate_active_task(scheduler: WarmupScheduler) -> None:
    spec = make_spec(sale_at=BASE_WALL + timedelta(hours=1))
    await scheduler.schedule(spec)
    with pytest.raises(ValueError):
        await scheduler.schedule(spec)


async def test_schedule_accepts_plain_task_id_and_sale_time(scheduler: WarmupScheduler) -> None:
    plan = await scheduler.schedule("plain-task", BASE_WALL + timedelta(hours=1))
    assert plan.task_id == "plain-task"
    assert scheduler.plan_of("plain-task") is plan


async def test_schedule_registers_future_jobs_only(scheduler: WarmupScheduler,
                                                   jobs: FakeJobScheduler) -> None:
    plan = await scheduler.schedule(make_spec(sale_at=BASE_WALL + timedelta(hours=1)))
    job_stages = {r["args"][1] for r in jobs.added}
    assert job_stages == set(WarmupStage) - {WarmupStage.TRIGGER_PURCHASE}
    assert all(r["id"].startswith("task-1:") for r in jobs.added)
    assert all(r["coalesce"] is True and r["trigger"] == "date" for r in jobs.added)
    assert {sp.job_id for sp in plan.stages if sp.job_id} == set(jobs.jobs)


async def test_register_rejects_bad_stage_and_duplicates(scheduler: WarmupScheduler) -> None:
    async def handler(ctx: WarmupContext) -> None:
        return None

    with pytest.raises(ValueError):
        scheduler.register("NOT_A_STAGE", handler)  # type: ignore[arg-type]
    scheduler.register(WarmupStage.PREPARE_BROWSER, handler)
    with pytest.raises(ValueError):
        scheduler.register(WarmupStage.PREPARE_BROWSER, handler)


async def test_start_is_idempotent_and_blocked_after_shutdown(
    clock: Clock, telemetry: TimelineRecorder, jobs: FakeJobScheduler,
) -> None:
    sched = WarmupScheduler(telemetry, job_scheduler=jobs, wall_clock=clock.wall_clock,
                            perf_counter=clock.perf_counter)
    await sched.start()
    await sched.start()
    await sched.shutdown()
    with pytest.raises(RuntimeError):
        await sched.start()


async def test_plan_of_unknown_task_raises(scheduler: WarmupScheduler) -> None:
    with pytest.raises(KeyError):
        scheduler.plan_of("nope")


async def test_dispatch_stage_records_stage_event_and_runs_handler(
    scheduler: WarmupScheduler, telemetry: TimelineRecorder, clock: Clock,
) -> None:
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock)
    await scheduler.schedule(make_spec(sale_at=BASE_WALL + timedelta(hours=1)))
    await scheduler._dispatch_stage("task-1", WarmupStage.PREPARE_BROWSER)
    assert recorder.count(WarmupStage.PREPARE_BROWSER) == 1
    event = stage_event(telemetry, WarmupStage.PREPARE_BROWSER)
    assert event is not None and event.detail["status"] == "OK"


async def test_dispatch_stage_ignores_unknown_or_finished_task(scheduler: WarmupScheduler) -> None:
    await scheduler._dispatch_stage("ghost", WarmupStage.PREPARE_BROWSER)  # 不得拋錯


async def test_active_task_ids_and_wait_until_finished(scheduler: WarmupScheduler) -> None:
    plan = await scheduler.schedule(make_spec(sale_at=BASE_WALL + timedelta(hours=1)))
    assert scheduler.active_task_ids == {"task-1"}
    assert await scheduler.wait_until_finished("task-1", timeout=0.01) is False
    plan.finished.set()
    assert await scheduler.wait_until_finished("task-1", timeout=0.01) is True
    assert scheduler.active_task_ids == set()


async def test_time_reference_property_exposes_current_offset(scheduler: WarmupScheduler) -> None:
    assert scheduler.time_reference.offset_ms == 0.0
    scheduler.update_clock_offset(25.0)
    assert scheduler.time_reference.offset_ms == 25.0


# =================================================================== FSM 驅動
async def test_prepare_browser_drives_prepare_session(scheduler: WarmupScheduler,
                                                      clock: Clock) -> None:
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock)
    fsm = FakeFsm()
    await scheduler.schedule(make_spec(sale_at=BASE_WALL + timedelta(hours=1)), fsm=fsm)
    await scheduler._dispatch_stage("task-1", WarmupStage.PREPARE_BROWSER)
    assert fsm.events == ["prepare_session"]
    assert fsm.state == "PREPARING"


async def test_check_session_success_drives_session_ready(scheduler: WarmupScheduler,
                                                          clock: Clock) -> None:
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock)
    fsm = FakeFsm()
    await scheduler.schedule(make_spec(sale_at=BASE_WALL + timedelta(hours=1)), fsm=fsm)
    await scheduler._dispatch_stage("task-1", WarmupStage.PREPARE_BROWSER)
    await scheduler._dispatch_stage("task-1", WarmupStage.CHECK_SESSION)
    assert fsm.events == ["prepare_session", "session_ready"]
    assert fsm.state == "WAITING_FOR_SALE"


async def test_attach_fsm_binds_after_schedule(scheduler: WarmupScheduler, clock: Clock) -> None:
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock)
    await scheduler.schedule(make_spec(sale_at=BASE_WALL + timedelta(hours=1)))
    fsm = FakeFsm()
    scheduler.attach_fsm("task-1", fsm)
    await scheduler._dispatch_stage("task-1", WarmupStage.PREPARE_BROWSER)
    assert fsm.events == ["prepare_session"]


async def test_send_fsm_event_failure_is_fail_closed(scheduler: WarmupScheduler,
                                                     telemetry: TimelineRecorder,
                                                     clock: Clock) -> None:
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock)
    fsm = FakeFsm(raise_on=("prepare_session",))
    plan = await scheduler.schedule(make_spec(sale_at=BASE_WALL + timedelta(hours=1)), fsm=fsm)
    await scheduler._dispatch_stage("task-1", WarmupStage.PREPARE_BROWSER)
    errors = [e.name for e in telemetry.events_of(TimelineEventType.ERROR)]
    assert "fsm_event_error:prepare_session" in errors
    assert plan.aborted is True
    assert marks(telemetry, "schedule_aborted")


# ============================================ [FROZEN-1][READINESS-SINGLE-OWNER]
async def test_late_schedule_readiness_recovers_and_reaches_sale_open(
    scheduler: WarmupScheduler, telemetry: TimelineRecorder, clock: Clock,
) -> None:
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock)
    fsm = FakeFsm()
    sale_at = clock.wall + timedelta(milliseconds=300)
    plan = await scheduler.schedule(make_spec(sale_at=sale_at), fsm=fsm)
    await scheduler.wait_until_finished("task-1", timeout=2.0)

    assert fsm.state == "SALE_OPEN"
    assert fsm.events == ["prepare_session", "session_ready", "sale_triggered"]
    order = [
        e.name for e in telemetry.events()
        if e.name in {"sale_readiness_catch_up", "sale_readiness_recovered",
                      WarmupStage.TRIGGER_PURCHASE.value}
    ]
    assert order == ["sale_readiness_catch_up", "sale_readiness_recovered",
                     WarmupStage.TRIGGER_PURCHASE.value]
    assert plan.finished.is_set()


async def test_late_schedule_readiness_marks_deferred_exactly_once(
    scheduler: WarmupScheduler, telemetry: TimelineRecorder, clock: Clock,
) -> None:
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock)
    sale_at = clock.wall + timedelta(milliseconds=300)
    await scheduler.schedule(make_spec(sale_at=sale_at), fsm=FakeFsm())
    await scheduler.wait_until_finished("task-1", timeout=2.0)

    assert len(marks(telemetry, "readiness_deferred_to_trigger_path")) == 1
    assert len(marks(telemetry, "sale_readiness_catch_up")) == 1
    assert len(marks(telemetry, "sale_readiness_recovered")) == 1
    assert marks(telemetry, "sale_readiness_reentered") == []


async def test_late_schedule_readiness_runs_every_pre_sale_stage(
    scheduler: WarmupScheduler, clock: Clock,
) -> None:
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock)
    sale_at = clock.wall + timedelta(milliseconds=300)
    await scheduler.schedule(make_spec(sale_at=sale_at), fsm=FakeFsm())
    await scheduler.wait_until_finished("task-1", timeout=2.0)

    assert set(PRE_SALE_STAGES) == set(PRE_SALE)
    for stage in PRE_SALE:
        assert recorder.count(stage) == 1, f"{stage} 未於就緒補齊中執行"
    assert recorder.count(WarmupStage.TRIGGER_PURCHASE) == 1


async def test_late_schedule_readiness_cost_is_reported_without_clipping(
    scheduler: WarmupScheduler, telemetry: TimelineRecorder, clock: Clock,
) -> None:
    """補齊耗時 800ms > 剩餘 300ms，超出的部分必須如實記為正的 sale_time_error_ms。"""
    recorder = Recorder()
    register_recording_handlers(
        scheduler, recorder, clock=clock,
        cost={stage: 0.2 for stage in PRE_SALE},
    )
    sale_at = clock.wall + timedelta(milliseconds=300)
    await scheduler.schedule(make_spec(sale_at=sale_at), fsm=FakeFsm())
    await scheduler.wait_until_finished("task-1", timeout=2.0)

    trigger = stage_event(telemetry, WarmupStage.TRIGGER_PURCHASE)
    assert trigger is not None
    error_ms = trigger.detail["sale_time_error_ms"]
    assert error_ms >= 500.0
    assert error_ms <= 700.0


async def test_readiness_without_fsm_aborts_before_running_any_stage(
    scheduler: WarmupScheduler, telemetry: TimelineRecorder, clock: Clock,
) -> None:
    """晚排程且 fsm is None：就緒補齊在跑任何 handler「之前」就 fail-closed 中止。"""
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock)
    sale_at = clock.wall + timedelta(milliseconds=300)
    plan = await scheduler.schedule(make_spec(sale_at=sale_at))
    await scheduler.wait_until_finished("task-1", timeout=2.0)

    assert plan.aborted is True
    assert recorder.calls == {}, "FSM 缺席時不得先跑任何預熱 handler"
    errors = [e.name for e in telemetry.events_of(TimelineEventType.ERROR)]
    assert "fsm_missing_before_trigger" in errors
    assert marks(telemetry, "sale_readiness_catch_up") == []


async def test_readiness_reuses_fired_stage_and_only_sends_events(
    scheduler: WarmupScheduler, clock: Clock,
) -> None:
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock)
    sale_at = clock.wall + timedelta(milliseconds=300)
    fsm = FakeFsm()
    await scheduler.schedule(make_spec(sale_at=sale_at))
    schedule = scheduler._active_tasks["task-1"]
    # 手動先跑掉一個預熱階段（此時尚未 attach FSM）
    await scheduler._run_stage(schedule, WarmupStage.PREPARE_BROWSER)
    assert recorder.count(WarmupStage.PREPARE_BROWSER) == 1
    scheduler.attach_fsm("task-1", fsm)

    assert await scheduler._ensure_sale_ready(schedule) is True
    assert recorder.count(WarmupStage.PREPARE_BROWSER) == 1, "已 fired 的階段不得重跑"
    assert fsm.events == ["prepare_session", "session_ready"]
    assert fsm.state == "WAITING_FOR_SALE"


async def test_readiness_failure_is_fail_closed_and_never_triggers_sale(
    scheduler: WarmupScheduler, telemetry: TimelineRecorder, clock: Clock,
) -> None:
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock,
                                fail={WarmupStage.CHECK_SESSION})
    fsm = FakeFsm()
    sale_at = clock.wall + timedelta(milliseconds=300)
    plan = await scheduler.schedule(make_spec(sale_at=sale_at), fsm=fsm)
    await scheduler.wait_until_finished("task-1", timeout=2.0)

    assert "sale_triggered" not in fsm.events
    assert fsm.state == "FAILED"
    assert plan.finished.is_set()
    assert plan.aborted is True


async def test_readiness_with_stuck_fsm_aborts_without_sale_trigger(
    scheduler: WarmupScheduler, telemetry: TimelineRecorder, clock: Clock,
) -> None:
    """[ABORT-LEGALITY-GATE] 卡在 PAYMENT_REQUIRED 且 abort 合法 -> 只送 abort_failed。"""
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock)
    fsm = FakeFsm(state="PAYMENT_REQUIRED", frozen=True, abort_allowed=True)
    sale_at = clock.wall + timedelta(milliseconds=300)
    await scheduler.schedule(make_spec(sale_at=sale_at), fsm=fsm)
    await scheduler.wait_until_finished("task-1", timeout=2.0)

    assert fsm.events == ["abort_failed"]
    assert "sale_triggered" not in fsm.events
    errors = [e.name for e in telemetry.events_of(TimelineEventType.ERROR)]
    assert "fsm_not_ready_for_sale" in errors


async def test_readiness_with_illegal_abort_sends_no_event_at_all(
    scheduler: WarmupScheduler, telemetry: TimelineRecorder, clock: Clock,
) -> None:
    """[ABORT-LEGALITY-GATE] can_send 為 False -> 完全不送事件，只記 fsm_abort_not_allowed。"""
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock)
    fsm = FakeFsm(state="PAYMENT_REQUIRED", frozen=True, abort_allowed=False)
    sale_at = clock.wall + timedelta(milliseconds=300)
    await scheduler.schedule(make_spec(sale_at=sale_at), fsm=fsm)
    await scheduler.wait_until_finished("task-1", timeout=2.0)

    assert fsm.events == []
    assert len(marks(telemetry, "fsm_abort_not_allowed")) >= 1


async def test_readiness_without_fsm_records_missing_fsm(
    scheduler: WarmupScheduler, telemetry: TimelineRecorder, clock: Clock,
) -> None:
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock)
    sale_at = clock.wall + timedelta(milliseconds=300)
    plan = await scheduler.schedule(make_spec(sale_at=sale_at))
    await scheduler.wait_until_finished("task-1", timeout=2.0)

    errors = [e.name for e in telemetry.events_of(TimelineEventType.ERROR)]
    assert "fsm_missing_before_trigger" in errors
    assert plan.aborted is True


async def test_normal_schedule_readiness_emits_no_recovery_events(
    scheduler: WarmupScheduler, telemetry: TimelineRecorder, clock: Clock,
) -> None:
    """非晚排程由 schedule() 的 overdue 迴圈補齊，不得出現 readiness 事件。"""
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock)
    fsm = FakeFsm()
    sale_at = clock.wall + timedelta(minutes=2)
    await scheduler.schedule(make_spec(sale_at=sale_at), fsm=fsm)
    schedule = scheduler._active_tasks["task-1"]

    assert schedule.readiness_deferred is False
    assert recorder.count(WarmupStage.PREPARE_BROWSER) == 1
    assert recorder.count(WarmupStage.CHECK_SESSION) == 1
    assert fsm.state == "WAITING_FOR_SALE"
    assert marks(telemetry, "sale_readiness_catch_up") == []
    assert marks(telemetry, "sale_readiness_recovered") == []
    assert marks(telemetry, "readiness_deferred_to_trigger_path") == []


async def test_late_spin_within_500ms_window(scheduler: WarmupScheduler, clock: Clock,
                                             jobs: FakeJobScheduler) -> None:
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock)
    sale_at = clock.wall + timedelta(milliseconds=300)
    await scheduler.schedule(make_spec(sale_at=sale_at), fsm=FakeFsm())
    schedule = scheduler._active_tasks["task-1"]
    assert schedule.spin_started is True
    assert jobs.added == []
    await scheduler.wait_until_finished("task-1", timeout=2.0)
    assert recorder.count(WarmupStage.TRIGGER_PURCHASE) == 1


async def test_post_sale_catch_up_immediate_trigger(scheduler: WarmupScheduler, clock: Clock,
                                                    telemetry: TimelineRecorder) -> None:
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock)
    sale_at = clock.wall - timedelta(milliseconds=250)
    await scheduler.schedule(make_spec(sale_at=sale_at), fsm=FakeFsm())
    await scheduler.wait_until_finished("task-1", timeout=2.0)
    trigger = stage_event(telemetry, WarmupStage.TRIGGER_PURCHASE)
    assert trigger is not None
    assert trigger.detail["sale_time_error_ms"] > 0


# =================================================== [FSM-STAGE-FAIL-CLOSED]
async def test_trigger_handler_failure_is_fail_closed(
    scheduler: WarmupScheduler, telemetry: TimelineRecorder, clock: Clock,
    jobs: FakeJobScheduler,
) -> None:
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock,
                                fail={WarmupStage.TRIGGER_PURCHASE})
    fsm = FakeFsm()
    sale_at = clock.wall + timedelta(milliseconds=300)
    plan = await scheduler.schedule(make_spec(sale_at=sale_at), fsm=fsm)
    await scheduler.wait_until_finished("task-1", timeout=2.0)

    schedule = scheduler._active_tasks["task-1"]
    assert fsm.state == "FAILED"
    assert schedule.job_ids == []
    assert all(sp.job_id is None for sp in plan.stages)
    aborted = marks(telemetry, "schedule_aborted")
    assert aborted and aborted[-1].detail["reason"] == "stage_failed:TRIGGER_PURCHASE"


async def test_spin_and_trigger_fail_closed_guard(
    scheduler: WarmupScheduler, clock: Clock,
) -> None:
    """TRIGGER 失敗時不得自我 cancel：outcome 應為 FAILED 而非 CANCELLED。"""
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock,
                                fail={WarmupStage.TRIGGER_PURCHASE})
    sale_at = clock.wall + timedelta(milliseconds=300)
    plan = await scheduler.schedule(make_spec(sale_at=sale_at), fsm=FakeFsm())
    await scheduler.wait_until_finished("task-1", timeout=2.0)
    outcome = plan.outcome_of(WarmupStage.TRIGGER_PURCHASE)
    assert outcome is not None
    assert outcome.status == "FAILED"


@pytest.mark.parametrize(
    "stage",
    [
        WarmupStage.PREPARE_BROWSER,
        WarmupStage.CHECK_SESSION,
        WarmupStage.NAVIGATE_PAGE,
        WarmupStage.ENTER_READY,
        WarmupStage.SPIN_WAIT,
    ],
)
async def test_every_stage_failure_uses_the_same_fail_closed_path(
    scheduler: WarmupScheduler, telemetry: TimelineRecorder, clock: Clock, stage: WarmupStage,
) -> None:
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock, fail={stage})
    fsm = FakeFsm()
    sale_at = clock.wall + timedelta(milliseconds=300)
    plan = await scheduler.schedule(make_spec(sale_at=sale_at), fsm=fsm)
    await scheduler.wait_until_finished("task-1", timeout=2.0)

    assert plan.aborted is True
    assert fsm.state == "FAILED"
    assert "sale_triggered" not in fsm.events
    aborted = marks(telemetry, "schedule_aborted")
    assert aborted and aborted[-1].detail["reason"] == f"stage_failed:{stage.value}"


async def test_abort_schedule_is_idempotent(scheduler: WarmupScheduler,
                                            telemetry: TimelineRecorder,
                                            clock: Clock) -> None:
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock)
    fsm = FakeFsm()
    await scheduler.schedule(make_spec(sale_at=BASE_WALL + timedelta(hours=1)), fsm=fsm)
    schedule = scheduler._active_tasks["task-1"]
    scheduler._abort_schedule(schedule, reason="first")
    scheduler._abort_schedule(schedule, reason="second")
    assert fsm.events.count("abort_failed") == 1
    assert len(marks(telemetry, "schedule_aborted")) == 1


async def test_abort_schedule_respects_final_state_guard(scheduler: WarmupScheduler,
                                                         clock: Clock) -> None:
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock)
    fsm = FakeFsm(state="COMPLETED")
    await scheduler.schedule(make_spec(sale_at=BASE_WALL + timedelta(hours=1)), fsm=fsm)
    schedule = scheduler._active_tasks["task-1"]
    scheduler._abort_schedule(schedule, reason="already-final")
    assert fsm.events == []


async def test_stage_dropped_when_already_fired(scheduler: WarmupScheduler,
                                                telemetry: TimelineRecorder,
                                                clock: Clock) -> None:
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock)
    await scheduler.schedule(make_spec(sale_at=BASE_WALL + timedelta(hours=1)))
    await scheduler._dispatch_stage("task-1", WarmupStage.PREPARE_BROWSER)
    await scheduler._dispatch_stage("task-1", WarmupStage.PREPARE_BROWSER)
    assert recorder.count(WarmupStage.PREPARE_BROWSER) == 1
    assert marks(telemetry, "stage_dropped")


async def test_spin_already_started_gate_drops_stale_job(scheduler: WarmupScheduler,
                                                         clock: Clock) -> None:
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock)
    await scheduler.schedule(make_spec(sale_at=BASE_WALL + timedelta(hours=1)),
                             fsm=FakeFsm(state="WAITING_FOR_SALE"))
    schedule = scheduler._active_tasks["task-1"]
    schedule.spin_started = True
    await scheduler._dispatch_stage("task-1", WarmupStage.SPIN_WAIT)
    assert schedule.spin_task is None
    assert recorder.count(WarmupStage.SPIN_WAIT) == 0


async def test_schedule_atomic_cleanup_on_add_job_failure(
    clock: Clock, telemetry: TimelineRecorder, factory: SyncFactory,
) -> None:
    failing_jobs = FakeJobScheduler(add_error=RuntimeError("apscheduler down"))
    sched = WarmupScheduler(telemetry, job_scheduler=failing_jobs,
                            clock_synchronizer_factory=factory,
                            wall_clock=clock.wall_clock, perf_counter=clock.perf_counter,
                            sleep=clock.sleeper())
    await sched.start()
    with pytest.raises(RuntimeError):
        await sched.schedule(make_spec(sale_at=BASE_WALL + timedelta(hours=1)))
    assert sched.active_task_ids == set()
    assert "task-1" not in sched._active_tasks
    plan = sched.plan_of("task-1")
    assert plan.aborted is True and plan.finished.is_set()
    assert all(sp.job_id is None for sp in plan.stages)
    await sched.shutdown()


async def test_safe_create_task_failure_closes_coroutine(
    scheduler: WarmupScheduler, telemetry: TimelineRecorder, clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock)
    await scheduler.schedule(make_spec(sale_at=BASE_WALL + timedelta(hours=1)))
    schedule = scheduler._active_tasks["task-1"]

    def boom(coro: Any) -> Any:
        coro.close()
        raise RuntimeError("cannot create task")

    monkeypatch.setattr(asyncio, "create_task", boom)
    coro = scheduler._spin_and_trigger(schedule)
    with pytest.raises(RuntimeError):
        scheduler._safe_create_task(coro, schedule)
    assert schedule.plan.finished.is_set()
    assert marks(telemetry, "trigger_path_aborted")


async def test_cancel_during_catchup_sets_finished(scheduler: WarmupScheduler,
                                                   clock: Clock) -> None:
    recorder = Recorder()
    gate = asyncio.Event()
    register_recording_handlers(scheduler, recorder, clock=clock,
                                gate={WarmupStage.CHECK_SESSION: gate})
    sale_at = clock.wall + timedelta(milliseconds=300)
    plan = await scheduler.schedule(make_spec(sale_at=sale_at), fsm=FakeFsm())
    await drain()
    assert scheduler.cancel("task-1") is True
    gate.set()
    await drain()
    assert plan.finished.is_set()
    assert recorder.count(WarmupStage.TRIGGER_PURCHASE) == 0


async def test_cancel_unknown_and_repeat_returns_false(scheduler: WarmupScheduler) -> None:
    assert scheduler.cancel("ghost") is False
    await scheduler.schedule(make_spec(sale_at=BASE_WALL + timedelta(hours=1)))
    assert scheduler.cancel("task-1") is True
    assert scheduler.cancel("task-1") is False


# ============================================== [REBASE-NO-EXPIRED-JOB]
async def test_update_clock_offset_below_epsilon_updates_dual_track_samples_without_job_churn(
    scheduler: WarmupScheduler, telemetry: TimelineRecorder, jobs: FakeJobScheduler,
) -> None:
    await scheduler.schedule(make_spec(sale_at=BASE_WALL + timedelta(hours=1)))
    added_before = len(jobs.added)
    removed_before = len(jobs.removed)
    samples = (
        ClockSample(source=ClockSource.NTP, offset_ms=0.2, rtt_ms=8.0),
        ClockSample(source=ClockSource.SERVER_HEADER, offset_ms=0.3, rtt_ms=12.0),
    )
    update = scheduler.update_clock_offset(0.2, ClockSource.NTP, samples=samples)

    assert isinstance(update, ClockOffsetUpdate)
    assert update.offset_ms == 0.0
    assert update.samples == samples
    assert scheduler.time_reference.samples == samples
    assert len(jobs.added) == added_before
    assert len(jobs.removed) == removed_before
    sync_events = telemetry.events_of(TimelineEventType.CLOCK_SYNC)
    assert sync_events[-1].detail["applied"] is False
    assert sync_events[-1].detail["reason"] == "below_epsilon"


async def test_update_clock_offset_rebases_future_jobs_to_exact_times(
    scheduler: WarmupScheduler, jobs: FakeJobScheduler, telemetry: TimelineRecorder,
) -> None:
    plan = await scheduler.schedule(make_spec(sale_at=BASE_WALL + timedelta(hours=1)))
    planned_before = {sp.stage: sp.planned_local_at for sp in plan.stages}
    jobs.added.clear()
    scheduler.update_clock_offset(-1000.0, ClockSource.NTP)

    for sp in plan.stages:
        assert sp.planned_local_at == planned_before[sp.stage] + timedelta(seconds=1)
    for record in jobs.added:
        stage = record["args"][1]
        assert record["run_date"] == planned_before[stage] + timedelta(seconds=1)
    sync = telemetry.events_of(TimelineEventType.CLOCK_SYNC)[-1]
    assert sync.detail["handed_off_count"] == 0
    assert sync.detail["applied"] is True


async def test_rebase_overdue_stage_is_not_rescheduled_and_is_handed_off(
    scheduler: WarmupScheduler, jobs: FakeJobScheduler, telemetry: TimelineRecorder,
    clock: Clock,
) -> None:
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock)
    plan = await scheduler.schedule(make_spec(sale_at=BASE_WALL + timedelta(minutes=11)),
                                    fsm=FakeFsm())
    prepare = plan.stage_of(WarmupStage.PREPARE_BROWSER)
    assert prepare.job_id is not None
    jobs.added.clear()

    # 大幅正向 offset -> 本機排程時點整體提前，PREPARE_BROWSER 立刻過期
    scheduler.update_clock_offset(120_000.0, ClockSource.NTP)

    assert prepare.job_id is None
    assert f"task-1:{WarmupStage.PREPARE_BROWSER.value}" in jobs.removed
    assert all(r["args"][1] is not WarmupStage.PREPARE_BROWSER for r in jobs.added)
    schedule = scheduler._active_tasks["task-1"]
    assert f"task-1:{WarmupStage.PREPARE_BROWSER.value}" not in schedule.job_ids
    sync = telemetry.events_of(TimelineEventType.CLOCK_SYNC)[-1]
    assert sync.detail["handed_off_count"] >= 1

    await drain()
    assert recorder.count(WarmupStage.PREPARE_BROWSER) == 1


async def test_rebase_overdue_stages_run_in_declaration_order(
    scheduler: WarmupScheduler, clock: Clock,
) -> None:
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock)
    await scheduler.schedule(make_spec(sale_at=BASE_WALL + timedelta(minutes=11)),
                             fsm=FakeFsm())
    scheduler.update_clock_offset(650_000.0, ClockSource.NTP)
    await drain(120)

    executed = [stage for kind, stage in recorder.trace if kind == "enter"]
    order = [s.value for s in PRE_SALE]
    seen = [s for s in executed if s in order]
    assert seen == sorted(seen, key=order.index)


async def test_rebase_handed_off_spin_starts_late_spin(
    scheduler: WarmupScheduler, jobs: FakeJobScheduler, clock: Clock,
) -> None:
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock)
    await scheduler.schedule(make_spec(sale_at=BASE_WALL + timedelta(minutes=11)),
                             fsm=FakeFsm())
    schedule = scheduler._active_tasks["task-1"]
    assert schedule.spin_started is False
    # 讓 T-500ms 過期但 T=0 尚未到（提前 10m59.8s）
    scheduler.update_clock_offset(659_800.0, ClockSource.NTP)
    await drain(120)
    assert schedule.spin_started is True
    assert schedule.spin_task is not None


async def test_rebase_past_sale_time_triggers_immediately(
    scheduler: WarmupScheduler, telemetry: TimelineRecorder, clock: Clock,
) -> None:
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock)
    await scheduler.schedule(make_spec(sale_at=BASE_WALL + timedelta(minutes=11)),
                             fsm=FakeFsm())
    scheduler.update_clock_offset(700_000.0, ClockSource.NTP)
    assert await scheduler.wait_until_finished("task-1", timeout=2.0) is True
    trigger = stage_event(telemetry, WarmupStage.TRIGGER_PURCHASE)
    assert trigger is not None
    assert trigger.detail["sale_time_error_ms"] > 0


async def test_rebase_catch_up_stops_when_task_cancelled(
    scheduler: WarmupScheduler, clock: Clock,
) -> None:
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock)
    await scheduler.schedule(make_spec(sale_at=BASE_WALL + timedelta(minutes=11)),
                             fsm=FakeFsm())
    scheduler.update_clock_offset(650_000.0, ClockSource.NTP)
    scheduler.cancel("task-1")
    await drain(60)
    assert recorder.count(WarmupStage.TRIGGER_PURCHASE) == 0


async def test_fired_stage_is_skipped_by_rebase(scheduler: WarmupScheduler, clock: Clock) -> None:
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock)
    await scheduler.schedule(make_spec(sale_at=BASE_WALL + timedelta(minutes=11)),
                             fsm=FakeFsm())
    await scheduler._dispatch_stage("task-1", WarmupStage.PREPARE_BROWSER)
    assert recorder.count(WarmupStage.PREPARE_BROWSER) == 1
    scheduler.update_clock_offset(650_000.0, ClockSource.NTP)
    await drain(60)
    assert recorder.count(WarmupStage.PREPARE_BROWSER) == 1


async def test_shutdown_drains_background_tasks(scheduler: WarmupScheduler, clock: Clock,
                                                jobs: FakeJobScheduler) -> None:
    recorder = Recorder()
    gate = asyncio.Event()
    register_recording_handlers(scheduler, recorder, clock=clock,
                                gate={WarmupStage.NAVIGATE_PAGE: gate})
    await scheduler.schedule(make_spec(sale_at=BASE_WALL + timedelta(minutes=11)),
                             fsm=FakeFsm())
    scheduler.update_clock_offset(650_000.0, ClockSource.NTP)
    await drain(10)
    gate.set()
    await scheduler.shutdown()
    assert scheduler._background_tasks == set()
    assert jobs.shutdown_calls == [True]


async def test_shutdown_is_idempotent(scheduler: WarmupScheduler, jobs: FakeJobScheduler) -> None:
    await scheduler.shutdown()
    await scheduler.shutdown()
    assert len(jobs.shutdown_calls) == 1


# ============================================== [CLOCK-SYNC-PER-TASK]
async def test_each_task_gets_its_own_synchronizer(scheduler: WarmupScheduler,
                                                   factory: SyncFactory) -> None:
    await scheduler.schedule(make_spec("task-a", sale_at=BASE_WALL + timedelta(hours=1),
                                       event_url="https://one.example.com/events/a"))
    await scheduler.schedule(make_spec("task-b", sale_at=BASE_WALL + timedelta(hours=1),
                                       event_url="https://two.example.com/events/b"))

    sync_a = scheduler._active_tasks["task-a"].clock_sync
    sync_b = scheduler._active_tasks["task-b"].clock_sync
    assert sync_a is not sync_b
    assert sync_a.server_url == "https://one.example.com/events/a"
    assert sync_b.server_url == "https://two.example.com/events/b"
    assert factory.calls == [
        "https://one.example.com/events/a",
        "https://two.example.com/events/b",
    ]


async def test_default_factory_builds_clock_synchronizer_per_event_url(
    clock: Clock, telemetry: TimelineRecorder, jobs: FakeJobScheduler,
) -> None:
    from scheduler.clock_sync import ClockSynchronizer

    sched = WarmupScheduler(telemetry, job_scheduler=jobs, wall_clock=clock.wall_clock,
                            perf_counter=clock.perf_counter, sleep=clock.sleeper())
    await sched.start()
    await sched.schedule(make_spec("task-a", sale_at=BASE_WALL + timedelta(hours=1),
                                   event_url="https://one.example.com/a"))
    await sched.schedule(make_spec("task-b", sale_at=BASE_WALL + timedelta(hours=1),
                                   event_url="https://two.example.com/b"))
    sync_a = sched._active_tasks["task-a"].clock_sync
    sync_b = sched._active_tasks["task-b"].clock_sync
    assert isinstance(sync_a, ClockSynchronizer) and isinstance(sync_b, ClockSynchronizer)
    assert sync_a.server_url == "https://one.example.com/a"
    assert sync_b.server_url == "https://two.example.com/b"
    await sched.shutdown()


async def test_explicit_synchronizer_is_shared_and_factory_unused(
    clock: Clock, telemetry: TimelineRecorder, jobs: FakeJobScheduler, factory: SyncFactory,
) -> None:
    shared = FakeSynchronizer("https://shared.example.com/x")
    sched = WarmupScheduler(telemetry, job_scheduler=jobs, clock_synchronizer=shared,
                            clock_synchronizer_factory=factory,
                            wall_clock=clock.wall_clock, perf_counter=clock.perf_counter,
                            sleep=clock.sleeper())
    await sched.start()
    await sched.schedule(make_spec("task-a", sale_at=BASE_WALL + timedelta(hours=1)))
    await sched.schedule(make_spec("task-b", sale_at=BASE_WALL + timedelta(hours=1)))
    assert sched._active_tasks["task-a"].clock_sync is shared
    assert sched._active_tasks["task-b"].clock_sync is shared
    assert factory.calls == []
    await sched.shutdown()


async def test_attach_clock_synchronizer_overrides_existing_tasks(
    scheduler: WarmupScheduler,
) -> None:
    await scheduler.schedule(make_spec(sale_at=BASE_WALL + timedelta(hours=1)))
    shared = FakeSynchronizer("https://shared.example.com/x")
    scheduler.attach_clock_synchronizer(shared, stages=[WarmupStage.CHECK_SESSION])
    assert scheduler._active_tasks["task-1"].clock_sync is shared
    assert scheduler._resync_stages == frozenset({WarmupStage.CHECK_SESSION})


async def test_resync_stage_records_dual_track_samples(
    scheduler: WarmupScheduler, telemetry: TimelineRecorder, clock: Clock,
    factory: SyncFactory,
) -> None:
    recorder = Recorder()
    register_recording_handlers(scheduler, recorder, clock=clock)
    await scheduler.schedule(make_spec(sale_at=BASE_WALL + timedelta(hours=1)), fsm=FakeFsm())
    await scheduler._dispatch_stage("task-1", WarmupStage.PREPARE_BROWSER)
    await scheduler._dispatch_stage("task-1", WarmupStage.CHECK_SESSION)

    assert factory.created[0].refresh_calls == 1
    sources = [e.detail["source"] for e in telemetry.events_of(TimelineEventType.CLOCK_SYNC)]
    assert "NTP" in sources and "SERVER_HEADER" in sources


async def test_resync_failure_is_recorded_and_non_fatal(
    clock: Clock, telemetry: TimelineRecorder, jobs: FakeJobScheduler,
) -> None:
    failing = SyncFactory(error=RuntimeError("ntp down"))
    sched = WarmupScheduler(telemetry, job_scheduler=jobs, clock_synchronizer_factory=failing,
                            wall_clock=clock.wall_clock, perf_counter=clock.perf_counter,
                            sleep=clock.sleeper())
    await sched.start()
    recorder = Recorder()
    register_recording_handlers(sched, recorder, clock=clock)
    await sched.schedule(make_spec(sale_at=BASE_WALL + timedelta(hours=1)), fsm=FakeFsm())
    await sched._dispatch_stage("task-1", WarmupStage.PREPARE_BROWSER)
    await sched._dispatch_stage("task-1", WarmupStage.CHECK_SESSION)

    errors = [e.name for e in telemetry.events_of(TimelineEventType.ERROR)]
    assert f"clock_resync:{WarmupStage.CHECK_SESSION.value}" in errors
    assert recorder.count(WarmupStage.CHECK_SESSION) == 1
    await sched.shutdown()


# ============================================== [STAGE-SERIALIZED]
async def test_stage_execution_is_serialized_against_rebase_catch_up(
    scheduler: WarmupScheduler, clock: Clock,
) -> None:
    """gated-handler 競態回歸鎖定：背景 catch-up 不得在 handler 仍 await 時越級執行。"""
    recorder = Recorder()
    gate = asyncio.Event()
    register_recording_handlers(scheduler, recorder, clock=clock,
                                gate={WarmupStage.CHECK_SESSION: gate})
    fsm = FakeFsm()
    await scheduler.schedule(make_spec(sale_at=BASE_WALL + timedelta(minutes=11)), fsm=fsm)
    schedule = scheduler._active_tasks["task-1"]
    await scheduler._dispatch_stage("task-1", WarmupStage.PREPARE_BROWSER)

    check_task = asyncio.create_task(scheduler._run_stage(schedule, WarmupStage.CHECK_SESSION))
    await drain(5)
    assert recorder.trace[-1] == ("enter", WarmupStage.CHECK_SESSION.value)

    # handler 仍卡住時觸發 rebase catch-up
    scheduler.update_clock_offset(650_000.0, ClockSource.NTP)
    await drain(20)
    assert recorder.trace[-1] == ("enter", WarmupStage.CHECK_SESSION.value), \
        "背景 catch-up 在 handler 尚未結束前就越級執行了"
    assert "session_ready" not in fsm.events

    gate.set()
    await check_task
    await drain(60)

    # 進出必須嚴格成對，證明沒有交錯
    for i in range(0, len(recorder.trace), 2):
        assert recorder.trace[i][0] == "enter"
        assert recorder.trace[i + 1] == ("exit", recorder.trace[i][1])
    assert fsm.events.count("session_ready") == 1
    ready_index = fsm.events.index("session_ready")
    assert "sale_triggered" not in fsm.events[:ready_index]


async def test_run_stage_and_readiness_are_lock_wrappers() -> None:
    for name in ("_run_stage", "_ensure_sale_ready", "_spin_and_trigger"):
        source = inspect.getsource(getattr(WarmupScheduler, name))
        assert "async with schedule.stage_lock" in source, name
    assert hasattr(WarmupScheduler, "_run_stage_locked")
    assert hasattr(WarmupScheduler, "_ensure_sale_ready_locked")


async def test_task_schedule_declares_serialization_fields() -> None:
    fields = TaskSchedule.__dataclass_fields__
    for name in ("stage_lock", "clock_sync", "readiness_deferred", "readiness_reported"):
        assert name in fields


async def test_pre_sale_stages_exclude_spin_and_trigger() -> None:
    assert PRE_SALE_STAGES == PRE_SALE
    assert WarmupStage.SPIN_WAIT not in PRE_SALE_STAGES
    assert WarmupStage.TRIGGER_PURCHASE not in PRE_SALE_STAGES
