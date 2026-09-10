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

from adapters.ticketing.kktix.selectors import css_only

FIXTURES = Path(__file__).parent / "fixtures"


def load_page_html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeTimeoutError(RuntimeError):
    """對應 Playwright 的 TimeoutError：元素在時限內未出現。"""


def _is_unit(tag: Any) -> bool:
    classes = tag.get("class") or []
    return "ticket-unit" in classes or "display-table" in classes or str(tag.get("id", "")).startswith("ticket_")


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

    async def wait_for(self, state: str = "visible", timeout: float | None = None) -> None:
        if not self.elements:
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

    async def click(self) -> None:
        element = self.element
        self.page.clicks.append(self.selector)
        self.page.clicked_elements.append(element)
        self.page.apply_click(element)

    async def fill(self, value: str) -> None:
        element = self.element
        element["value"] = value
        self.page.fills.append((self.selector, value))

    async def evaluate(self, script: str) -> None:
        self.page.dispatches.append((self.element.name, script))


class FakePage:
    def __init__(self, html: str, *, url: str = "https://registration.test/events/x") -> None:
        self.soup = BeautifulSoup(html, "html.parser")
        self.url = url
        self.clicks: list[str] = []
        self.clicked_elements: list[Any] = []
        self.fills: list[tuple[str, str]] = []
        self.dispatches: list[tuple[str, str]] = []
        self.goto_urls: list[str] = []
        self.load_states: list[str] = []
        self.screenshots: list[str] = []
        self.fail_load_state = False
        self.quantity_step = 1
        self.click_transitions: list[tuple[str, str]] = []

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

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector, self.select_within(self.soup, selector))

    # --------------------------------------------------------------- 行為

    def on_click(self, trigger: str, next_html: str) -> None:
        """點到帶有 `trigger`（id／ng-click／class）的元素後，頁面換成 `next_html`。"""
        self.click_transitions.append((trigger, next_html))

    def _maybe_navigate(self, element: Any) -> None:
        classes = " ".join(element.get("class") or [])
        signature = " ".join(
            str(x) for x in (element.get("id", ""), element.get("ng-click", ""), classes)
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
        for candidate in self.select_within(unit, "input.ticket-quantity, input[type='number']"):
            current = int(str(candidate.get("value", "0")) or 0)
            candidate["value"] = str(max(0, current + delta))
            return

    async def goto(self, url: str) -> None:
        self.goto_urls.append(url)
        self.url = url

    async def content(self) -> str:
        return str(self.soup)

    async def wait_for_load_state(self, state: str = "load", timeout: float | None = None) -> None:
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
        self._sequence = 0

    async def start(self) -> None:
        self.started = True

    async def new_page(self) -> FakePage:
        return self.page

    async def attach_cdp(self, page: FakePage) -> None:
        self.attached.append(page)

    async def stop(self) -> None:
        self.stopped = True

    def make_screenshot_hook(self, experiment_id: str, page: FakePage) -> Any:
        from browser.context_factory import screenshot_filename

        def hook(source: str, target: str, event: str) -> None:
            self._sequence += 1
            self.transitions.append((source, target, event))
            name = screenshot_filename(experiment_id, self._sequence, target)
            (self.screenshot_dir / name).write_bytes(b"\x89PNG\r\n\x1a\n")

        return hook
