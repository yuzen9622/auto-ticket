from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Coroutine, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Protocol

from domain.task import PurchaseTaskSpec
from scheduler.clock_sync import (
    DEFAULT_NTP_HOST,
    ClockOffsetUpdate,
    ClockSample,
    ClockSource,
    ClockSynchronizer,
    ClockSynchronizerLike,
    TimeReference,
)
from telemetry.timeline import TimelineEventType, TimelineRecorder


class WarmupStage(str, Enum):
    PREPARE_BROWSER = "PREPARING_BROWSER"
    CHECK_SESSION = "CHECKING_SESSION"
    NAVIGATE_PAGE = "NAVIGATING_EVENT"
    ENTER_READY = "EVENT_PAGE_READY"
    SPIN_WAIT = "SPIN_WAIT"
    TRIGGER_PURCHASE = "TRIGGER_PURCHASE"


DEFAULT_STAGE_OFFSETS: Mapping[WarmupStage, timedelta] = MappingProxyType({
    WarmupStage.PREPARE_BROWSER: timedelta(minutes=10),
    WarmupStage.CHECK_SESSION: timedelta(minutes=5),
    WarmupStage.NAVIGATE_PAGE: timedelta(minutes=1),
    WarmupStage.ENTER_READY: timedelta(seconds=10),
    WarmupStage.SPIN_WAIT: timedelta(milliseconds=500),
    WarmupStage.TRIGGER_PURCHASE: timedelta(seconds=0),
})

DEFAULT_RESYNC_STAGES = frozenset({
    WarmupStage.CHECK_SESSION,
    WarmupStage.NAVIGATE_PAGE,
})

# 以字串常數本地宣告，避免 scheduler -> fsm 的模組耦合。
# 必須與 fsm.states.FINAL_STATES 一致（由驗收指令 #27 機械化比對）。
FSM_FINAL_STATE_IDS: frozenset[str] = frozenset({"COMPLETED", "SOLD_OUT", "TIMEOUT", "FAILED"})

# 就緒補齊路徑：(階段, 合法來源狀態, 應送事件)
SALE_READINESS_PATH: tuple[tuple[WarmupStage, str, str], ...] = (
    (WarmupStage.PREPARE_BROWSER, "IDLE", "prepare_session"),
    (WarmupStage.CHECK_SESSION, "PREPARING", "session_ready"),
)
SALE_READINESS_EVENTS: Mapping[WarmupStage, tuple[str, str]] = MappingProxyType({
    stage: (expected_from, event) for stage, expected_from, event in SALE_READINESS_PATH
})

