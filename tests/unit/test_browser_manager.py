"""瀏覽器管理與截圖 hook。"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest

from browser.cdp_rtt import CdpRttTracker
from browser.context_factory import (
    DEFAULT_MACOS_CHROME_USER_AGENT,
    BrowserProfile,
    build_persistent_context_options,
    resolve_screenshot_path,
    sanitize_path_component,
    screenshot_filename,
)
from browser.manager import PlaywrightManager
from telemetry.timeline import TimelineEventType, TimelineRecorder
from tests.netguard import netguard_autouse  # noqa: F401


# --------------------------------------------------------------- fakes
class FakeCdpSession:
    def __init__(self) -> None:
        self.listeners: list[tuple[str, Any]] = []
        self.sent: list[str] = []
        self.detached = False

    def on(self, name: str, handler: Any) -> None:
        self.listeners.append((name, handler))

    def remove_listener(self, name: str, handler: Any) -> None:
        # pyee 以 dict key 移除 listener，bound method 相等即視為同一個 handler，
        # 故此處必須用 `==` 而非 `is`（每次屬性存取都會產生新的 bound method 物件）。
        self.listeners = [(n, h) for n, h in self.listeners if not (n == name and h == handler)]

    async def send(self, method: str) -> None:
        self.sent.append(method)

    async def detach(self) -> None:
        self.detached = True


class FakePage:
    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.shots: list[str] = []

    async def screenshot(self, path: str) -> None:
        self.shots.append(path)


class FakeContext:
    def __init__(self) -> None:
        self.pages: list[FakePage] = []
        self.init_scripts: list[str] = []
        self.closed = 0
        self.close_error: Exception | None = None
        self.sessions: list[FakeCdpSession] = []

    async def new_page(self) -> FakePage:
        page = FakePage(self)
        self.pages.append(page)
        return page

    async def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)

    async def new_cdp_session(self, page: FakePage) -> FakeCdpSession:
        session = FakeCdpSession()
        self.sessions.append(session)
        return session

    async def close(self) -> None:
        self.closed += 1
        if self.close_error is not None:
            raise self.close_error


class FakeChromium:
    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.launch_calls: list[tuple[str, dict[str, Any]]] = []

    async def launch_persistent_context(self, user_data_dir: str, **opts: Any) -> FakeContext:
        self.launch_calls.append((user_data_dir, opts))
        return self.context


class FakePlaywright:
    def __init__(self, context: FakeContext) -> None:
        self.chromium = FakeChromium(context)
        self.stopped = 0
        self.stop_error: Exception | None = None

    async def start(self) -> FakePlaywright:
        return self

    async def stop(self) -> None:
        self.stopped += 1
        if self.stop_error is not None:
            raise self.stop_error


@pytest.fixture
def context() -> FakeContext:
    return FakeContext()


@pytest.fixture
def playwright(context: FakeContext) -> FakePlaywright:
    return FakePlaywright(context)


@pytest.fixture
def telemetry() -> TimelineRecorder:
    return TimelineRecorder()


@pytest.fixture
def manager(tmp_path: Path, telemetry: TimelineRecorder, playwright: FakePlaywright) -> PlaywrightManager:
    return PlaywrightManager(
        BrowserProfile(name="test-profile", user_data_dir=tmp_path / "profile"),
        telemetry,
        screenshot_dir=tmp_path / "shots",
        playwright_launcher=lambda: playwright,
    )


# --------------------------------------------------------------- profile
def test_browser_profile_defaults_use_macos_chrome_ua() -> None:
    profile = BrowserProfile(name="p1")
    assert profile.user_agent == DEFAULT_MACOS_CHROME_USER_AGENT
    assert "Macintosh" in profile.user_agent and "Chrome/" in profile.user_agent
    assert profile.viewport == {"width": 1920, "height": 1080}
    assert profile.user_data_dir == Path(".browser_profiles") / "p1"


def test_browser_profile_rejects_unsafe_name() -> None:
    with pytest.raises(ValueError):
        BrowserProfile(name="../etc")


def test_context_options_disable_automation_markers() -> None:
    opts = build_persistent_context_options(BrowserProfile(name="p1"), extra_args=("--mute-audio",))
    assert "--disable-blink-features=AutomationControlled" in opts["args"]
    assert "--mute-audio" in opts["args"]
    assert opts["ignore_default_args"] == ["--enable-automation"]
    assert opts["headless"] is True


def test_profile_can_request_a_headed_browser() -> None:
    """有頭模式必須傳得下去：那是使用者手動登入 persistent profile 的唯一途徑。"""
    opts = build_persistent_context_options(BrowserProfile(name="p", headless=False))
    assert opts["headless"] is False
    assert opts["locale"] == "zh-TW"
    assert opts["timezone_id"] == "Asia/Taipei"


def test_context_options_do_not_duplicate_extra_args() -> None:
    opts = build_persistent_context_options(
        BrowserProfile(name="p1"), extra_args=("--no-sandbox",)
    )
    assert opts["args"].count("--no-sandbox") == 1


# --------------------------------------------------------------- path safety
def test_sanitize_path_component_strips_separators() -> None:
    assert "/" not in sanitize_path_component("a/b/c")
    assert sanitize_path_component("") == "unknown"
    assert sanitize_path_component("///") == "unknown"
    assert sanitize_path_component("...") == "unknown"


def test_screenshot_filename_is_flat_and_zero_padded() -> None:
    name = screenshot_filename("exp/../../etc", 7, "SALE_OPEN/../..")
    assert "/" not in name and ".." not in name
    assert "_0007_" in name and name.endswith(".png")


@pytest.mark.parametrize("bad", [-1, True, 1.0])
def test_screenshot_filename_rejects_bad_sequence(bad: Any) -> None:
    with pytest.raises(ValueError):
        screenshot_filename("exp", bad, "S")


@pytest.mark.parametrize(
    "evil", ["../../etc/passwd", "/etc/passwd", "a/../../b.png", "sub/../../../x.png"]
)
def test_resolve_screenshot_path_blocks_traversal(tmp_path: Path, evil: str) -> None:
    with pytest.raises(ValueError):
        resolve_screenshot_path(tmp_path, evil)


def test_resolve_screenshot_path_accepts_safe_name(tmp_path: Path) -> None:
    dest = resolve_screenshot_path(tmp_path, "exp_0001_IDLE.png")
    assert dest.parent == tmp_path.resolve()


# --------------------------------------------------------------- lifecycle
async def test_start_launches_persistent_context_with_stealth(
    manager: PlaywrightManager, playwright: FakePlaywright, context: FakeContext,
    telemetry: TimelineRecorder,
) -> None:
    await manager.start()
    assert manager.is_started is True
    assert playwright.chromium.launch_calls
    assert "webdriver" in context.init_scripts[0]
    marks = [e.name for e in telemetry.events_of(TimelineEventType.MARK)]
    assert "browser_started" in marks
    await manager.stop()


async def test_start_is_idempotent(manager: PlaywrightManager, playwright: FakePlaywright) -> None:
    await manager.start()
    await manager.start()
    assert len(playwright.chromium.launch_calls) == 1
    await manager.stop()


async def test_new_page_requires_started(manager: PlaywrightManager) -> None:
    with pytest.raises(RuntimeError):
        await manager.new_page()


async def test_stop_is_idempotent(manager: PlaywrightManager, playwright: FakePlaywright,
                                  context: FakeContext) -> None:
    await manager.start()
    await manager.stop()
    await manager.stop()
    assert context.closed == 1
    assert playwright.stopped == 1
    assert manager.is_started is False


# --------------------------------------------------------------- CDP
async def test_attach_and_full_detach_clears_tracker(
    manager: PlaywrightManager, context: FakeContext, telemetry: TimelineRecorder,
) -> None:
    await manager.start()
    page = await manager.new_page()
    await manager.attach_cdp(page)
    session = context.sessions[0]
    assert [n for n, _ in session.listeners] == [
        "Network.requestWillBeSent", "Network.responseReceived",
    ]
    assert session.sent == ["Network.enable"]

    manager._cdp_tracker.on_request_will_be_sent({"requestId": "r1", "request": {"url": "u"}})
    assert manager._cdp_tracker.pending_count == 1

    await manager.detach_cdp()
    assert session.listeners == []
    assert session.detached is True
    assert manager._cdp_tracker.pending_count == 0
    await manager.stop()


async def test_single_page_detach_keeps_shared_tracker(
    manager: PlaywrightManager, context: FakeContext,
) -> None:
    """單頁 detach 不得清空 manager 範圍共用的 tracker（其他頁仍有 in-flight）。"""
    await manager.start()
    page_a = await manager.new_page()
    page_b = await context.new_page()
    await manager.attach_cdp(page_a)
    await manager.attach_cdp(page_b)

    tracker: CdpRttTracker = manager._cdp_tracker
    tracker.on_request_will_be_sent({"requestId": "from-b", "request": {"url": "u"}})

    await manager.detach_cdp(page_a)
    assert context.sessions[0].detached is True
    assert context.sessions[1].detached is False
    assert tracker.pending_count == 1, "單頁 detach 不得清掉其他頁的 pending"

    tracker.on_response_received({"requestId": "from-b", "response": {"status": 200}})
    assert tracker.stats()["matched"] == 1
    assert tracker.stats()["orphan"] == 0
    await manager.stop()


# --------------------------------------------------------------- screenshots
async def test_capture_screenshot_writes_expected_filename(
    manager: PlaywrightManager, tmp_path: Path,
) -> None:
    await manager.start()
    page = await manager.new_page()
    dest = await manager.capture_screenshot(3, "SALE_OPEN", page, experiment_id="exp")
    assert dest is not None
    assert dest.name == "exp_0003_SALE_OPEN.png"
    assert page.shots == [str(dest)]
    await manager.stop()


async def test_capture_screenshot_skipped_when_closing(
    manager: PlaywrightManager, telemetry: TimelineRecorder,
) -> None:
    await manager.start()
    page = await manager.new_page()
    await manager.stop()
    assert await manager.capture_screenshot(1, "S", page) is None
    reasons = [
        e.detail.get("reason") for e in telemetry.events_of(TimelineEventType.MARK)
        if e.name == "screenshot_skipped"
    ]
    assert "closing_or_stopped" in reasons


async def test_three_hooks_share_one_sequence_counter(manager: PlaywrightManager) -> None:
    """三個 hook 交錯 9 次，序號必須是 0001..0009。"""
    await manager.start()
    page = await manager.new_page()
    hooks = [manager.make_screenshot_hook(f"exp{i}", page) for i in range(3)]
    for _ in range(3):
        for hook in hooks:
            hook("A", "B", "e")
    await asyncio.sleep(0)
    await manager.drain_background_tasks()

    sequences = sorted(Path(p).name.split("_")[1] for p in page.shots)
    assert sequences == [f"{i:04d}" for i in range(1, 10)]
    assert len(set(page.shots)) == 9
    await manager.stop()


async def test_hook_freezes_parameters_per_call(manager: PlaywrightManager) -> None:
    await manager.start()
    page = await manager.new_page()
    hook = manager.make_screenshot_hook("exp", page)
    hook("A", "STATE_ONE", "e")
    hook("A", "STATE_TWO", "e")
    await asyncio.sleep(0)
    await manager.drain_background_tasks()
    names = sorted(Path(p).name for p in page.shots)
    assert names == ["exp_0001_STATE_ONE.png", "exp_0002_STATE_TWO.png"]
    await manager.stop()


async def test_hook_is_safe_from_plain_threads(manager: PlaywrightManager) -> None:
    """8 個一般執行緒 × 20 次，不得拋 no-running-loop。"""
    await manager.start()
    page = await manager.new_page()
    hook = manager.make_screenshot_hook("exp", page)
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        try:
            barrier.wait(timeout=5)
            for _ in range(20):
                hook("A", "B", "e")
        except BaseException as exc:  # pragma: no cover - 失敗時才用到
            errors.append(exc)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
        assert t.is_alive() is False
    assert not errors, errors

    for _ in range(50):
        await asyncio.sleep(0)
        if manager.background_task_count >= 160:
            break
    await manager.drain_background_tasks()

    sequences = [Path(p).name.split("_")[1] for p in page.shots]
    assert len(sequences) == 160
    assert len(set(sequences)) == 160
    assert manager.background_task_count == 0
    await manager.stop()


def _call_hook_from_plain_thread(hook: Any) -> list[BaseException]:
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            hook("A", "B", "e")
        except BaseException as exc:  # pragma: no cover - 失敗時才用到
            errors.append(exc)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout=5)
    assert thread.is_alive() is False
    return errors


async def test_hook_after_stop_is_silent_and_leaves_no_orphan_coroutine(
    manager: PlaywrightManager, telemetry: TimelineRecorder,
) -> None:
    """關閉後自一般執行緒呼叫 hook：不拋例外、不留孤兒協程。"""
    await manager.start()
    page = await manager.new_page()
    hook = manager.make_screenshot_hook("exp", page)
    await manager.stop()

    assert not _call_hook_from_plain_thread(hook)
    assert manager.background_task_count == 0
    reasons = [
        e.detail.get("reason") for e in telemetry.events_of(TimelineEventType.MARK)
        if e.name == "screenshot_skipped"
    ]
    assert "closing" in reasons


async def test_hook_without_running_manager_records_no_loop(
    tmp_path: Path, telemetry: TimelineRecorder, playwright: FakePlaywright,
) -> None:
    """尚未 start() -> 沒有 loop 可用，記錄 no_loop 並安全略過。"""
    idle_manager = PlaywrightManager(
        BrowserProfile(name="idle-profile", user_data_dir=tmp_path / "p"),
        telemetry,
        screenshot_dir=tmp_path / "shots",
        playwright_launcher=lambda: playwright,
    )
    hook = idle_manager.make_screenshot_hook("exp", object())
    assert not _call_hook_from_plain_thread(hook)
    assert idle_manager.background_task_count == 0
    reasons = [
        e.detail.get("reason") for e in telemetry.events_of(TimelineEventType.MARK)
        if e.name == "screenshot_skipped"
    ]
    assert "no_loop" in reasons


async def test_hook_records_error_when_screenshot_fails(
    manager: PlaywrightManager, telemetry: TimelineRecorder,
) -> None:
    await manager.start()
    page = await manager.new_page()

    async def boom(path: str) -> None:
        raise RuntimeError("disk full")

    page.screenshot = boom  # type: ignore[method-assign]
    manager.make_screenshot_hook("exp", page)("A", "B", "e")
    await asyncio.sleep(0)
    await manager.drain_background_tasks()
    errors = [e.name for e in telemetry.events_of(TimelineEventType.ERROR)]
    assert "screenshot_failed" in errors
    await manager.stop()


async def test_drain_background_tasks_cancels_on_timeout(manager: PlaywrightManager) -> None:
    await manager.start()

    async def never_finish() -> None:
        await asyncio.sleep(3600)

    task = asyncio.create_task(never_finish())
    manager._background_tasks.add(task)
    await manager.drain_background_tasks(timeout=0.05)
    assert task.cancelled() or task.done()
    assert manager.background_task_count == 0
    await manager.stop()


async def test_drain_background_tasks_noop_when_empty(manager: PlaywrightManager) -> None:
    await manager.start()
    await manager.drain_background_tasks()
    assert manager.background_task_count == 0
    await manager.stop()


# --------------------------------------------------------------- stop 故障注入
@pytest.mark.parametrize("failing_step", ["detach_cdp", "drain", "context_close", "playwright_stop"])
async def test_stop_releases_every_resource_even_when_a_step_fails(
    manager: PlaywrightManager, playwright: FakePlaywright, context: FakeContext,
    failing_step: str,
) -> None:
    """任一步驟拋例外，後續釋放仍須執行且例外向外傳播。"""
    await manager.start()
    await manager.new_page()

    boom = RuntimeError(f"{failing_step} failed")
    if failing_step == "detach_cdp":
        async def failing_detach(page: Any | None = None) -> None:
            raise boom
        manager.detach_cdp = failing_detach  # type: ignore[method-assign]
    elif failing_step == "drain":
        async def failing_drain(timeout: float = 5.0) -> None:
            raise boom
        manager.drain_background_tasks = failing_drain  # type: ignore[method-assign]
    elif failing_step == "context_close":
        context.close_error = boom
    else:
        playwright.stop_error = boom

    with pytest.raises(RuntimeError):
        await manager.stop()

    assert manager._context is None
    assert manager._playwright is None
    assert manager.is_started is False
    if failing_step != "context_close":
        assert context.closed == 1
    if failing_step in {"context_close", "playwright_stop"}:
        assert playwright.stopped == 1
