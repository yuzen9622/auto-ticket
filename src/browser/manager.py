from __future__ import annotations

import asyncio
import itertools
import threading
from collections.abc import Awaitable, Callable
from contextlib import suppress
from importlib import import_module
from pathlib import Path
from typing import Any

from browser.cdp_attach import (
    CDP_CONNECT_TIMEOUT_MS,
    CdpAttachError,
    CdpEndpoint,
    CdpEndpointError,
    PageTarget,
    parse_cdp_endpoint,
    parse_page_target,
    resolve_ws_endpoint,
    select_attached_page,
    validate_ws_endpoint,
)
from browser.cdp_rtt import CdpRttTracker
from browser.context_factory import (
    DEFAULT_SCREENSHOT_DIR,
    BrowserProfile,
    build_persistent_context_options,
    resolve_screenshot_path,
    screenshot_filename,
)
from telemetry.timeline import TimelineEventType, TimelineRecorder


class PlaywrightManager:
    def __init__(
        self,
        profile: BrowserProfile,
        telemetry: TimelineRecorder,
        *,
        screenshot_dir: Path = DEFAULT_SCREENSHOT_DIR,
        playwright_launcher: Any = None,
        cdp_tracker: CdpRttTracker | None = None,
        cdp_endpoint: str | None = None,
        cdp_page_url: str | None = None,
        cdp_ws_resolver: Callable[[CdpEndpoint], Awaitable[str]] | None = None,
    ) -> None:
        self.profile = profile
        self.telemetry = telemetry
        self.screenshot_dir = Path(screenshot_dir)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self._launcher = playwright_launcher
        self._cdp_tracker = cdp_tracker or CdpRttTracker(telemetry)
        if cdp_endpoint is None:
            if cdp_page_url is not None:
                raise CdpEndpointError("cdp_page_url 需搭配 cdp_endpoint")
            self._cdp: CdpEndpoint | None = None
            self._page_target: PageTarget | None = None
        else:
            if cdp_page_url is None:
                raise CdpEndpointError("CDP attach 模式必須指定頁籤 target")
            self._cdp = parse_cdp_endpoint(cdp_endpoint)
            self._page_target = parse_page_target(cdp_page_url)
        self._cdp_ws_resolver = cdp_ws_resolver or resolve_ws_endpoint
        self._started = False
        self._closing = False
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._attached_page: Any = None
        self._owns_context = False
        self._cdp_sessions: dict[int, Any] = {}
        self._background_tasks: set[asyncio.Task[None]] = set()
        # Manager 範圍的單一截圖序號來源。
        # 所有 make_screenshot_hook() 共用，保證多 hook 併存時檔名唯一不覆寫。
        self._screenshot_seq: itertools.count[int] = itertools.count(1)
        # 序號取用鎖：hook 契約允許由「非 event loop 的
        # 一般執行緒」呼叫（FSM callback 為同步函式，可能跑在 to_thread / CDP 執行緒），
        # 不依賴 CPython 對 next() 的隱含原子性。
        self._seq_lock = threading.Lock()
        # manager 所屬 event loop，於 start() 捕獲、stop() 清空。
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def is_started(self) -> bool:
        return self._started

    @property
    def background_task_count(self) -> int:
        return len(self._background_tasks)

    async def _start_playwright(self) -> Any:
        if self._launcher is None:
            async_playwright = import_module("playwright.async_api").async_playwright
            return await async_playwright().start()
        return await self._launcher().start()

    async def _start_launch(self) -> None:
        opts = build_persistent_context_options(self.profile)
        context = await self._playwright.chromium.launch_persistent_context(
            str(self.profile.user_data_dir),
            **opts,
        )
        # Ownership 必須在下一個 await 前落定；若 init script 失敗，teardown 才能關閉 context。
        self._context = context
        self._owns_context = True
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined, configurable: true});"
        )

    async def _start_attach(self, endpoint: CdpEndpoint, target: PageTarget) -> None:
        ws_endpoint = validate_ws_endpoint(
            await self._cdp_ws_resolver(endpoint), endpoint
        )
        self.telemetry.record(
            TimelineEventType.MARK,
            "cdp_attach_discovered",
            endpoint=endpoint.origin,
        )
        try:
            browser = await self._playwright.chromium.connect_over_cdp(
                ws_endpoint,
                timeout=CDP_CONNECT_TIMEOUT_MS,
                no_defaults=True,
            )
        except Exception:
            # Playwright 原始例外可能回顯含 browser UUID 的 ws URL，不可外洩。
            raise CdpAttachError(
                f"無法連接本機 CDP endpoint {endpoint.origin}"
            ) from None
        self._browser = browser
        page = select_attached_page(browser, target)
        self._context = page.context
        self._attached_page = page
        self._owns_context = False
        self.telemetry.record(
            TimelineEventType.MARK,
            "cdp_attach_page_selected",
            target=target.label,
        )
        # 明確不呼叫 add_init_script：那會改動使用者的 context。

    async def start(self) -> None:
        if self._started:
            return
        # 捕獲本 manager 所屬 loop，供跨執行緒 hook 排程。
        self._loop = asyncio.get_running_loop()
        try:
            self._playwright = await self._start_playwright()
            cdp, target = self._cdp, self._page_target
            if cdp is None or target is None:
                await self._start_launch()
            else:
                await self._start_attach(cdp, target)
        except BaseException:
            await self._teardown()
            raise
        self._started = True
        self.telemetry.record(
            TimelineEventType.MARK,
            "browser_started",
            profile=self.profile.name,
            mode="cdp_attach" if self._cdp is not None else "launch",
        )

    async def new_page(self) -> Any:
        if not self._started or self._context is None:
            raise RuntimeError("Browser not started")
        if self._attached_page is not None:
            # attach 模式：只用使用者已開好的那一頁，永不 new_page()。
            if self._attached_page.is_closed():
                raise CdpAttachError("已選定的 CDP 頁籤已被關閉")
            return self._attached_page
        pages = self._context.pages
        if pages:
            return pages[0]
        return await self._context.new_page()

    async def attach_cdp(self, page: Any) -> None:
        session = await page.context.new_cdp_session(page)
        session.on(
            "Network.requestWillBeSent", self._cdp_tracker.on_request_will_be_sent
        )
        session.on("Network.responseReceived", self._cdp_tracker.on_response_received)
        await session.send("Network.enable")
        self._cdp_sessions[id(page)] = session

    async def detach_cdp(self, page: Any | None = None) -> None:
        """CDP 卸載。

        **tracker 清理策略（明定，不得任意更動）**：
        * `page=None`（全量卸載）-> 呼叫 `self._cdp_tracker.clear()`，因為此時已無任何
          page 會再送 response，殘留 pending 永遠配不到對，屬純記憶體洩漏。
        * `page=<單頁>` -> **不得** clear：tracker 為 manager 範圍共用，其他仍掛載的 page
          可能有 in-flight request，清掉會把它們的 RTT 全部誤判為 orphan。該頁殘留的
          pending 由既有 TTL（60s）與 LRU（500）機制自然回收。
        由驗收 8.6 的「單頁 detach 不清 tracker / 全量 detach 清 tracker」雙向測試鎖定。
        """
        if page is not None:
            session = self._cdp_sessions.pop(id(page), None)
            if session is not None:
                with suppress(Exception):
                    session.remove_listener(
                        "Network.requestWillBeSent",
                        self._cdp_tracker.on_request_will_be_sent,
                    )
                    session.remove_listener(
                        "Network.responseReceived",
                        self._cdp_tracker.on_response_received,
                    )
                with suppress(Exception):
                    await session.detach()
        else:
            sessions = list(self._cdp_sessions.values())
            self._cdp_sessions.clear()
            self._cdp_tracker.clear()  # 釋放 CDP Tracker 暫存佇列
            for s in sessions:
                with suppress(Exception):
                    s.remove_listener(
                        "Network.requestWillBeSent",
                        self._cdp_tracker.on_request_will_be_sent,
                    )
                    s.remove_listener(
                        "Network.responseReceived",
                        self._cdp_tracker.on_response_received,
                    )
                with suppress(Exception):
                    await s.detach()

    async def capture_screenshot(
        self,
        sequence: int,
        state: str,
        page: Any | None = None,
        *,
        experiment_id: str | None = None,
    ) -> Path | None:
        if self._closing or not self._started:
            self.telemetry.record(
                TimelineEventType.MARK,
                "screenshot_skipped",
                reason="closing_or_stopped",
            )
            return None
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        exp_id = experiment_id or self.profile.name
        fname = screenshot_filename(exp_id, sequence, state)
        dest = resolve_screenshot_path(self.screenshot_dir, fname)
        target_page = page or await self.new_page()
        await target_page.screenshot(path=str(dest))
        return dest

    def make_screenshot_hook(
        self, experiment_id: str, page: Any
    ) -> Callable[[str, str, str], None]:
        """產生 FSM 轉移用的同步截圖 hook。

        **執行緒契約**：本 hook 是同步函式，明確允許由
        任意執行緒呼叫（FSM callback 必為同步，可能跑在 `asyncio.to_thread`、CDP 事件
        執行緒或測試的一般 thread）。因此**嚴禁**在呼叫端執行緒直接 `asyncio.create_task()`
        ——非 event-loop 執行緒會拋 `RuntimeError: no running event loop`，並遺留未 await
        的協程（`-W error::RuntimeWarning` 下直接失敗）。一律走下方分派：
        * 呼叫端就在 manager 所屬 loop -> 直接 `create_task`；
        * 其他執行緒 -> `loop.call_soon_threadsafe()`，**協程物件在 loop 執行緒內才建立**，
          排程失敗（loop 已關閉）時完全不會產生孤兒協程。
        `_background_tasks` 因此只在 loop 執行緒被異動，無需額外加鎖。
        """

        # 嚴禁在此重新建立計數器；
        # 必須取用 Manager 範圍的 self._screenshot_seq，否則多 hook 會各自從 1 起算而覆寫檔案。
        def hook(source: str, target: str, event: str) -> None:
            with self._seq_lock:
                sequence = next(self._screenshot_seq)
            if self._closing:
                # 關閉中：不再排程新的截圖，但仍留下可觀測的略過紀錄。
                self.telemetry.record(
                    TimelineEventType.MARK,
                    "screenshot_skipped",
                    reason="closing",
                )
                return

            loop = self._loop
            if loop is None or loop.is_closed():
                self.telemetry.record(
                    TimelineEventType.MARK,
                    "screenshot_skipped",
                    reason="no_loop",
                )
                return

            frozen_seq = sequence
            frozen_state = str(getattr(target, "id", target))
            frozen_exp = str(experiment_id)

            async def _bg(
                seq: int = frozen_seq,
                state_name: str = frozen_state,
                exp_id: str = frozen_exp,
            ) -> None:
                try:
                    await self.capture_screenshot(
                        seq, state_name, page, experiment_id=exp_id
                    )
                except Exception as exc:
                    self.telemetry.record_error(
                        "screenshot_failed", exc, state=state_name, experiment_id=exp_id
                    )

            def _spawn() -> None:
                # 於 loop 執行緒內建立協程並登記；此處不可能拋 no-running-loop。
                task = loop.create_task(_bg())
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)

            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None

            if running is loop:
                _spawn()
            else:
                try:
                    loop.call_soon_threadsafe(_spawn)
                except RuntimeError:
                    self.telemetry.record(
                        TimelineEventType.MARK,
                        "screenshot_skipped",
                        reason="loop_closed",
                    )

        return hook

    async def drain_background_tasks(self, timeout: float = 5.0) -> None:
        if not self._background_tasks:
            return
        tasks = tuple(self._background_tasks)
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            self._background_tasks.clear()

    async def _teardown(self) -> None:
        self._closing = True
        try:
            await self.detach_cdp()
        finally:
            try:
                await self.drain_background_tasks(timeout=5.0)
            finally:
                try:
                    if self._owns_context and self._context is not None:
                        await self._context.close()
                finally:
                    self._context = None
                    self._browser = None
                    self._attached_page = None
                    self._owns_context = False
                    try:
                        if self._playwright is not None:
                            await self._playwright.stop()
                    finally:
                        self._playwright = None
                        self._started = False
                        # loop 參考一併釋放；
                        # 之後任何 hook 呼叫都走 "no_loop" 略過路徑，不會產生孤兒協程。
                        self._loop = None
                        # 重設，使同一 manager 可在 stop 後重新 start。
                        self._closing = False

    async def stop(self) -> None:
        if not self._started and self._playwright is None:
            return
        await self._teardown()