# 開賣前必須完成的所有預熱階段（宣告順序即執行順序）。
# 就緒補齊不只補 SALE_READINESS_PATH 的兩個 FSM 驅動階段：NAVIGATE_PAGE / ENTER_READY
# 的 handler 是真正把頁面帶到可下單狀態的工作，晚排程同樣不得跳過。
PRE_SALE_STAGES: tuple[WarmupStage, ...] = tuple(
    st for st in WarmupStage
    if st not in {WarmupStage.SPIN_WAIT, WarmupStage.TRIGGER_PURCHASE}
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class StagePlan:
    stage: WarmupStage
    target_true_at: datetime
    planned_local_at: datetime
    deadline_perf: float
    overdue: bool
    job_id: str | None = None


@dataclass(slots=True)
class StageOutcome:
    stage: WarmupStage
    status: str
    drift_us: int
    error: BaseException | None = None
    executed_at: datetime | None = None


@dataclass(slots=True)
class WarmupPlan:
    task_id: str
    sale_start_at: datetime
    target_true_at: datetime
    target_local_at: datetime
    stages: tuple[StagePlan, ...]
    time_reference: TimeReference
    outcomes: list[StageOutcome] = field(default_factory=list)
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    aborted: bool = False

    def stage_of(self, stage: WarmupStage) -> StagePlan:
        for s in self.stages:
            if s.stage == stage:
                return s
        raise KeyError(f"Stage {stage} not in plan")

    def outcome_of(self, stage: WarmupStage) -> StageOutcome | None:
        for o in self.outcomes:
            if o.stage == stage:
                return o
        return None


@dataclass(slots=True)
class WarmupContext:
    task_id: str
    stage: WarmupStage
    planned_local_at: datetime
    fired_local_at: datetime
    drift_us: int
    time_reference: TimeReference


@dataclass(slots=True)
class TaskSchedule:
    task_id: str
    plan: WarmupPlan
    job_ids: list[str]
    fsm: Any | None = None
    spin_task: asyncio.Task[None] | None = None
    spin_started: bool = False
    cancelled: bool = False
    failed: bool = False
    fired_stages: set[WarmupStage] = field(default_factory=set)
    # 每個 task 一把鎖：階段執行、就緒補齊、rebase catch-up 全部序列化。
    # 嚴禁在 handler 內回頭呼叫 scheduler 的階段 API（會自鎖）。
    stage_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # 本 task 專屬的時鐘同步器（依自身 event_url 組裝）。
    clock_sync: Any | None = None
    # 建立當下已進入 T-500ms 視窗 -> 就緒補齊延後給
    # _ensure_sale_ready() 單一 owner 處理；schedule() 不得先跑 overdue 預熱階段。
    readiness_deferred: bool = False
    readiness_reported: bool = False


class JobScheduler(Protocol):
    def add_job(
        self,
        func: Any,
        trigger: str,
        run_date: datetime,
        args: tuple[Any, ...],
        id: str,
        misfire_grace_time: int | None,
        coalesce: bool,
    ) -> Any: ...
    def remove_job(self, job_id: str) -> None: ...
    def shutdown(self, wait: bool = True) -> None: ...
    @property
    def running(self) -> bool: ...
    def start(self) -> None: ...


class WarmupScheduler:
    def __init__(
        self,
        telemetry: TimelineRecorder,
        *,
        job_scheduler: JobScheduler | None = None,
        clock_synchronizer: ClockSynchronizerLike | None = None,
        clock_synchronizer_factory: Callable[[str | None], ClockSynchronizerLike] | None = None,
        time_reference: TimeReference | None = None,
        resync_stages: Iterable[WarmupStage] = DEFAULT_RESYNC_STAGES,
        offset_epsilon_ms: float = 0.5,
        stage_offsets: Mapping[WarmupStage, timedelta] = DEFAULT_STAGE_OFFSETS,
        wall_clock: Callable[[], datetime] = _utc_now,
        perf_counter: Callable[[], float] = time.perf_counter,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        spin_sleep_threshold_s: float = 0.005,
        misfire_grace_time: int | None = None,
    ) -> None:
        self._telemetry = telemetry
        self._time_reference = time_reference or TimeReference(
            offset_ms=0.0, samples=(), primary_source=None,
            monotonic_anchor_perf=perf_counter(), monotonic_anchor_wall=wall_clock(),
        )
        if job_scheduler is None:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            self._jobs: JobScheduler = AsyncIOScheduler()
        else:
            self._jobs = job_scheduler
        # `clock_synchronizer` = 呼叫端明示共用的覆寫（所有 task 共用）；
        # 未指定時改由 factory 依「各 task 自己的 event_url」逐一組裝，
        # 避免單一 synchronizer 的 server_url 被第一個 task 永久釘死。
        self._clock_sync = clock_synchronizer
        self._clock_sync_factory: Callable[[str | None], ClockSynchronizerLike] = (
            clock_synchronizer_factory or self._default_clock_sync_factory
        )
        self._resync_stages = frozenset(resync_stages)
        self._offset_epsilon_ms = offset_epsilon_ms
        self._stage_offsets = dict(stage_offsets)
        self._wall = wall_clock
        self._perf = perf_counter
        self._sleep = sleep
        self._spin_sleep_threshold_s = spin_sleep_threshold_s
        self._misfire_grace_time = misfire_grace_time

        self._plans: dict[str, WarmupPlan] = {}
        self._active_tasks: dict[str, TaskSchedule] = {}
        self._handlers: dict[WarmupStage, Callable[[WarmupContext], Awaitable[None]]] = {}
        # rebase 後接手過期階段的本地協程；由 shutdown() 一併排空。
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._started = False
        self._shutdown = False

    def _default_clock_sync_factory(self, server_url: str | None) -> ClockSynchronizerLike:
        """每個 task 一個同步器。"""
        return ClockSynchronizer(
            ntp_host=DEFAULT_NTP_HOST,
            server_url=server_url,
            perf_counter=self._perf,
            wall_clock=self._wall,
        )

    def register(self, stage: WarmupStage, handler: Callable[[WarmupContext], Awaitable[None]]) -> None:
        if not isinstance(stage, WarmupStage):
            raise ValueError(f"Invalid WarmupStage: {stage}")
        if stage in self._handlers:
            raise ValueError(f"Handler already registered for stage: {stage}")
        self._handlers[stage] = handler

    async def start(self) -> None:
        if self._started:
            return
        if self._shutdown:
            raise RuntimeError("scheduler already shut down")
        if self._jobs is not None and not self._jobs.running:
            self._jobs.start()
        self._started = True
        self._telemetry.record(TimelineEventType.MARK, "scheduler_started")

    def _safe_create_task(self, coro: Coroutine[Any, Any, None], schedule: TaskSchedule) -> asyncio.Task[None]:
        try:
            task = asyncio.create_task(coro)
        except BaseException as exc:
            coro.close()
            schedule.plan.finished.set()
            self._telemetry.record(
                TimelineEventType.MARK, "trigger_path_aborted",
                task_id=schedule.task_id, error=repr(exc),
            )
            raise
        task.add_done_callback(lambda t: self._on_spin_task_done(schedule, t))
        return task

    def _on_spin_task_done(self, schedule: TaskSchedule, task: asyncio.Task[None]) -> None:
        pass

    def attach_fsm(self, task_id: str, fsm: Any) -> None:
        sched = self._active_tasks.get(task_id)
        if sched is not None:
            sched.fsm = fsm

    def _send_fsm_event(self, schedule: TaskSchedule, event_name: str) -> bool:
        """FSM 事件唯一送出點。

        呼叫端必須事先確認狀態合法；本函式只負責送出與失敗收斂。
        任何例外（含 TransitionNotAllowed）都視為致命：記錄後原子化中止，回傳 False。
        """
        fsm = schedule.fsm
        if fsm is None:
            return False
        try:
            bound = getattr(fsm, event_name, None)
            if callable(bound):
                bound()
            elif hasattr(fsm, "send"):
                fsm.send(event_name)
            else:
                raise AttributeError(f"FSM exposes no event {event_name!r}")
        except Exception as exc:
            self._telemetry.record_error(
                f"fsm_event_error:{event_name}", exc, task_id=schedule.task_id,
            )
            self._abort_schedule(schedule, reason=f"fsm_event_failed:{event_name}")
            return False
        return True

    def _drive_fsm_abort_failed(self, schedule: TaskSchedule) -> None:
        """推進 FSM 至 FAILED 終態。

        兩道守衛，缺一不可：
        1) 已在終態 -> 直接跳過；
        2) `fsm.can_send("abort_failed")` 為 False -> **不送任何事件**，只記錄
           `fsm_abort_not_allowed`。真實 `PurchaseWorkflow` 的 `abort_failed` 已涵蓋全部
           10 個非終態，正常情況必然合法；但排程器對 FSM 只做鴨子型別假設（測試會注入
           stub），沒有這道閘門就會對不支援的實作丟出 TransitionNotAllowed。
        未提供 `can_send` 的實作視為「不保證合法」——仍嘗試送出，但例外會被下方捕捉。
        """
        fsm = schedule.fsm
        if fsm is None:
            return
        try:
            current_id = self._get_fsm_state_id(fsm)
            if current_id in FSM_FINAL_STATE_IDS:
                return
            can_send = getattr(fsm, "can_send", None)
            if callable(can_send) and not can_send("abort_failed"):
                self._telemetry.record(
                    TimelineEventType.MARK, "fsm_abort_not_allowed",
                    task_id=schedule.task_id, current_fsm_state=str(current_id),
                )
                return
            if hasattr(fsm, "abort_failed"):
                fsm.abort_failed()
            elif hasattr(fsm, "send"):
                fsm.send("abort_failed")
        except Exception as exc:
            self._telemetry.record_error(
                "fsm_abort_failed_error", exc, task_id=schedule.task_id,
            )

    def _abort_schedule(self, schedule: TaskSchedule, *, reason: str) -> None:
        """唯一的原子化中止入口。

        任何階段（含 TRIGGER_PURCHASE）失敗、或 FSM 狀態不符時都必須走這裡：
        1) 標記 failed/aborted -> 2) 取消所有後續 jobs -> 3) 取消 spin task
        4) 推進 FSM 至 FAILED -> 5) 記錄並喚醒等待者。
        冪等：重複呼叫不會重送 FSM 事件（終態守衛），也不會重複移除 job。
        """
        already = schedule.failed and schedule.plan.aborted
        schedule.failed = True
        schedule.plan.aborted = True

        if self._jobs is not None:
            for jid in tuple(schedule.job_ids):
                try:
                    self._jobs.remove_job(jid)
                except Exception:
                    pass
            schedule.job_ids.clear()
        for sp in schedule.plan.stages:
            sp.job_id = None

        # 若中止是由 spin 協程自身觸發（例如 TRIGGER_PURCHASE handler 失敗），
        # 絕不自我 cancel，否則 CancelledError 會覆蓋真正的失敗原因。
        spin_task = schedule.spin_task
        if (
            spin_task is not None
            and not spin_task.done()
            and spin_task is not asyncio.current_task()
        ):
            spin_task.cancel()

        self._drive_fsm_abort_failed(schedule)
        if not already:
            self._telemetry.record(
                TimelineEventType.MARK, "schedule_aborted",
                task_id=schedule.task_id, reason=reason,
            )
        schedule.plan.finished.set()

    async def schedule(
        self,
        task_or_spec: str | PurchaseTaskSpec,
        sale_start_at: datetime | None = None,
        fsm: Any | None = None,
        server_url: str | None = None,
    ) -> WarmupPlan:
        if not self._started:
            raise RuntimeError("start() must be called before schedule()")

        if isinstance(task_or_spec, PurchaseTaskSpec):
            task_id = task_or_spec.task_id
            target_sale_time = task_or_spec.sale_start_at
            resolved_server_url = server_url or task_or_spec.event_url
        else:
            task_id = task_or_spec
            if sale_start_at is None:
                raise ValueError("sale_start_at must be provided when task_id is str")
            target_sale_time = sale_start_at
            resolved_server_url = server_url

        if target_sale_time.tzinfo is None:
            raise ValueError("sale_start_at must be tz-aware UTC datetime")

        # 雙軌時鐘同步器裝配。
        # 呼叫端明示注入（建構參數或 attach_clock_synchronizer）-> 全域共用該實例；
        # 否則每個 task 用 factory 依「自己的 event_url」各組裝一個。
        # 嚴禁改回「共用單一 ClockSynchronizer 並寫入其 _server_url」的舊寫法：
        # 那會讓第二個 task 的 event_url 被第一個永久釘死（G16 靜態鎖定）。
        task_clock_sync: ClockSynchronizerLike = (
            self._clock_sync
            if self._clock_sync is not None
            else self._clock_sync_factory(resolved_server_url)
        )

        existing = self._active_tasks.get(task_id)
        if existing is not None and not existing.cancelled and not existing.plan.finished.is_set():
            raise ValueError(f"task {task_id} already scheduled")

        target_local = self._time_reference.to_local(target_sale_time)
        anchor_wall = self._wall()
        anchor_perf = self._perf()

        stage_plans: list[StagePlan] = []
        for stage in WarmupStage:
            planned = target_local - self._stage_offsets[stage]
            deadline = anchor_perf + (planned - anchor_wall).total_seconds()
            overdue = planned <= anchor_wall
            stage_plans.append(StagePlan(
                stage=stage,
                target_true_at=target_sale_time,
                planned_local_at=planned,
                deadline_perf=deadline,
                overdue=overdue,
            ))

        plan = WarmupPlan(
            task_id=task_id,
            sale_start_at=target_sale_time,
            target_true_at=target_sale_time,
            target_local_at=target_local,
            stages=tuple(stage_plans),
            time_reference=self._time_reference,
        )
        self._plans[task_id] = plan
        schedule = TaskSchedule(
            task_id=task_id, plan=plan, job_ids=[], fsm=fsm, clock_sync=task_clock_sync,
        )
        self._active_tasks[task_id] = schedule

        try:
            spin_plan = plan.stage_of(WarmupStage.SPIN_WAIT)
            trigger_plan = plan.stage_of(WarmupStage.TRIGGER_PURCHASE)

            # 0. 就緒補齊的**單一 owner** 判定。
            # 若排程建立當下就已落在 T-500ms 視窗內（spin 階段已過期），本函式
            # **不得**先跑 overdue 預熱階段：那會在抵達 _ensure_sale_ready() 之前
            # 就把 FSM 推到 WAITING_FOR_SALE，使 sale_readiness_catch_up /
            # sale_readiness_recovered 永遠不會被記錄，與 8.4 / 8.7 的必測順序矛盾。
            # 這種情況一律標記 readiness_deferred，整段補齊交給 _ensure_sale_ready()。
            entry_wall = self._wall()
            schedule.readiness_deferred = spin_plan.planned_local_at <= entry_wall
            if schedule.readiness_deferred:
                self._telemetry.record(
                    TimelineEventType.MARK, "readiness_deferred_to_trigger_path",
                    task_id=task_id,
                )

            # 1. 晚排程動態重新錨定：依序執行 overdue 預熱階段
            #    （僅適用非 deferred 路徑；deferred 由 _ensure_sale_ready() 全權負責）
            if not schedule.readiness_deferred:
                for sp in plan.stages:
                    if sp.stage in {WarmupStage.SPIN_WAIT, WarmupStage.TRIGGER_PURCHASE}:
                        continue
                    # 每輪動態更新目前牆鐘，防止長時間執行後 overdue 標記失真
                    current_loop_wall = self._wall()
                    if sp.planned_local_at <= current_loop_wall:
                        sp.overdue = True

                    if sp.overdue:
                        if schedule.cancelled or schedule.failed or plan.aborted:
                            plan.outcomes.append(StageOutcome(sp.stage, "SKIPPED", 0))
                            continue
                        await self._run_stage(schedule, sp.stage)
                        outcome = plan.outcome_of(sp.stage)
                        if outcome is not None and outcome.status == "FAILED":
                            schedule.failed = True
                            plan.aborted = True
                            break

            # 2. 三路徑動態重新錨定分流
            # 必須以最新採樣的 dynamic_wall 進行分流比較，嚴禁使用靜態的 anchor_wall！
            dynamic_wall = self._wall()

            if schedule.failed or plan.aborted:
                plan.finished.set()
            elif spin_plan.planned_local_at > dynamic_wall:
                # Normal 路徑：僅排入未來仍有時間觸發的 APScheduler jobs
                if self._jobs is not None:
                    for sp in plan.stages:
                        if (
                            sp.stage == WarmupStage.TRIGGER_PURCHASE
                            or sp.overdue
                            or sp.planned_local_at <= dynamic_wall
                        ):
                            continue
                        jid = f"{task_id}:{sp.stage.value}"
                        sp.job_id = jid
                        schedule.job_ids.append(jid)
                        self._jobs.add_job(
                            self._dispatch_stage,
                            trigger="date",
                            run_date=sp.planned_local_at,
                            args=(task_id, sp.stage),
                            id=jid,
                            misfire_grace_time=self._misfire_grace_time,
                            coalesce=True,
                        )
            elif dynamic_wall < trigger_plan.planned_local_at:
                # Late-spin 路徑 (T-500ms ~ T=0)：直接啟動 spin 任務
                spin_plan.job_id = None
                schedule.spin_started = True
                schedule.spin_task = self._safe_create_task(self._spin_and_trigger(schedule), schedule)
            else:
                # Post-sale 路徑 (T >= 0)：立即 inline 執行觸發
                spin_plan.job_id = None
                trigger_plan.job_id = None
                schedule.spin_started = True
                await self._spin_and_trigger(schedule)

            return plan
        except BaseException:
            # 原子清理回滾
            for jid in tuple(schedule.job_ids):
                if self._jobs is not None:
                    try:
                        self._jobs.remove_job(jid)
                    except Exception:
                        pass
            schedule.job_ids.clear()
            for sp in plan.stages:
                sp.job_id = None
            if schedule.spin_task is not None and not schedule.spin_task.done():
                schedule.spin_task.cancel()
            self._active_tasks.pop(task_id, None)
            plan.aborted = True
            plan.finished.set()
            self._telemetry.record(TimelineEventType.MARK, "schedule_aborted", task_id=task_id)
            raise

    async def _dispatch_stage(self, task_id: str, stage: WarmupStage) -> None:
        schedule = self._active_tasks.get(task_id)
        if schedule is None or schedule.cancelled or schedule.failed or schedule.plan.aborted:
            return
        if stage == WarmupStage.SPIN_WAIT:
            if not schedule.spin_started:
                schedule.spin_started = True
                schedule.spin_task = self._safe_create_task(self._spin_and_trigger(schedule), schedule)
        else:
            await self._run_stage(schedule, stage)

    async def _run_stage(self, schedule: TaskSchedule, stage: WarmupStage) -> None:
        """對外唯一入口：取 per-task 鎖後委派 _run_stage_locked()。

        鎖必須涵蓋「fired 標記 -> FSM 前置驅動 -> handler await -> FSM 後置驅動」整段。
        少了它，live-rebase 的背景 catch-up 協程會在某個 handler 正在 await 時越級執行
        後續階段；更糟的是 fired 標記已先設下，補齊路徑會誤判該階段「已完成」而直接
        送出 session_ready，在 CHECK_SESSION 尚未回來時就把 FSM 推進去。
        """
        async with schedule.stage_lock:
            await self._run_stage_locked(schedule, stage)

    async def _run_stage_locked(self, schedule: TaskSchedule, stage: WarmupStage) -> None:
        """呼叫端必須已持有 schedule.stage_lock。"""
        if stage in schedule.fired_stages:
            self._telemetry.record(TimelineEventType.MARK, "stage_dropped", task_id=schedule.task_id, stage=stage.value)
            return

        now_wall = self._wall()
        now_perf = self._perf()
        sp = schedule.plan.stage_of(stage)

        extra_detail: dict[str, Any] = {}
        if stage == WarmupStage.TRIGGER_PURCHASE:
            drift_ms = (now_perf - sp.deadline_perf) * 1000.0
            drift_us = int(drift_ms * 1000)
            extra_detail["sale_time_error_ms"] = round(drift_ms, 3)
        else:
            drift_us = int((now_wall - sp.planned_local_at).total_seconds() * 1_000_000)

        schedule.fired_stages.add(stage)

        # 雙軌並行時鐘同步重校準（一律用本 task 專屬同步器）
        task_clock_sync = schedule.clock_sync if schedule.clock_sync is not None else self._clock_sync
        if stage in self._resync_stages and task_clock_sync is not None:
            try:
                ref = await task_clock_sync.refresh()
            except Exception as exc:
                self._telemetry.record_error(f"clock_resync:{stage.value}", exc)
            else:
                if ref.samples:
                    for s in ref.samples:
                        self._telemetry.record_clock_sync(
                            source=s.source.value,
                            previous_offset_ms=self._time_reference.offset_ms,
                            offset_ms=s.offset_ms,
                            delta_ms=s.offset_ms - self._time_reference.offset_ms,
                            applied=(s.source == ref.primary_source),
                            stage=stage.value,
                            rtt_ms=s.rtt_ms,
                        )
                    self.update_clock_offset(
                        ref.offset_ms,
                        ref.primary_source or ClockSource.MANUAL,
                        samples=ref.samples,
                    )
                else:
                    self._telemetry.record_clock_sync(
                        source="NONE", previous_offset_ms=self._time_reference.offset_ms,
                        offset_ms=self._time_reference.offset_ms, delta_ms=0.0,
                        applied=False, reason="no_samples", stage=stage.value,
                    )

        self._telemetry.record_stage(
            stage.value,
            planned_at=sp.planned_local_at,
            fired_at=now_wall,
            drift_us=drift_us,
            status="OK",
            **extra_detail,
        )

        # 階段與狀態機明確驅動契約：前置觸發
        if schedule.fsm is not None:
            current_id = self._get_fsm_state_id(schedule.fsm)
            if stage == WarmupStage.PREPARE_BROWSER and current_id == "IDLE":
                if not self._send_fsm_event(schedule, "prepare_session"):
                    schedule.plan.outcomes.append(
                        StageOutcome(stage, "FAILED", drift_us, executed_at=now_wall)
                    )
                    return
            elif stage == WarmupStage.TRIGGER_PURCHASE:
                # 絕不向狀態不符的 FSM 發送 sale_triggered。抵達此處前
                # _ensure_sale_ready() 已完成就緒補齊，仍不符即為致命異常。
                if current_id != "WAITING_FOR_SALE":
                    self._telemetry.record_error(
                        "fsm_not_ready_for_sale",
                        RuntimeError(
                            f"FSM in invalid state before trigger: {current_id}, "
                            "expected WAITING_FOR_SALE"
                        ),
                        task_id=schedule.task_id,
                        current_fsm_state=str(current_id),
                    )
                    schedule.plan.outcomes.append(
                        StageOutcome(stage, "FAILED", drift_us, executed_at=now_wall)
                    )
                    self._abort_schedule(schedule, reason=f"fsm_not_ready:{current_id}")
                    return
                if not self._send_fsm_event(schedule, "sale_triggered"):
                    schedule.plan.outcomes.append(
                        StageOutcome(stage, "FAILED", drift_us, executed_at=now_wall)
                    )
                    return

        handler = self._handlers.get(stage)
        outcome_status = "OK"
        outcome_error: BaseException | None = None
        if handler is not None:
            ctx = WarmupContext(
                task_id=schedule.task_id,
                stage=stage,
                planned_local_at=sp.planned_local_at,
                fired_local_at=now_wall,
                drift_us=drift_us,
                time_reference=self._time_reference,
            )
            try:
                res = await handler(ctx)
                if res == "FAILED" or res is False:
                    outcome_status = "FAILED"
            except asyncio.CancelledError:
                schedule.plan.outcomes.append(StageOutcome(stage, "CANCELLED", drift_us, executed_at=now_wall))
                raise
            except Exception as exc:
                self._telemetry.record_error(f"handler_error:{stage.value}", exc)
                outcome_status = "FAILED"
                outcome_error = exc

        schedule.plan.outcomes.append(
            StageOutcome(stage, outcome_status, drift_us, error=outcome_error, executed_at=now_wall)
        )

        # 階段與狀態機明確驅動契約：後置推進
        if (
            schedule.fsm is not None
            and outcome_status == "OK"
            and stage == WarmupStage.CHECK_SESSION
            and self._get_fsm_state_id(schedule.fsm) == "PREPARING"
        ):
            if not self._send_fsm_event(schedule, "session_ready"):
                return

        # 任一階段失敗即原子化中止，「不得」再把
        # TRIGGER_PURCHASE 排除於本條件之外：那會讓開賣階段失敗後殘留 jobs、
        # FSM 卡在 SALE_OPEN 且無人推進終態（fail-open 缺陷）。
        # 註：本檔受 G12 靜態掃描，註解亦不得出現該排除條件的字面寫法。
        if outcome_status == "FAILED":
            self._abort_schedule(schedule, reason=f"stage_failed:{stage.value}")

    async def _spin_and_trigger(self, schedule: TaskSchedule) -> None:
        plan = schedule.plan
        try:
            if schedule.cancelled or schedule.failed or plan.aborted:
                self._telemetry.record(
                    TimelineEventType.MARK, "spin_cancelled",
                    task_id=schedule.task_id, stage="entry",
                )
                return

            # 晚排程的就緒補齊必須在 spin 等待「之前」做完，
            # 讓補齊耗時盡量落在 T=0 之前的剩餘視窗內，而不是整段加到開賣之後
            # （若補齊比剩餘時間長，超出部分仍會如實成為正的 sale_time_error_ms，不修飾）。
            # 非晚排程不進這條分支：補齊已由 schedule() 的 overdue 迴圈完成。
            if schedule.readiness_deferred and not await self._ensure_sale_ready(schedule):
                return

            await self._run_stage(schedule, WarmupStage.SPIN_WAIT)

            if schedule.cancelled or schedule.failed or plan.aborted:
                self._telemetry.record(
                    TimelineEventType.MARK, "spin_cancelled",
                    task_id=schedule.task_id, stage="after_spin_wait",
                )
                return

            trigger_plan = plan.stage_of(WarmupStage.TRIGGER_PURCHASE)
            while True:
                if schedule.cancelled or schedule.failed or plan.aborted:
                    self._telemetry.record(
                        TimelineEventType.MARK, "spin_cancelled",
                        task_id=schedule.task_id, stage="in_spin_loop",
                    )
                    return
                remaining = trigger_plan.deadline_perf - self._perf()
                if remaining <= 0:
                    break
                if remaining > self._spin_sleep_threshold_s:
                    await self._sleep(remaining - self._spin_sleep_threshold_s)

            if schedule.cancelled or schedule.failed or plan.aborted:
                self._telemetry.record(
                    TimelineEventType.MARK, "spin_cancelled",
                    task_id=schedule.task_id, stage="before_trigger",
                )
                return

            # 凍結決策＝選項 2：流程就緒優先。
            # 不放棄本次開賣，先把 FSM 沿合法路徑推進到 WAITING_FOR_SALE 再觸發；
            # 無法就緒才 fail-closed 中止。絕不硬送 sale_triggered。
            # 就緒判定與觸發必須在**同一次持鎖**內完成，否則
            # 中間空隙會讓 rebase catch-up 插入階段、使剛驗證過的就緒狀態失效。
            # 此處多半只是純驗證（晚排程已在 spin 前補齊、正常排程由 schedule() 補齊），
            # 只有 rebase 之後仍有未跑階段時才會真的補跑，屆時才記錄 readiness 事件。
            async with schedule.stage_lock:
                if not await self._ensure_sale_ready_locked(schedule):
                    return
                await self._run_stage_locked(schedule, WarmupStage.TRIGGER_PURCHASE)
        finally:
            plan.finished.set()

    async def _ensure_sale_ready(self, schedule: TaskSchedule) -> bool:
        """取 per-task 鎖後委派 _ensure_sale_ready_locked()。"""
        async with schedule.stage_lock:
            return await self._ensure_sale_ready_locked(schedule)

    async def _ensure_sale_ready_locked(self, schedule: TaskSchedule) -> bool:
        """開賣前的就緒保證（凍結決策＝選項 2）。

        呼叫端必須已持有 `schedule.stage_lock`。

        回傳 True 表示 FSM 已在 WAITING_FOR_SALE、且所有預熱階段皆已執行，
        可安全發送 sale_triggered；回傳 False 表示已 fail-closed 中止（呼叫端立即 return）。

        **唯一 owner 契約**：晚排程（`readiness_deferred`）的預熱
        補齊只在這裡發生，`schedule()` 不得代勞。因此 `sale_readiness_catch_up` 與
        `sale_readiness_recovered` 必然成對、恰記錄一次，且順序早於 TRIGGER_PURCHASE。

        補齊策略沿 `PRE_SALE_STAGES` 宣告順序逐段推進（不只 SALE_READINESS_PATH 的兩個
        FSM 驅動階段——NAVIGATE_PAGE / ENTER_READY 是把頁面帶到可下單狀態的實際工作，
        晚排程同樣不得跳過）：
          * 該階段從未觸發 -> 補跑其 handler（handler 內部的 FSM 前/後置驅動會自然推進狀態）；
          * handler 已跑過但狀態仍停在 expected_from -> 只補送對應 FSM 事件，不重跑 handler
            （涵蓋 attach_fsm() 晚於階段執行的情況）。
        由此產生的延遲會如實反映在 TRIGGER_PURCHASE 的 sale_time_error_ms（正值），不修飾。
        """
        fsm = schedule.fsm
        if fsm is None:
            self._telemetry.record_error(
                "fsm_missing_before_trigger",
                RuntimeError("FSM is None before trigger, refusing to send sale_triggered"),
                task_id=schedule.task_id,
            )
            self._abort_schedule(schedule, reason="fsm_missing_before_trigger")
            return False

        pending_stages = [st for st in PRE_SALE_STAGES if st not in schedule.fired_stages]

        # 正常路徑（非晚排程）：階段都跑完且狀態已就緒 -> 直接放行，
        # 不記錄任何 readiness 事件（沒有發生補齊，就不得偽造恢復紀錄）。
        if not pending_stages and self._get_fsm_state_id(fsm) == "WAITING_FOR_SALE":
            return True

        if schedule.readiness_reported:
            # 防重入守衛：正常流程本函式每個 schedule 只會被呼叫一次。
            self._telemetry.record(
                TimelineEventType.MARK, "sale_readiness_reentered",
                task_id=schedule.task_id,
                current_fsm_state=str(self._get_fsm_state_id(fsm)),
            )
        else:
            schedule.readiness_reported = True
            self._telemetry.record(
                TimelineEventType.MARK, "sale_readiness_catch_up",
                task_id=schedule.task_id,
                current_fsm_state=str(self._get_fsm_state_id(fsm)),
                pending_stages=[st.value for st in pending_stages],
                deferred=schedule.readiness_deferred,
            )
        readiness_start_perf = self._perf()

        for stage in PRE_SALE_STAGES:
            if schedule.cancelled or schedule.failed or schedule.plan.aborted:
                return False
            if stage not in schedule.fired_stages:
                await self._run_stage_locked(schedule, stage)
                if schedule.cancelled or schedule.failed or schedule.plan.aborted:
                    return False
            expected = SALE_READINESS_EVENTS.get(stage)
            if expected is None:
                continue
            expected_from, event_name = expected
            if self._get_fsm_state_id(fsm) == expected_from:
                if not self._send_fsm_event(schedule, event_name):
                    return False

        current_id = self._get_fsm_state_id(fsm)
        if current_id == "WAITING_FOR_SALE":
            self._telemetry.record(
                TimelineEventType.MARK, "sale_readiness_recovered",
                task_id=schedule.task_id,
                readiness_cost_ms=round((self._perf() - readiness_start_perf) * 1000.0, 3),
            )
            return True

        self._telemetry.record_error(
            "fsm_not_ready_for_sale",
            RuntimeError(
                f"FSM stuck at {current_id} after readiness catch-up, expected WAITING_FOR_SALE"
            ),
            task_id=schedule.task_id,
            current_fsm_state=str(current_id),
        )
        self._abort_schedule(schedule, reason=f"fsm_not_ready:{current_id}")
        return False

    def update_clock_offset(
        self,
        offset_ms: float,
        source: ClockSource | str = ClockSource.MANUAL,
        samples: tuple[ClockSample, ...] = (),
    ) -> ClockOffsetUpdate:
        previous = self._time_reference.offset_ms
        delta = offset_ms - previous
        source_id = source.value if isinstance(source, ClockSource) else str(source)
        now_wall = self._wall()
        new_samples = samples if samples else self._time_reference.samples

        if abs(delta) <= self._offset_epsilon_ms:
            if samples:
                self._time_reference = replace(
                    self._time_reference,
                    samples=new_samples,
                )
                for sched in self._active_tasks.values():
                    if getattr(sched.plan, "time_reference", None) is not None:
                        sched.plan.time_reference = replace(
                            sched.plan.time_reference,
                            samples=new_samples,
                        )
            self._telemetry.record_clock_sync(
                source=source_id, previous_offset_ms=previous,
                offset_ms=previous, delta_ms=delta, applied=False,
                reason="below_epsilon",
            )
            return ClockOffsetUpdate(
                offset_ms=previous,
                source=source_id,
                samples=self._time_reference.samples,
                applied_at=now_wall,
            )

        self._time_reference = replace(
            self._time_reference,
            offset_ms=offset_ms,
            samples=new_samples,
            primary_source=source if isinstance(source, ClockSource) else ClockSource.MANUAL,
        )
        for sched in self._active_tasks.values():
            if getattr(sched.plan, "time_reference", None) is not None:
                sched.plan.time_reference = self._time_reference
        self._telemetry.apply_offset(offset_ms)
        shift = timedelta(milliseconds=delta)
        rebased: list[str] = []
        rescheduled: list[str] = []
        skipped: list[str] = []
        handed_off: list[str] = []

        for sched in self._active_tasks.values():
            if sched.cancelled or sched.failed or sched.plan.finished.is_set():
                continue
            plan = sched.plan
            plan.target_local_at = plan.target_true_at - timedelta(milliseconds=offset_ms)
            touched = False
            overdue_handoff: list[StagePlan] = []
            for sp in plan.stages:
                key = f"{plan.task_id}:{sp.stage.value}"
                if sp.stage in sched.fired_stages:
                    skipped.append(key)
                    continue
                sp.planned_local_at -= shift
                sp.deadline_perf -= delta / 1000.0
                sp.overdue = sp.planned_local_at <= now_wall
                touched = True

                if sp.job_id is None or self._jobs is None:
                    # 無 APScheduler job 的階段（SPIN_WAIT late 路徑、TRIGGER_PURCHASE）
                    # 只需更新 deadline_perf；實際觸發由 spin 協程依 perf 判定。
                    rescheduled.append(key)
                    continue

                try:
                    self._jobs.remove_job(sp.job_id)
                except Exception:
                    pass

                if sp.overdue:
                    # 重算後已過期：絕不重掛 APScheduler
                    # （必然 misfire）。移除 job 並轉交本地協程立即接手。
                    # 嚴禁把 run_date 夾擠到「現在 + 1ms」再重掛（見 G13）。
                    sp.job_id = None
                    try:
                        sched.job_ids.remove(key)
                    except ValueError:
                        pass
                    overdue_handoff.append(sp)
                    handed_off.append(key)
                    continue

                self._jobs.add_job(
                    self._dispatch_stage,
                    trigger="date",
                    run_date=sp.planned_local_at,
                    args=(plan.task_id, sp.stage),
                    id=sp.job_id,
                    misfire_grace_time=self._misfire_grace_time,
                    coalesce=True,
                )
                rescheduled.append(key)
            if touched:
                rebased.append(plan.task_id)
            self._handoff_overdue_after_rebase(sched, overdue_handoff, now_wall)

        self._telemetry.record_clock_sync(
            source=source_id, previous_offset_ms=previous,
            offset_ms=offset_ms, delta_ms=delta, applied=True,
            rebased_count=len(rebased), rescheduled_count=len(rescheduled),
            handed_off_count=len(handed_off),
        )
        return ClockOffsetUpdate(
            offset_ms=offset_ms,
            source=source_id,
            samples=new_samples,
            applied_at=now_wall,
        )

    def _handoff_overdue_after_rebase(
        self,
        sched: TaskSchedule,
        stages: list[StagePlan],
        now_wall: datetime,
    ) -> None:
        """把 rebase 後已過期的階段交給本地協程接手。

        update_clock_offset() 是同步方法，無法 await，故以單一背景協程循序執行，
        避免多個 catch-up 併發互踩。協程登記於 _background_tasks，由 shutdown() 排空。
        """
        if sched.cancelled or sched.failed or sched.plan.aborted:
            return
        spin_due = (
            not sched.spin_started
            and sched.plan.stage_of(WarmupStage.SPIN_WAIT).planned_local_at <= now_wall
        )
        if not stages and not spin_due:
            return
        task = asyncio.create_task(self._run_rebase_catch_up(sched, tuple(stages)))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _run_rebase_catch_up(
        self,
        sched: TaskSchedule,
        stages: tuple[StagePlan, ...],
    ) -> None:
        """依 WarmupStage 宣告順序循序補跑過期階段，再視情況接上 late-spin / post-sale。"""
        for sp in stages:
            if sched.cancelled or sched.failed or sched.plan.aborted:
                return
            await self._dispatch_stage(sched.task_id, sp.stage)
        if sched.cancelled or sched.failed or sched.plan.aborted:
            return
        spin_plan = sched.plan.stage_of(WarmupStage.SPIN_WAIT)
        if spin_plan.planned_local_at <= self._wall() and not sched.spin_started:
            # late-spin / post-sale 由 _spin_and_trigger() 依 deadline_perf 自行分辨：
            # 尚未到 T=0 -> tight-loop 等待；已越過 T=0 -> 立即觸發。
            sched.spin_started = True
            sched.spin_task = self._safe_create_task(self._spin_and_trigger(sched), sched)

    def attach_clock_synchronizer(
        self,
        synchronizer: ClockSynchronizerLike,
        stages: Iterable[WarmupStage] | None = None,
    ) -> None:
        """明示指定共用同步器（覆寫 factory）。

        呼叫端明確表達「所有 task 共用這一個」，因此同時覆寫既有活躍 task 的
        `clock_sync`，避免同一個 scheduler 內新舊 task 用到不同來源而難以追查。
        """
        self._clock_sync = synchronizer
        for sched in self._active_tasks.values():
            sched.clock_sync = synchronizer
        if stages is not None:
            self._resync_stages = frozenset(stages)

    def cancel(self, task_id: str) -> bool:
        schedule = self._active_tasks.get(task_id)
        if schedule is None or schedule.cancelled:
            return False
        schedule.cancelled = True
        if self._jobs is not None:
            for jid in tuple(schedule.job_ids):
                try:
                    self._jobs.remove_job(jid)
                except Exception:
                    pass
            schedule.job_ids.clear()
        if schedule.spin_task is not None and not schedule.spin_task.done():
            schedule.spin_task.cancel()
        schedule.plan.finished.set()
        self._telemetry.record(TimelineEventType.MARK, "task_cancelled", task_id=task_id)
        return True

    async def shutdown(self, wait: bool = True) -> None:
        """非同步安全關閉並排空所有活躍任務"""
        if self._shutdown:
            return
        self._shutdown = True
        tasks: list[asyncio.Task[Any]] = []
        for task_id in list(self._active_tasks.keys()):
            schedule = self._active_tasks.get(task_id)
            if schedule is not None and schedule.spin_task is not None and not schedule.spin_task.done():
                tasks.append(schedule.spin_task)
            self.cancel(task_id)

        # 一併排空 rebase catch-up 協程，
        # 否則事件迴圈關閉時會噴 "coroutine was never awaited" RuntimeWarning。
        for bg in tuple(self._background_tasks):
            if not bg.done():
                bg.cancel()
            tasks.append(bg)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()

        if self._jobs is not None and hasattr(self._jobs, "running") and self._jobs.running:
            self._jobs.shutdown(wait=wait)
        self._started = False
        self._telemetry.record(TimelineEventType.MARK, "scheduler_shutdown")

    async def wait_until_finished(self, task_id: str, timeout: float | None = None) -> bool:
        plan = self.plan_of(task_id)
        try:
            if timeout is None:
                await plan.finished.wait()
                return True
            await asyncio.wait_for(plan.finished.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def plan_of(self, task_id: str) -> WarmupPlan:
        plan = self._plans.get(task_id)
        if plan is None:
            raise KeyError(f"Unknown task {task_id}")
        return plan

    @property
    def active_task_ids(self) -> set[str]:
        return {
            tid
            for tid, s in self._active_tasks.items()
            if not s.cancelled and not s.failed and not s.plan.finished.is_set()
        }

    @property
    def time_reference(self) -> TimeReference:
        return self._time_reference

    @staticmethod
    def _get_fsm_state_id(fsm: Any) -> str | None:
        """安全內省 FSM 狀態 ID，遵循 python-statemachine 3.2+ 標準，嚴禁使用 deprecated 之 current_state"""
        if fsm is None:
            return None
        if hasattr(fsm, "current_state_id"):
            cid = fsm.current_state_id
            return cid() if callable(cid) else str(cid)
        if hasattr(fsm, "configuration"):
            try:
                state_obj = next(iter(fsm.configuration))
                return getattr(state_obj, "id", None) or getattr(state_obj, "value", str(state_obj))
            except Exception:
                pass
        if hasattr(fsm, "state"):
            st = fsm.state
            return getattr(st, "id", None) or getattr(st, "value", str(st))
        return None
