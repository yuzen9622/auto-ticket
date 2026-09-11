from __future__ import annotations

from collections.abc import Callable
from typing import Any

from statemachine import State, StateMachine
from statemachine.orderedset import OrderedSet

from domain.task import PurchaseTaskSpec
from fsm.states import FINAL_STATES, PurchaseState
from telemetry.timeline import TimelineRecorder

# 註：本檔嚴禁 import `TransitionNotAllowed` / `TicketPriority` / `PurchaseEvent`——
# 它們在本模組內未被使用，會直接違反驗收 #20 的 Ruff `F401`。


class PurchaseWorkflow(StateMachine):
    # 14 個狀態定義
    IDLE = State(PurchaseState.IDLE.value, initial=True)
    PREPARING = State(PurchaseState.PREPARING.value)
    WAITING_FOR_SALE = State(PurchaseState.WAITING_FOR_SALE.value)
    SALE_OPEN = State(PurchaseState.SALE_OPEN.value)
    TICKET_SELECTION = State(PurchaseState.TICKET_SELECTION.value)
    SEAT_SELECTION = State(PurchaseState.SEAT_SELECTION.value)
    FORM_FILLING = State(PurchaseState.FORM_FILLING.value)
    VERIFICATION_REQUIRED = State(PurchaseState.VERIFICATION_REQUIRED.value)
    PAYMENT_REQUIRED = State(PurchaseState.PAYMENT_REQUIRED.value)
    PAYMENT_PROCESSING = State(PurchaseState.PAYMENT_PROCESSING.value)
    COMPLETED = State(PurchaseState.COMPLETED.value, final=True)
    SOLD_OUT = State(PurchaseState.SOLD_OUT.value, final=True)
    TIMEOUT = State(PurchaseState.TIMEOUT.value, final=True)
    FAILED = State(PurchaseState.FAILED.value, final=True)

    # 33 條轉移路徑（含多目標條件原子分流）
    prepare_session = IDLE.to(PREPARING)
    session_ready = PREPARING.to(WAITING_FOR_SALE)
    sale_triggered = WAITING_FOR_SALE.to(SALE_OPEN)

    page_loaded = SALE_OPEN.to(TICKET_SELECTION)
    ticket_reserved = TICKET_SELECTION.to(SEAT_SELECTION)

    # 票券降級分流
    retry_fallback_ticket = (
        TICKET_SELECTION.to(TICKET_SELECTION, cond="has_next_priority")
        | TICKET_SELECTION.to(SOLD_OUT, cond="is_tickets_exhausted")
    )
    all_tickets_unavailable = TICKET_SELECTION.to(SOLD_OUT)

    # 座位確認與衝突重試分流
    seat_confirmed = SEAT_SELECTION.to(FORM_FILLING)
    seat_conflict = (
        SEAT_SELECTION.to(TICKET_SELECTION, cond="can_retry_seat")
        | SEAT_SELECTION.to(FAILED, cond="is_seat_exhausted")
    )

    # 表單提交分流
    form_submitted = (
        FORM_FILLING.to(VERIFICATION_REQUIRED, cond="requires_verification")
        | FORM_FILLING.to(PAYMENT_REQUIRED, unless="requires_verification")
    )

    # 驗證碼分流
    verification_passed = VERIFICATION_REQUIRED.to(PAYMENT_REQUIRED)
    retry_verification = (
        VERIFICATION_REQUIRED.to(VERIFICATION_REQUIRED, cond="can_retry_verification")
        | VERIFICATION_REQUIRED.to(FAILED, cond="is_verification_exhausted")
    )
    verification_failed = VERIFICATION_REQUIRED.to(FAILED)

    # 付款分流
    submit_payment = PAYMENT_REQUIRED.to(PAYMENT_PROCESSING)
    payment_success = PAYMENT_PROCESSING.to(COMPLETED)
    payment_declined = PAYMENT_PROCESSING.to(FAILED)

    # 逾時終態
    abort_timeout = (
        SALE_OPEN.to(TIMEOUT)
        | TICKET_SELECTION.to(TIMEOUT)
        | FORM_FILLING.to(TIMEOUT)
    )

    # 異常中斷終態
    # **必須涵蓋全部 10 個非終態**：排程器的 _abort_schedule() 對任何非終態都會嘗試
    # 推進 FAILED，若此處漏掉任一狀態，真實 FSM 會拋 TransitionNotAllowed。
    # 由驗收 #35 機械化比對「abort_failed 的來源狀態集合 == 全部非終態」。
    abort_failed = (
        IDLE.to(FAILED)
        | PREPARING.to(FAILED)
        | WAITING_FOR_SALE.to(FAILED)
        | SALE_OPEN.to(FAILED)
        | TICKET_SELECTION.to(FAILED)
        | SEAT_SELECTION.to(FAILED)
        | FORM_FILLING.to(FAILED)
        | VERIFICATION_REQUIRED.to(FAILED)
        | PAYMENT_REQUIRED.to(FAILED)
        | PAYMENT_PROCESSING.to(FAILED)
    )

    def __init__(
        self,
        spec: PurchaseTaskSpec,
        telemetry: TimelineRecorder | None = None,
        *,
        on_transition_hook: Callable[[str, str, str], None] | None = None,
    ) -> None:
        self.spec = spec
        self.telemetry = telemetry
        self._on_transition_hook = on_transition_hook
        self.current_priority_index = 0
        self.verification_retry_count = 0
        self.seat_retry_count = 0
        self._requires_verification = False
        super().__init__()

    @property
    def current_state_id(self) -> str:
        state_obj = next(iter(self.configuration))
        return getattr(state_obj, "id", None) or getattr(state_obj, "value", str(state_obj))

    def on_transition(self, event: Any, source: State, target: State) -> None:
        event_id = getattr(event, "id", str(event))
        from_id = getattr(source, "id", str(source))
        to_id = getattr(target, "id", str(target))
        if event_id == "retry_verification" and to_id == "VERIFICATION_REQUIRED":
            self.verification_retry_count += 1
        elif event_id == "seat_conflict" and to_id == "TICKET_SELECTION":
            self.seat_retry_count += 1
        elif event_id == "retry_fallback_ticket" and to_id == "TICKET_SELECTION":
            self.current_priority_index += 1

        if self.telemetry is not None:
            self.telemetry.record_transition(from_id, to_id, event_id)
        if self._on_transition_hook is not None:
            self._on_transition_hook(from_id, to_id, event_id)

    @classmethod
    def transition_sources(cls, event_name: str) -> frozenset[str]:
        """該事件所有合法來源狀態的 id 集合（class 層內省，供守門與測試使用）。

        python-statemachine 3.2 的 class 層 `Event` 物件**沒有**公開的 `transitions`
        屬性（只有私有 `_transitions`），因此一律改走公開的 `State.transitions`。
        """
        return frozenset(
            t.source.id
            for state in cls.states
            for t in state.transitions
            if any(str(e) == event_name for e in t.events)
        )

    @classmethod
    def transition_count(cls) -> int:
        """全機轉移路徑總數（應為 33）。"""
        return sum(len(list(state.transitions)) for state in cls.states)

    def can_send(self, event_name: str) -> bool:
        """目前狀態是否存在該事件的合法轉移（終態一律 False）。

        使用 3.2 的公開 `allowed_events`，不依賴任何私有屬性。
        """
        if self.current_state_id in {s.value for s in FINAL_STATES}:
            return False
        try:
            return any(
                getattr(ev, "id", str(ev)) == event_name for ev in self.allowed_events
            )
        except Exception:
            return False

    def sync_to_state(
        self, target_state_id: str, event_name: str = "state_sync"
    ) -> bool:
        """把 FSM 對齊到頁面**實際**所處的狀態，作為觀察者而非流程控制器。

        搶票由 `detect_page_state` 驅動，頁面會跳過 FSM 認得的中間步驟（例如直接
        從選票彈到付款頁）。硬要走事件鏈只會讓審計歷程與真實頁面脫節，因此這裡
        允許非終態之間的平滑跳躍——但**終態絕對不可逆**：已經 COMPLETED／SOLD_OUT
        的實驗被覆寫回進行中，整份研究資料就再也分不出哪一次真的成交。
        """
        final_ids = {s.value for s in FINAL_STATES}
        if self.current_state_id in final_ids:
            return False

        # 同狀態自事件（如 ticket_fallback_reselected）：只記錄，不動 configuration，
        # 也不走 can_send/send——那會誤觸 self-transition 而讓重試計數憑空多一。
        if self.current_state_id == target_state_id:
            self._record_sync(self.current_state_id, target_state_id, event_name)
            return True

        if self.can_send(event_name):
            try:
                self.send(event_name)
            except Exception as exc:
                if self.telemetry is not None:
                    self.telemetry.record_error(f"fsm_sync_send_error:{event_name}", exc)
            if self.current_state_id in final_ids:
                return False
            if self.current_state_id == target_state_id:
                return True

        target = self.states_map.get(target_state_id)
        if target is None:
            return False
        from_id = self.current_state_id
        self.configuration = OrderedSet([target])
        self._record_sync(from_id, target_state_id, event_name)
        return True

    def _record_sync(self, from_id: str, to_id: str, event_name: str) -> None:
        """跳躍同步不經過 `on_transition`，歷程得在這裡補記，否則審計會缺一段。"""
        if self.telemetry is not None:
            self.telemetry.record_transition(from_id, to_id, event_name)
        if self._on_transition_hook is not None:
            self._on_transition_hook(from_id, to_id, event_name)

    def can_retry_verification(self) -> bool:
        return self.verification_retry_count < self.spec.max_retries

    def is_verification_exhausted(self) -> bool:
        return not self.can_retry_verification()

    def can_retry_seat(self) -> bool:
        return self.seat_retry_count < self.spec.max_retries

    def is_seat_exhausted(self) -> bool:
        return not self.can_retry_seat()

    def has_next_priority(self) -> bool:
        priorities = self.spec.ticket_preference.sorted_priorities
        return (self.current_priority_index + 1) < len(priorities)

    def is_tickets_exhausted(self) -> bool:
        return not self.has_next_priority()

    def requires_verification(self) -> bool:
        return self._requires_verification

    def set_requires_verification(self, value: bool) -> None:
        self._requires_verification = bool(value)
