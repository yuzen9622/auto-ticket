"""瀏覽器管理與截圖 hook。"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest

from browser.cdp_attach import (
    CdpAttachError,
    CdpDiscoveryError,
    CdpEndpointError,
    CdpPageSelectionError,
)
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
        self.listeners = [
            (n, h) for n, h in self.listeners if not (n == name and h == handler)
        ]

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

    async def launch_persistent_context(
        self, user_data_dir: str, **opts: Any
    ) -> FakeContext:
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
def manager(
    tmp_path: Path, telemetry: TimelineRecorder, playwright: FakePlaywright
) -> PlaywrightManager:
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
    opts = build_persistent_context_options(
        BrowserProfile(name="p1"), extra_args=("--mute-audio",)
    )
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
    manager: PlaywrightManager,
    playwright: FakePlaywright,
    context: FakeContext,
    telemetry: TimelineRecorder,
) -> None:
    await manager.start()
    assert manager.is_started is True
    assert playwright.chromium.launch_calls
    assert "webdriver" in context.init_scripts[0]
    marks = [e.name for e in telemetry.events_of(TimelineEventType.MARK)]
    assert "browser_started" in marks
    await manager.stop()


async def test_start_is_idempotent(
    manager: PlaywrightManager, playwright: FakePlaywright
) -> None:
    await manager.start()
    await manager.start()
    assert len(playwright.chromium.launch_calls) == 1
    await manager.stop()


async def test_new_page_requires_started(manager: PlaywrightManager) -> None:
    with pytest.raises(RuntimeError):
        await manager.new_page()


async def test_stop_is_idempotent(
    manager: PlaywrightManager, playwright: FakePlaywright, context: FakeContext
) -> None:
    await manager.start()
    await manager.stop()
    await manager.stop()
    assert context.closed == 1
    assert playwright.stopped == 1
    assert manager.is_started is False


# --------------------------------------------------------------- CDP
async def test_attach_and_full_detach_clears_tracker(
    manager: PlaywrightManager,
    context: FakeContext,
    telemetry: TimelineRecorder,
) -> None:
    await manager.start()
    page = await manager.new_page()
    await manager.attach_cdp(page)
    session = context.sessions[0]
    assert [n for n, _ in session.listeners] == [
        "Network.requestWillBeSent",
        "Network.responseReceived",
    ]
    assert session.sent == ["Network.enable"]

    manager._cdp_tracker.on_request_will_be_sent(
        {"requestId": "r1", "request": {"url": "u"}}
    )
    assert manager._cdp_tracker.pending_count == 1

    await manager.detach_cdp()
    assert session.listeners == []
    assert session.detached is True
    assert manager._cdp_tracker.pending_count == 0
    await manager.stop()


async def test_single_page_detach_keeps_shared_tracker(
    manager: PlaywrightManager,
    context: FakeContext,
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
    manager: PlaywrightManager,
    tmp_path: Path,
) -> None:
    await manager.start()
    page = await manager.new_page()
    dest = await manager.capture_screenshot(3, "SALE_OPEN", page, experiment_id="exp")
    assert dest is not None
    assert dest.name == "exp_0003_SALE_OPEN.png"
    assert page.shots == [str(dest)]
    await manager.stop()


async def test_capture_screenshot_skipped_when_closing(
    manager: PlaywrightManager,
    telemetry: TimelineRecorder,
) -> None:
    await manager.start()
    page = await manager.new_page()
    await manager.stop()
    assert await manager.capture_screenshot(1, "S", page) is None
    reasons = [
        e.detail.get("reason")
        for e in telemetry.events_of(TimelineEventType.MARK)
        if e.name == "screenshot_skipped"
    ]
    assert "closing_or_stopped" in reasons


async def test_three_hooks_share_one_sequence_counter(
    manager: PlaywrightManager,
) -> None:
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
    manager: PlaywrightManager,
    telemetry: TimelineRecorder,
) -> None:
    """關閉後自一般執行緒呼叫 hook：不拋例外、不留孤兒協程。

    `stop()` 已將 `_closing` 重設以允許重新 start，因此略過理由是 `no_loop`
    （`_loop` 同步釋放）而非 `closing`。
    """
    await manager.start()
    page = await manager.new_page()
    hook = manager.make_screenshot_hook("exp", page)
    await manager.stop()

    assert not _call_hook_from_plain_thread(hook)
    assert manager.background_task_count == 0
    reasons = [
        e.detail.get("reason")
        for e in telemetry.events_of(TimelineEventType.MARK)
        if e.name == "screenshot_skipped"
    ]
    assert reasons == ["no_loop"]


async def test_hook_without_running_manager_records_no_loop(
    tmp_path: Path,
    telemetry: TimelineRecorder,
    playwright: FakePlaywright,
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
        e.detail.get("reason")
        for e in telemetry.events_of(TimelineEventType.MARK)
        if e.name == "screenshot_skipped"
    ]
    assert "no_loop" in reasons


async def test_hook_records_error_when_screenshot_fails(
    manager: PlaywrightManager,
    telemetry: TimelineRecorder,
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


async def test_drain_background_tasks_cancels_on_timeout(
    manager: PlaywrightManager,
) -> None:
    await manager.start()

    async def never_finish() -> None:
        await asyncio.sleep(3600)

    task = asyncio.create_task(never_finish())
    manager._background_tasks.add(task)
    await manager.drain_background_tasks(timeout=0.05)
    assert task.cancelled() or task.done()
    assert manager.background_task_count == 0
    await manager.stop()


async def test_drain_background_tasks_noop_when_empty(
    manager: PlaywrightManager,
) -> None:
    await manager.start()
    await manager.drain_background_tasks()
    assert manager.background_task_count == 0
    await manager.stop()


# --------------------------------------------------------------- stop 故障注入
@pytest.mark.parametrize(
    "failing_step", ["detach_cdp", "drain", "context_close", "playwright_stop"]
)
async def test_stop_releases_every_resource_even_when_a_step_fails(
    manager: PlaywrightManager,
    playwright: FakePlaywright,
    context: FakeContext,
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


# --------------------------------------------------------------- CDP 借用模式
# 以下 fakes 一旦被「改動或關閉借來的 session」就立刻 AssertionError：
# ownership 的主證據是這些 runtime sentinel，而非靜態掃描。
class SentinelBorrowedContext:
    def __init__(self, *pages: SentinelBorrowedPage) -> None:
        self.pages: list[SentinelBorrowedPage] = list(pages)
        self.sessions: list[FakeCdpSession] = []
        for page in self.pages:
            page.context = self

    async def close(self) -> None:
        raise AssertionError("borrowed context must not be closed")

    async def new_page(self) -> SentinelBorrowedPage:
        raise AssertionError("borrowed context must not be mutated")

    async def add_init_script(self, script: str) -> None:
        raise AssertionError("borrowed context must not be mutated")

    async def new_cdp_session(self, page: Any) -> FakeCdpSession:
        session = FakeCdpSession()
        self.sessions.append(session)
        return session


class SentinelBorrowedPage:
    def __init__(self, url: str, *, closed: bool = False) -> None:
        self.url = url
        self.context: Any = None
        self.shots: list[str] = []
        self._closed = closed

    def is_closed(self) -> bool:
        return self._closed

    async def screenshot(self, path: str) -> None:
        self.shots.append(path)


class SentinelBrowser:
    def __init__(self, *contexts: SentinelBorrowedContext) -> None:
        self.contexts = list(contexts)

    async def close(self) -> None:
        raise AssertionError("must not close borrowed browser")


class FakeCdpChromium:
    def __init__(self, browser: SentinelBrowser) -> None:
        self._browser = browser
        self.connect_calls: list[tuple[str, dict[str, Any]]] = []
        self.connect_error: BaseException | None = None

    async def launch_persistent_context(self, user_data_dir: str, **opts: Any) -> Any:
        raise AssertionError("attach mode must never launch a browser")

    async def connect_over_cdp(
        self, ws_endpoint: str, **kwargs: Any
    ) -> SentinelBrowser:
        self.connect_calls.append((ws_endpoint, kwargs))
        if self.connect_error is not None:
            raise self.connect_error
        return self._browser


class FakeCdpPlaywright:
    def __init__(self, browser: SentinelBrowser) -> None:
        self.chromium = FakeCdpChromium(browser)
        self.stopped = 0

    async def start(self) -> FakeCdpPlaywright:
        return self

    async def stop(self) -> None:
        self.stopped += 1


CDP_ENDPOINT = "http://127.0.0.1:9222"
CDP_WS = "ws://127.0.0.1:9222/devtools/browser/x"
CDP_TARGET_URL = "https://example.com/events/1"


async def _resolver(endpoint: Any) -> str:
    return CDP_WS


def _attach_manager(
    tmp_path: Path,
    telemetry: TimelineRecorder,
    playwright: FakeCdpPlaywright,
    *,
    resolver: Any = _resolver,
    page_url: str = CDP_TARGET_URL,
) -> PlaywrightManager:
    return PlaywrightManager(
        BrowserProfile(name="borrowed", user_data_dir=tmp_path / "profile"),
        telemetry,
        screenshot_dir=tmp_path / "shots",
        playwright_launcher=lambda: playwright,
        cdp_endpoint=CDP_ENDPOINT,
        cdp_page_url=page_url,
        cdp_ws_resolver=resolver,
    )


def _single_match_browser() -> tuple[SentinelBrowser, SentinelBorrowedPage]:
    wanted = SentinelBorrowedPage("https://example.com/events/1?utm=x")
    browser = SentinelBrowser(
        SentinelBorrowedContext(
            SentinelBorrowedPage("about:blank"),
            SentinelBorrowedPage("https://evil.example/events/1"),
        ),
        SentinelBorrowedContext(
            wanted, SentinelBorrowedPage("https://example.com/events/2")
        ),
        SentinelBorrowedContext(
            SentinelBorrowedPage("https://example.com/events/1", closed=True)
        ),
    )
    return browser, wanted


def test_attach_mode_requires_a_page_target(
    tmp_path: Path, telemetry: TimelineRecorder
) -> None:
    with pytest.raises(CdpEndpointError):
        PlaywrightManager(
            BrowserProfile(name="borrowed", user_data_dir=tmp_path / "profile"),
            telemetry,
            screenshot_dir=tmp_path / "shots",
            cdp_endpoint=CDP_ENDPOINT,
        )


def test_fallback_urls_requires_cdp_endpoint(
    tmp_path: Path, telemetry: TimelineRecorder
) -> None:
    with pytest.raises(CdpEndpointError, match="cdp_page_fallback_urls 需搭配 cdp_endpoint"):
        PlaywrightManager(
            BrowserProfile(name="borrowed", user_data_dir=tmp_path / "profile"),
            telemetry,
            screenshot_dir=tmp_path / "shots",
            cdp_page_fallback_urls=["https://kktix.com/"],
        )


async def test_attach_mode_selects_redirected_page_via_fallback_url(
    tmp_path: Path, telemetry: TimelineRecorder
) -> None:
    wanted = SentinelBorrowedPage("https://kktix.com/")
    browser = SentinelBrowser(
        SentinelBorrowedContext(
            SentinelBorrowedPage("about:blank"),
            wanted,
        ),
    )
    playwright = FakeCdpPlaywright(browser)
    manager = PlaywrightManager(
        BrowserProfile(name="borrowed", user_data_dir=tmp_path / "profile"),
        telemetry,
        screenshot_dir=tmp_path / "shots",
        playwright_launcher=lambda: playwright,
        cdp_endpoint=CDP_ENDPOINT,
        cdp_page_url="https://kktix.com/users/sign_in",
        cdp_page_fallback_urls=["https://kktix.com/"],
        cdp_ws_resolver=_resolver,
    )
    await manager.start()
    page = await manager.new_page()
    assert page is wanted
    await manager.stop()


async def test_attach_mode_uses_connect_over_cdp_with_no_defaults(
    tmp_path: Path,
    telemetry: TimelineRecorder,
) -> None:
    browser, _ = _single_match_browser()
    playwright = FakeCdpPlaywright(browser)
    manager = _attach_manager(tmp_path, telemetry, playwright)

    await manager.start()

    (ws_endpoint, kwargs) = playwright.chromium.connect_calls[0]
    assert len(playwright.chromium.connect_calls) == 1
    assert ws_endpoint == CDP_WS
    assert ws_endpoint != CDP_ENDPOINT
    assert kwargs["no_defaults"] is True
    marks = {e.name for e in telemetry.events_of(TimelineEventType.MARK)}
    assert {
        "cdp_attach_discovered",
        "cdp_attach_page_selected",
        "browser_started",
    } <= marks
    await manager.stop()


async def test_attach_mode_selects_the_single_matching_page(
    tmp_path: Path,
    telemetry: TimelineRecorder,
) -> None:
    browser, wanted = _single_match_browser()
    manager = _attach_manager(tmp_path, telemetry, FakeCdpPlaywright(browser))

    await manager.start()
    assert await manager.new_page() is wanted
    assert await manager.new_page() is wanted
    await manager.stop()


async def test_attached_page_closed_after_selection_fails_closed(
    tmp_path: Path,
    telemetry: TimelineRecorder,
) -> None:
    browser, wanted = _single_match_browser()
    manager = _attach_manager(tmp_path, telemetry, FakeCdpPlaywright(browser))

    await manager.start()
    wanted._closed = True
    with pytest.raises(CdpAttachError, match="頁籤已被關閉"):
        await manager.new_page()
    await manager.stop()


@pytest.mark.parametrize(
    "pages",
    [
        (),
        ("https://example.com/events/1", "https://example.com/events/1/register"),
    ],
    ids=["no_match", "ambiguous"],
)
async def test_attach_mode_refuses_anything_but_exactly_one_match(
    tmp_path: Path,
    telemetry: TimelineRecorder,
    pages: tuple[str, ...],
) -> None:
    browser = SentinelBrowser(
        *(SentinelBorrowedContext(SentinelBorrowedPage(url)) for url in pages)
    )
    playwright = FakeCdpPlaywright(browser)
    manager = _attach_manager(tmp_path, telemetry, playwright)

    with pytest.raises(CdpPageSelectionError):
        await manager.start()
    assert manager.is_started is False
    assert playwright.stopped == 1


async def test_stop_does_not_touch_borrowed_browser_or_context(
    tmp_path: Path,
    telemetry: TimelineRecorder,
) -> None:
    """ownership 主證據：借來的 browser / context 全程不得被關閉或改動。"""
    browser, wanted = _single_match_browser()
    playwright = FakeCdpPlaywright(browser)
    manager = _attach_manager(tmp_path, telemetry, playwright)

    await manager.start()
    page = await manager.new_page()
    await manager.attach_cdp(page)
    await manager.stop()

    assert manager.is_started is False
    assert playwright.stopped == 1
    assert page is wanted
    borrowed_context = wanted.context
    assert borrowed_context.sessions[0].detached is True


async def test_attach_mode_does_not_inject_init_script(
    tmp_path: Path,
    telemetry: TimelineRecorder,
) -> None:
    """`add_init_script` 會改動使用者的 context，attach 路徑必須完全不碰。"""
    browser, wanted = _single_match_browser()
    manager = _attach_manager(tmp_path, telemetry, FakeCdpPlaywright(browser))

    await manager.start()
    assert not hasattr(wanted.context, "init_scripts")
    await manager.stop()


async def test_failed_attach_leaves_manager_clean_and_retryable(
    tmp_path: Path,
    telemetry: TimelineRecorder,
) -> None:
    browser, wanted = _single_match_browser()
    playwright = FakeCdpPlaywright(browser)
    attempts = 0

    async def flaky(endpoint: Any) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise CdpDiscoveryError("discovery failed")
        return CDP_WS

    manager = _attach_manager(tmp_path, telemetry, playwright, resolver=flaky)

    with pytest.raises(CdpDiscoveryError):
        await manager.start()
    assert manager.is_started is False
    assert playwright.stopped == 1
    assert not playwright.chromium.connect_calls

    await manager.start()
    assert manager.is_started is True
    assert await manager.new_page() is wanted
    await manager.stop()
    assert playwright.stopped == 2


async def test_untrusted_resolver_cannot_bypass_loopback_validation(
    tmp_path: Path,
    telemetry: TimelineRecorder,
) -> None:
    browser, _ = _single_match_browser()
    playwright = FakeCdpPlaywright(browser)

    async def malicious(endpoint: Any) -> str:
        return "ws://evil.example:9222/devtools/browser/x"

    manager = _attach_manager(tmp_path, telemetry, playwright, resolver=malicious)
    with pytest.raises(CdpDiscoveryError):
        await manager.start()
    assert playwright.chromium.connect_calls == []
    assert playwright.stopped == 1


async def test_connect_failure_is_sanitized(
    tmp_path: Path, telemetry: TimelineRecorder
) -> None:
    browser, _ = _single_match_browser()
    playwright = FakeCdpPlaywright(browser)
    playwright.chromium.connect_error = RuntimeError(
        "connect failed: ws://127.0.0.1:9222/devtools/browser/SECRET-UUID"
    )
    manager = _attach_manager(tmp_path, telemetry, playwright)

    with pytest.raises(CdpAttachError) as caught:
        await manager.start()

    message = str(caught.value)
    assert "SECRET-UUID" not in message
    assert "devtools" not in message
    assert caught.value.__cause__ is None
    details = " ".join(
        str(e.detail) for e in telemetry.events_of(TimelineEventType.MARK)
    )
    assert "SECRET-UUID" not in details
    assert "devtools" not in details
    assert playwright.stopped == 1


async def test_stop_after_failed_start_is_idempotent(
    tmp_path: Path,
    telemetry: TimelineRecorder,
) -> None:
    browser, _ = _single_match_browser()
    playwright = FakeCdpPlaywright(browser)

    async def broken(endpoint: Any) -> str:
        raise CdpDiscoveryError("discovery failed")

    manager = _attach_manager(tmp_path, telemetry, playwright, resolver=broken)

    with pytest.raises(CdpDiscoveryError):
        await manager.start()
    await manager.stop()
    await manager.stop()
    assert playwright.stopped == 1


async def test_launch_mode_still_closes_owned_context(
    manager: PlaywrightManager,
    playwright: FakePlaywright,
    context: FakeContext,
    telemetry: TimelineRecorder,
) -> None:
    await manager.start()
    await manager.stop()
    assert context.closed == 1
    modes = [
        e.detail.get("mode")
        for e in telemetry.events_of(TimelineEventType.MARK)
        if e.name == "browser_started"
    ]
    assert modes == ["launch"]


async def test_launch_init_script_failure_closes_context_and_is_retryable(
    manager: PlaywrightManager,
    playwright: FakePlaywright,
    context: FakeContext,
) -> None:
    """owned context 在 init script 失敗時仍須關閉，否則 profile 會被鎖住。"""
    boom = RuntimeError("init script failed")
    calls = 0
    original = context.add_init_script

    async def flaky_init_script(script: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise boom
        await original(script)

    context.add_init_script = flaky_init_script  # type: ignore[method-assign]

    with pytest.raises(RuntimeError):
        await manager.start()
    assert context.closed == 1
    assert playwright.stopped == 1
    assert manager.is_started is False
    assert manager._context is None
    assert manager._playwright is None

    await manager.start()
    assert manager.is_started is True
    await manager.stop()
    assert context.closed == 2
    assert playwright.stopped == 2


async def test_screenshot_hook_skips_after_stop(
    tmp_path: Path,
    telemetry: TimelineRecorder,
) -> None:
    """`_closing` 重設後 hook 仍須走 `no_loop` 略過分支，不得排程新截圖。"""
    browser, _ = _single_match_browser()
    manager = _attach_manager(tmp_path, telemetry, FakeCdpPlaywright(browser))

    await manager.start()
    page = await manager.new_page()
    hook = manager.make_screenshot_hook("exp", page)
    await manager.stop()

    hook("A", "B", "e")
    assert manager.background_task_count == 0
    assert page.shots == []
    reasons = [
        e.detail.get("reason")
        for e in telemetry.events_of(TimelineEventType.MARK)
        if e.name == "screenshot_skipped"
    ]
    assert reasons == ["no_loop"]
