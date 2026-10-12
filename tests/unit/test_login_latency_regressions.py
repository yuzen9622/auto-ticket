"""Regression seams for login detection and avoidable DOM waits (offline)."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from adapters.ticketing.dom import first_visible
from adapters.ticketing.page_state import LoginState
from adapters.ticketing.tixcraft.adapter import TixcraftAdapter
from adapters.ticketing.tixcraft.pages import parse_login_state
from tests.netguard import netguard_autouse  # noqa: F401
from worker.handlers.session import execute_session_check, probe_login_state


@pytest.mark.asyncio
@pytest.mark.parametrize("html", [
    '<a href="/login">會員登入</a>',
    '<script>const logoutUrl = "/logout";</script><a href="/login">會員登入</a>',
    '<p>歡迎光臨</p>',
])
async def test_tixcraft_stale_cookie_is_not_login_proof(html):
    cookies = [{"domain": "tixcraft.com", "name": "TIXUISID", "value": "stale"}]
    page: Any = SimpleNamespace(
        url="https://tixcraft.com/",
        content=AsyncMock(return_value=html),
        context=SimpleNamespace(cookies=AsyncMock(return_value=cookies)),
    )
    adapter = TixcraftAdapter()
    assert await adapter.probe_login_state(page) is not LoginState.LOGGED_IN
    assert await probe_login_state(adapter, page, cookies, "tixcraft") is not LoginState.LOGGED_IN


@pytest.mark.parametrize(("html", "expected"), [
    ('<a href="/user/logout">登出</a>', LoginState.LOGGED_IN),
    ('<form action="/logout"><button>Logout</button></form>', LoginState.LOGGED_IN),
    ('<button>會員登入</button>', LoginState.LOGGED_OUT),
    ('<a href="/user/logout">登出</a><a href="/login">會員登入</a>', LoginState.LOGGED_OUT),
    ('<script>const logout = "/logout"</script>', LoginState.UNKNOWN),
    ('<p>登入後可以按登出</p>', LoginState.UNKNOWN),
    ('<template><a href="/logout">登出</a></template>', LoginState.UNKNOWN),
    ('<div hidden><a href="/logout">登出</a></div>', LoginState.UNKNOWN),
    ('<div style="display: none"><a href="/logout">登出</a></div>', LoginState.UNKNOWN),
    ('<a href="https://other.example/logout">登出</a>', LoginState.UNKNOWN),
])
def test_tixcraft_login_requires_an_actual_control(html, expected):
    assert parse_login_state(html) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(("url", "html", "expected"), [
    ("https://tixcraft.com/login", '<a href="/logout">登出</a>', LoginState.LOGGED_OUT),
    ("https://tixcraft.com/", 'Just a moment <a href="/logout">登出</a>', LoginState.UNKNOWN),
    ("https://other.example/", '<a href="/logout">登出</a>', LoginState.UNKNOWN),
])
async def test_tixcraft_login_page_challenge_and_origin_override_markers(url, html, expected):
    page: Any = SimpleNamespace(url=url, content=AsyncMock(return_value=html))
    assert await TixcraftAdapter().probe_login_state(page) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(("html", "expected"), [
    ('<a href="/login">會員登入</a>', "LOGGED_OUT"),
    ('<a href="/user/logout">登出</a>', "LOGGED_IN"),
    ('<p>歡迎光臨</p>', "UNKNOWN"),
])
async def test_session_check_uses_real_adapter_not_cookie_presence(monkeypatch, html, expected):
    cookies = [{"domain": "tixcraft.com", "name": "TIXUISID", "value": "stale"}]
    page = SimpleNamespace(
        url="https://tixcraft.com/", content=AsyncMock(return_value=html),
        goto=AsyncMock(), context=SimpleNamespace(cookies=AsyncMock(return_value=cookies)),
        locator=lambda selector: SimpleNamespace(count=AsyncMock(return_value=0)),
    )
    browser = SimpleNamespace(start=AsyncMock(), stop=AsyncMock(), new_page=AsyncMock(return_value=page))
    broker: Any = SimpleNamespace(mark_running=AsyncMock(), complete=AsyncMock(), fail=AsyncMock())
    monkeypatch.setattr("worker.handlers.session.PlaywrightManager", lambda *args, **kwargs: browser)
    monkeypatch.setattr("worker.handlers.session._ensure_browser_endpoint", AsyncMock(return_value=None))
    monkeypatch.setattr("worker.handlers.session.EncryptedFileVault.from_env", lambda root: None)
    monkeypatch.setattr("worker.handlers.session.EnvCredentialSource.load", lambda *args: None)
    settings: Any = SimpleNamespace(vault_root=None, headless=True, screenshot_dir=None)
    job: Any = SimpleNamespace(id="offline-check", profile="test", payload={"platform": "tixcraft"})
    await execute_session_check(job, worker_id="test", db=MagicMock(), broker=broker, outbox=MagicMock(), settings=settings)
    broker.fail.assert_not_awaited()
    result = broker.complete.call_args.kwargs["result"]
    assert result["login_state"] == expected
    assert result["has_session"] is (expected == "LOGGED_IN")
    assert result["page_kind"] == ("LOGGED_IN" if expected == "LOGGED_IN" else "LOGGED_OUT")
    browser.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_ready_fallback_does_not_wait_for_missing_primary():
    waits = []

    class Locator:
        def __init__(self, visible):
            self.visible = visible
            self.first = self

        async def is_visible(self):
            return self.visible

        async def wait_for(self, *, state, timeout):
            if not self.visible:
                waits.append(timeout)
                raise TimeoutError

    missing, ready = Locator(False), Locator(True)
    page: Any = SimpleNamespace(locator=lambda selector: {"#old": missing, "#ready": ready}[selector])
    assert await first_visible(page, ["#old", "#ready"], timeout_ms=5000) is ready
    assert waits == [], f"Already visible field paid unnecessary wait budgets: {waits}"
