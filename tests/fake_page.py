# ruff: noqa: S112, BLE001
"""離線測試用的假 Page／Locator（以 BeautifulSoup 驅動真實頁面快照）。

只實作 adapter 與 dom 模組實際用到的 Playwright API 子集，並用
`selectors.css_only()` 濾掉 soupsieve 無法解析的 Playwright 專屬語法——
於是「選擇器候選順位」在離線測試中也是真的在被逐一嘗試。

**絕不啟動真實瀏覽器**：整份測試套件掛 netguard，這裡也不 import playwright。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from adapters.ticketing.kktix.selectors import KKTIXSelectors, css_only

FIXTURES = Path(__file__).parent / "fixtures"


def load_page_html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeTimeoutError(RuntimeError):
    """對應 Playwright 的 TimeoutError：元素在時限內未出現。"""


def _is_unit(tag: Any) -> bool:
    classes = tag.get("class") or []
    return (
        "ticket-unit" in classes
        or "display-table" in classes
        or str(tag.get("id", "")).startswith("ticket_")
    )


class FakeLocator:
    def __init__(self, page: FakePage, selector: str, elements: list[Any]) -> None:
        self.page = page
        self.selector = selector
        self.elements = list(elements)

    @property
    def first(self) -> FakeLocator:
        return FakeLocator(self.page, self.selector, self.elements[:1])

    def locator(self, selector: str) -> FakeLocator:
        found: list[Any] = []
        for element in self.elements:
            found.extend(self.page.select_within(element, selector))
        return FakeLocator(self.page, selector, found)

    async def count(self) -> int:
        return len(self.elements)

    async def all(self) -> list[FakeLocator]:
        return [FakeLocator(self.page, self.selector, [e]) for e in self.elements]

    async def wait_for(
        self, state: str = "visible", timeout: float | None = None
    ) -> None:
        # 記下每一次等待的預算：「選配探測不得使用長預算」靠這裡才驗得到。
        self.page.wait_timeouts.append((self.selector, timeout))
        if not self.elements:
            # 真實 Playwright 的 wait_for 會在 timeout 內持續輪詢，元素可能稍後才渲染。
            revealed = self.page.flush_pending_render(self.selector)
            if revealed:
                self.elements = revealed
                return
            raise FakeTimeoutError(f"{self.selector} not visible")

    @property
    def element(self) -> Any:
        if not self.elements:
            raise FakeTimeoutError(f"{self.selector} has no element")
        return self.elements[0]

    async def inner_text(self) -> str:
        return self.element.get_text(" ", strip=True)

    async def input_value(self) -> str:
        return str(self.element.get("value", ""))

    async def is_checked(self) -> bool:
        return self.element.get("checked") is not None

    async def is_visible(self) -> bool:
        """對應 Playwright 的即時可見度判定（不等待）。

        Bootstrap 會把彈窗骨架留在 DOM 裡，只靠 `count()` 會把從未顯示的模板
        誤判成「正在彈的訊息」。這裡以 `hidden` 屬性與 `display:none` 樣式模擬
        那個差異，讓「隱藏的彈窗模板不算彈窗」在離線測試中真的驗得到。
        """
        if not self.elements:
            return False
        element = self.element
        if element.get("hidden") is not None:
            return False
        style = str(element.get("style", "")).replace(" ", "").lower()
        return "display:none" not in style

    async def get_attribute(self, name: str) -> str | None:
        value = self.element.get(name)
        return None if value is None else str(value)

    async def click(self) -> None:
        element = self.element
        self.page.clicks.append(self.selector)
        self.page.clicked_elements.append(element)
        self.page.apply_click(element)

    async def fill(self, value: str) -> None:
        element = self.element
        element["value"] = value
        self.page.fills.append((self.selector, value))

    async def evaluate(self, script: str, *, timeout: float | None = None) -> None:
        if self.page.evaluate_error is not None:
            raise self.page.evaluate_error
        self.page.dispatches.append((self.element.name, script))

    async def screenshot(self, **kwargs: Any) -> bytes:
        """對應 Playwright 的元素截圖；離線測試回傳可辨識的決定性 bytes。"""
        element = self.element
        self.page.screenshots.append(self.selector)
        token = self.page.image_tokens.get(self.selector, "0")
        return f"fake-image:{element.get('id') or element.name}:{token}".encode()


class FakePage:
    def __init__(
        self, html: str, *, url: str = "https://registration.test/events/x"
    ) -> None:
        self.soup = BeautifulSoup(html, "html.parser")
        self.url = url
        self.clicks: list[str] = []
        self.clicked_elements: list[Any] = []
        self.fills: list[tuple[str, str]] = []
        self.dispatches: list[tuple[str, str]] = []
        self.goto_urls: list[str] = []
        self.goto_kwargs: list[dict[str, Any]] = []
        self.load_states: list[str] = []
        self.screenshots: list[str] = []
        self.image_tokens: dict[str, str] = {}
        self.fail_load_state = False
        self.quantity_step = 1
        self.click_transitions: list[tuple[str, str]] = []
        self.evaluate_error: Exception | None = None
        self._pending_render: str | None = None
        self.wait_timeouts: list[tuple[str, float | None]] = []
        # 導頁進行中的 `content()` 會拋錯：設此旗標可重现那一次失敗。
        self.content_error_once: Exception | None = None

    @classmethod
    def from_fixture(cls, name: str, **kwargs: Any) -> FakePage:
        return cls(load_page_html(name), **kwargs)

    # ------------------------------------------------------------- 選擇器

    def select_within(self, root: Any, selector: str) -> list[Any]:
        found: list[Any] = []
        seen: set[int] = set()
        for part in css_only(selector):
            try:
                matches = root.select(part)
            except Exception:
                continue
            for tag in matches:
                if id(tag) not in seen:
                    seen.add(id(tag))
                    found.append(tag)
        return found

    def _quantity_inputs(self, unit: Any) -> list[Any]:
        found: list[Any] = []
        seen: set[int] = set()
        for part in css_only(KKTIXSelectors.TICKET_QUANTITY_INPUT):
            for tag in self.select_within(unit, part):
                if id(tag) not in seen:
                    seen.add(id(tag))
                    found.append(tag)
        return found

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector, self.select_within(self.soup, selector))

    # --------------------------------------------------------------- 行為

    def render_after_wait(self, html: str) -> None:
        """模擬非同步渲染：要等到有人真的 `wait_for` 落空，`html` 才會被掛上。"""
        self._pending_render = html

    def flush_pending_render(self, selector: str) -> list[Any]:
        if self._pending_render is None:
            return []
        self.soup = BeautifulSoup(self._pending_render, "html.parser")
        self._pending_render = None
        return self.select_within(self.soup, selector)

    def on_click(self, trigger: str, next_html: str) -> None:
        """點到帶有 `trigger`（id／ng-click／class）的元素後，頁面換成 `next_html`。"""
        self.click_transitions.append((trigger, next_html))

    def _maybe_navigate(self, element: Any) -> None:
        classes = " ".join(element.get("class") or [])
        signature = " ".join(
            str(x)
            for x in (element.get("id", ""), element.get("ng-click", ""), classes)
        )
        for index, (trigger, next_html) in enumerate(self.click_transitions):
            if trigger in signature:
                self.soup = BeautifulSoup(next_html, "html.parser")
                del self.click_transitions[index]
                return

    def apply_click(self, element: Any) -> None:
        self._maybe_navigate(element)
        classes = element.get("class") or []
        if element.name == "input" and element.get("type") == "checkbox":
            element["checked"] = "checked"
            return
        delta = 0
        if "plus" in classes:
            delta = self.quantity_step
        elif "minus" in classes:
            delta = -self.quantity_step
        if delta == 0:
            return
        unit = element
        while unit is not None and not (getattr(unit, "get", None) and _is_unit(unit)):
            unit = unit.parent
        if unit is None:
            return
        # 數量欄位一律跟著註冊表走：實站改版時這裡不該還拿舊字串假裝選得到。
        for candidate in self._quantity_inputs(unit):
            current = int(str(candidate.get("value", "0")) or 0)
            candidate["value"] = str(max(0, current + delta))
            return

    async def goto(
        self, url: str, *, wait_until: str | None = None, timeout: float | None = None
    ) -> None:
        self.goto_urls.append(url)
        self.goto_kwargs.append({"wait_until": wait_until, "timeout": timeout})
        self.url = url

    async def content(self) -> str:
        if self.content_error_once is not None:
            error, self.content_error_once = self.content_error_once, None
            raise error
        return str(self.soup)

    async def wait_for_load_state(
        self, state: str = "load", timeout: float | None = None
    ) -> None:
        if self.fail_load_state:
            raise FakeTimeoutError(f"load state {state} timed out")
        self.load_states.append(state)

    async def screenshot(self, path: str) -> None:
        self.screenshots.append(path)
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\n")


class FakeBrowser:
    """PlaywrightManager 的替身：只實作協調器用到的部分，不啟動任何瀏覽器。"""

    def __init__(self, page: FakePage, screenshot_dir: Path) -> None:
        self.page = page
        self.screenshot_dir = Path(screenshot_dir)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.started = False
        self.stopped = False
        self.attached: list[FakePage] = []
        self.transitions: list[tuple[str, str, str]] = []
        self.drained = False
        self.write_screenshots = True
        self._sequence = 0

    async def start(self) -> None:
        self.started = True

    async def new_page(self) -> FakePage:
        return self.page

    async def attach_cdp(self, page: FakePage) -> None:
        self.attached.append(page)

    async def stop(self) -> None:
        self.stopped = True

    async def drain_background_tasks(self, timeout: float = 5.0) -> None:
        self.drained = True

    def make_screenshot_hook(self, experiment_id: str, page: FakePage) -> Any:
        from browser.context_factory import screenshot_filename

        def hook(source: str, target: str, event: str) -> None:
            self._sequence += 1
            self.transitions.append((source, target, event))
            if not self.write_screenshots:
                return  # 模擬背景截圖被逾時取消
            name = screenshot_filename(experiment_id, self._sequence, target)
            (self.screenshot_dir / name).write_bytes(b"\x89PNG\r\n\x1a\n")

        return hook
