"""購票協調器 ×（真實排程器 ＋ 真實狀態機 ＋ 真實 adapter）的端到端閉環。

瀏覽器與頁面是替身，時鐘受控；除此之外整條路徑都是產品程式碼。
全程掛 netguard：不連外、不啟動真實 Chromium、不送出任何訂單或付款。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from adapters.payment import MockPaymentProvider, PaymentOutcome
from adapters.ticketing.kktix.adapter import KKTIXAdapter
from adapters.verification import ManualVerificationProvider, VerificationChallenge
from domain.preference import SeatPreference, TicketPreference, TicketPriority
from domain.task import AttendeeProfile, PurchaseTaskSpec, UserContactProfile
from purchase.orchestrator import PurchaseOrchestrator
from scheduler.clock_sync import TimeReference
from scheduler.scheduler import WarmupScheduler, WarmupStage
from telemetry.timeline import TimelineEventType, TimelineRecorder
from tests.fake_page import FakeBrowser, FakePage, load_page_html
from tests.netguard import netguard_autouse  # noqa: F401

BASE_WALL = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
CLOUDFLARE_HTML = "<html><body>Just a moment...</body></html>"
NO_CAPTCHA_ORDER_HTML = (
    "<div id='orderApp'><input name='contact[name]'><input name='contact[email]'>"
    "<input name='contact[phone]'><button ng-click='confirmOrder()'>確認表單資料</button></div>"
)


class Clock:
    def __init__(self, wall: datetime = BASE_WALL, perf: float = 500.0, tick: float = 0.0005) -> None:
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
        self.running = False


class NullSynchronizer:
    def __init__(self, server_url: str | None = None) -> None:
        self.server_url = server_url

    async def refresh(self) -> TimeReference:
        return TimeReference(offset_ms=0.0, samples=(), primary_source=None)


def answers(*values: str) -> Callable[[VerificationChallenge], Any]:
    queue = list(values)

    async def source(challenge: VerificationChallenge) -> str:
        return queue.pop(0) if queue else ""

    return source


def make_spec(
    sale_at: datetime,
    task_id: str = "flow-1",
    *,
    priorities: list[TicketPriority] | None = None,
    qualification_code: str | None = None,
    auto_login: bool = False,
) -> PurchaseTaskSpec:
    return PurchaseTaskSpec(
        task_id=task_id,
        event_title="去識別化測試活動",
        event_url="https://registration.example.com/events/1",
        sale_start_at=sale_at,
        ticket_preference=TicketPreference(
            quantity=2,
            priorities=priorities or [TicketPriority(price=3200)],
            seat_preference=SeatPreference(strategy="best_available"),
        ),
        contact_profile=UserContactProfile(name="王小明", phone="0912345678", email="a@b.co"),
        attendees=(
            AttendeeProfile(name="參加人一", phone="0987654321", id_number="A123456789"),
            AttendeeProfile(name="參加人二", phone="0987654322", id_number="A123456780"),
        ),
        payment_method="mock",
        qualification_code=qualification_code,
        auto_login=auto_login,
    )


class Flow:
    def __init__(self, tmp_path: Path) -> None:
        self.clock = Clock()
        self.telemetry = TimelineRecorder(
            perf_counter=self.clock.perf_counter, wall_clock=self.clock.wall_clock
        )
        self.jobs = FakeJobScheduler()
        self.scheduler = WarmupScheduler(
            self.telemetry,
            job_scheduler=self.jobs,
            clock_synchronizer_factory=NullSynchronizer,
            wall_clock=self.clock.wall_clock,
            perf_counter=self.clock.perf_counter,
            sleep=self.clock.sleeper(),
        )
        self.tmp_path = tmp_path
        self.page = FakePage.from_fixture("kktix_registration_new.html")
        self.browser = FakeBrowser(self.page, tmp_path / "shots")
        self.timeline_path = tmp_path / "timeline.json"

    def wire(
        self,
        *,
        start_html: str | None = None,
        order_html: str | None = None,
        payment_simulate: PaymentOutcome = PaymentOutcome.CHECKPOINT_REACHED,
        verification_answers: tuple[str, ...] = ("ATA",),
        spec: PurchaseTaskSpec | None = None,
    ) -> PurchaseOrchestrator:
        if spec is not None:
            self._spec = spec
        if start_html is not None:
            self.page = FakePage(start_html)
            self.browser.page = self.page
        self.page.on_click(
            "challenge(1)", order_html or load_page_html("kktix_registration_order.html")
        )
        self.page.on_click("confirmOrder", load_page_html("kktix_payment.html"))
        adapter = KKTIXAdapter(
            telemetry=self.telemetry,
            payment=MockPaymentProvider(simulate=payment_simulate, telemetry=self.telemetry),
            verification=ManualVerificationProvider(
                answers(*verification_answers), timeout_s=1.0, telemetry=self.telemetry
            ),
            attendees=self.spec.attendees,
            timeout_ms=20,
        )
        return PurchaseOrchestrator(
            self.spec,
            browser=self.browser,
            scheduler=self.scheduler,
            adapter=adapter,
            telemetry=self.telemetry,
            timeline_path=self.timeline_path,
            detect_timeout_ms=20,
            wait_timeout=5.0,
            session_gate_timeout_s=0.2,
            session_gate_poll_s=0.0,
        )

    @property
    def spec(self) -> PurchaseTaskSpec:
        if not hasattr(self, "_spec"):
            self._spec = make_spec(self.clock.wall + timedelta(milliseconds=300))
        return self._spec

    def transitions(self) -> list[tuple[str, str, str]]:
        return [
            (e.detail["from_state"], e.detail["event"], e.detail["to_state"])
            for e in self.telemetry.events_of(TimelineEventType.TRANSITION)
        ]

    def states(self) -> list[str]:
        return [t[2] for t in self.transitions()]


@pytest.fixture
async def flow(tmp_path: Path) -> Any:
    instance = Flow(tmp_path)
    yield instance
    await instance.scheduler.shutdown()


async def test_full_flow_reaches_completed(flow: Flow) -> None:
    report = await flow.wire().run()
    assert report.final_state == "COMPLETED"
    assert flow.states() == [
        "PREPARING", "WAITING_FOR_SALE", "SALE_OPEN", "TICKET_SELECTION",
        "SEAT_SELECTION", "FORM_FILLING", "VERIFICATION_REQUIRED",
        "PAYMENT_REQUIRED", "PAYMENT_PROCESSING", "COMPLETED",
    ]


async def test_full_flow_exports_timeline_and_screenshots(flow: Flow) -> None:
    report = await flow.wire().run()
    payload = json.loads(flow.timeline_path.read_text(encoding="utf-8"))
    assert any(e["event_type"] == "STAGE" for e in payload)
    # 第一次轉移（IDLE -> PREPARING）發生在 PREPARE_BROWSER handler 建立分頁之前，
    # 此時截圖 hook 尚不存在——這是預熱順序的必然結果，如實斷言而非掩蓋。
    assert flow.transitions()[0] == ("IDLE", "prepare_session", "PREPARING")
    assert len(report.screenshots) == len(flow.transitions()) - 1
    assert report.screenshots[0].endswith("_0001_WAITING_FOR_SALE.png")
    assert report.sale_time_error_ms is not None


async def test_full_flow_records_decision_and_checkpoint(flow: Flow) -> None:
    report = await flow.wire().run()
    names = {e.name for e in flow.telemetry.events()}
    assert "ticket_decision" in names
    assert "payment_checkpoint_reached" in names
    assert "order_submitted" in names
    assert report.ticket_trace and report.ticket_trace[0].endswith("SELECTED index=0 name=全票 A 區")


async def test_sold_out_page_ends_in_sold_out(flow: Flow) -> None:
    report = await flow.wire(start_html=load_page_html("kktix_sold_out.html")).run()
    assert report.final_state == "SOLD_OUT"
    assert flow.states()[-1] == "SOLD_OUT"
    assert report.aborted is False


async def test_flow_without_captcha_skips_verification(flow: Flow) -> None:
    report = await flow.wire(order_html=NO_CAPTCHA_ORDER_HTML).run()
    assert report.final_state == "COMPLETED"
    assert "VERIFICATION_REQUIRED" not in flow.states()


async def test_verification_retry_then_success(flow: Flow) -> None:
    report = await flow.wire(verification_answers=("", "ATA")).run()
    assert report.final_state == "COMPLETED"
    assert [t[1] for t in flow.transitions()].count("retry_verification") == 1


async def test_declined_payment_ends_in_failed(flow: Flow) -> None:
    report = await flow.wire(payment_simulate=PaymentOutcome.DECLINED).run()
    assert report.final_state == "FAILED"
    assert ("PAYMENT_PROCESSING", "payment_declined", "FAILED") in flow.transitions()


async def test_cloudflare_challenge_aborts_before_any_order(flow: Flow) -> None:
    """人機驗證在開賣前的就緒閘門就被擋下，不會帶著挑戰頁衝進開賣。"""
    report = await flow.wire(start_html=CLOUDFLARE_HTML).run()
    assert report.final_state == "FAILED"
    assert report.aborted is True
    probes = [e for e in flow.telemetry.events() if e.name == "session_probe"]
    assert probes and all(p.detail["kind"] == "CHALLENGE" for p in probes)
    assert "page not ready before sale: CHALLENGE" in str(report.error)
    assert flow.page.clicks == []
    assert [e.name for e in flow.telemetry.events() if e.name == "cloudflare_challenge"]


async def test_login_redirect_also_blocks_before_the_sale(flow: Flow) -> None:
    """沒登入被導去登入頁，同樣停在閘門，不會誤判成售罄。"""
    report = await flow.wire(
        start_html="<div id='signin'><form action='/users/sign_in'>"
                   "<input type='password'></form></div>"
    ).run()
    assert report.final_state == "FAILED"
    assert "page not ready before sale: LOGIN" in str(report.error)
    assert flow.page.clicks == []


async def test_pre_sale_stages_all_run_before_trigger(flow: Flow) -> None:
    await flow.wire().run()
    stage_events = [
        e.name for e in flow.telemetry.events_of(TimelineEventType.STAGE)
    ]
    assert stage_events == [s.value for s in WarmupStage]


QUALIFICATION_CODE_PAGE_HTML = (
    "<div id='registrationsNewApp'>"
    "<div class='code-input'>"
    "<input type='text' ng-model='code'>"
    "<button type='button' class='btn' ng-click='checkCode()'>送出</button>"
    "</div></div>"
)


async def test_flow_with_qualification_code(flow: Flow) -> None:
    spec = make_spec(
        flow.clock.wall + timedelta(milliseconds=300),
        qualification_code="VIP999",
    )
    orch = flow.wire(start_html=QUALIFICATION_CODE_PAGE_HTML, spec=spec)
    flow.page.on_click("checkCode()", load_page_html("kktix_registration_new.html"))
    report = await orch.run()
    assert report.final_state == "COMPLETED"
    assert orch._rt.qualification_handled is True
    assert any(e.name == "qualification_code_submitted" for e in flow.telemetry.events())


LOGIN_PAGE_HTML = (
    "<div id='signin'><form action='/users/sign_in'>"
    "<input type='text' name='user[login]'>"
    "<input type='password' name='user[password]'>"
    "<button type='submit' class='btn-login'>登入</button>"
    "</form></div>"
)


async def test_flow_auto_login_screenshot_barrier(
    flow: Flow, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTO_TICKET_KKTIX_USERNAME", "flowuser@example.com")
    monkeypatch.setenv("AUTO_TICKET_KKTIX_PASSWORD", "flowpass123")
    spec = make_spec(
        flow.clock.wall + timedelta(milliseconds=300),
        auto_login=True,
    )
    orch = flow.wire(start_html=LOGIN_PAGE_HTML, spec=spec)
    flow.page.on_click("btn-login", load_page_html("kktix_registration_new.html"))
    orig_nav = orch.adapter.navigate_to_event

    async def nav_stub(page: Any, url: str) -> bool:
        from bs4 import BeautifulSoup

        flow.page.soup = BeautifulSoup(load_page_html("kktix_registration_new.html"), "html.parser")
        return bool(await orig_nav(page, url))

    orch.adapter.navigate_to_event = nav_stub
    report = await orch.run()
    assert report.final_state == "COMPLETED"
    timeline_text = flow.timeline_path.read_text(encoding="utf-8")
    assert "flowuser@example.com" not in timeline_text
    assert "flowpass123" not in timeline_text


async def test_flow_single_payment_lock(flow: Flow) -> None:
    from purchase.handlers import PurchaseStepError

    orch = flow.wire()
    orch._rt.page = flow.page
    from fsm.machine import PurchaseWorkflow

    orch.fsm = PurchaseWorkflow(orch.spec, orch.telemetry)
    orch.fsm.sync_to_state("PAYMENT_REQUIRED", "form_submitted")
    await orch._handle_payment_required(flow.page)
    assert orch._rt.payment_attempted is True
    with pytest.raises(PurchaseStepError, match="payment already attempted"):
        await orch._handle_payment_required(flow.page)
