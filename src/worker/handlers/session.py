from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Sequence
from enum import Enum
from typing import Any

from accounts.vault import EncryptedFileVault, EnvCredentialSource
from adapters.payment.mock import MockPaymentProvider
from adapters.ticketing.kktix.adapter import (
    LOGIN_HUMAN_VERIFICATION_TEXTS,
    KKTIXAdapter,
    KKTIXPageKind,
)
from api.schemas.ws import ServerMessageType
from broker.broker import SqliteTaskBroker
from broker.jobs import JobKind, JobRecord
from broker.outbox import OutboxWriter
from browser.context_factory import BrowserProfile
from browser.manager import PlaywrightManager
from browser.system_chrome import SystemChromeError, ensure_system_chrome
from storage.database import Database
from telemetry.timeline import TimelineRecorder

from ..control import ControlPoller, ControlState
from ..settings import WorkerSettings

PLATFORM_SESSION_CONFIG = {
    "kktix": {
        "login_url": "https://kktix.com/users/sign_in",
        "check_url": "https://kktix.com/users/sign_in",
        "redirect_url": "https://kktix.com/",
        "host_fragment": "kktix",
        "login_path_fragment": "users/sign_in",
    },
    "tixcraft": {
        "login_url": "https://tixcraft.com/login",
        "check_url": "https://tixcraft.com/user/changePassword",
        "redirect_url": "https://tixcraft.com/",
        "host_fragment": "tixcraft",
        "login_path_fragment": "login",
    },
    "ibon": {
        "login_url": "https://ticket.ibon.com.tw/Account/Login",
        "check_url": "https://ticket.ibon.com.tw/Account/Login",
        "redirect_url": "https://ticket.ibon.com.tw/",
        "host_fragment": "ibon",
        "login_path_fragment": "account/login",
    },
}


def summarize_cookies(
    cookies: list[dict[str, Any]], host_fragment: str = "kktix"
) -> tuple[int, bool]:
    """只回報數量與「是否存在 session 類 cookie」——**絕不印出 cookie 內容**。

    注意：這個布林值**不能當成「已登入」**。cookie 名字帶 session 只代表站方發過
    一個 session，未登入的訪客一樣會拿到；被 Cloudflare 擋住時更是連頁面都沒看到。
    真正的登入判定要看登入後才拿得到的畫面，見 `probe_login_state`。
    """
    relevant = [c for c in cookies if host_fragment in str(c.get("domain", ""))]
    has_session = any(
        "session" in str(c.get("name", "")).lower()
        or "token" in str(c.get("name", "")).lower()
        for c in relevant
    )
    return len(relevant), has_session


class LoginState(str, Enum):
    """登入狀態的三種可能，刻意不把「不知道」摺進「未登入」。"""

    LOGGED_IN = "LOGGED_IN"
    LOGGED_OUT = "LOGGED_OUT"
    UNKNOWN = "UNKNOWN"


async def probe_login_state(adapter: Any, page: Any) -> LoginState:
    """以頁面實際判定登入與否。

    被人機驗證擋住時一律回 UNKNOWN——那時候我們根本沒看到 KKTIX 的頁面，
    宣稱「已登入」是在編造事實。
    """
    kind = await adapter.probe_page(page, page.url)
    if kind is KKTIXPageKind.CHALLENGE:
        return LoginState.UNKNOWN
    if kind is KKTIXPageKind.LOGIN:
        return LoginState.LOGGED_OUT
    return LoginState.LOGGED_IN


async def _login_needs_human_verification(page: Any) -> bool:
    """登入頁是不是在要求人工驗證。

    出現這個字樣代表帳密根本沒被受理，把它回報成「帳號或密碼可能有誤」是誤導。
    """
    try:
        text = await page.inner_text("body")
    except Exception:
        return False
    lowered = str(text).lower()
    return any(marker.lower() in lowered for marker in LOGIN_HUMAN_VERIFICATION_TEXTS)


async def _ensure_browser_endpoint(
    settings: WorkerSettings,
    initial_url: str,
    fallback_urls: Sequence[str] | None = None,
) -> str | None:
    """拿到可 attach 的真 Chrome 端點；拿不到就回 None，由呼叫端自行降級。"""
    if settings.cdp_endpoint is not None:
        return settings.cdp_endpoint
    if not settings.auto_launch_browser:
        return None
    try:
        chrome = await ensure_system_chrome(
            port=settings.browser_debug_port,
            initial_url=initial_url,
            fallback_urls=fallback_urls,
        )
    except SystemChromeError:
        return None
    return chrome.endpoint


async def execute_session_check(
    job: JobRecord,
    *,
    worker_id: str,
    db: Database,
    broker: SqliteTaskBroker,
    outbox: OutboxWriter,
    settings: WorkerSettings,
) -> None:
    await broker.mark_running(job.id, worker_id=worker_id)
    platform = str(job.payload.get("platform", "kktix")).lower()
    cfg = PLATFORM_SESSION_CONFIG.get(platform, PLATFORM_SESSION_CONFIG["kktix"])
    target_url = cfg["check_url"]
    redirect_url = cfg.get("redirect_url")
    fallback_urls = [redirect_url] if redirect_url else None
    host_fragment = cfg["host_fragment"]

    telemetry = TimelineRecorder()
    # 登入檢查一定要用使用者本機那顆真 Chrome：Playwright 自帶的會被人機驗證擋在
    # 門外，於是永遠只能回報 CHALLENGE，檢查等於沒做。
    cdp_endpoint = await _ensure_browser_endpoint(
        settings, target_url, fallback_urls=fallback_urls
    )
    browser = PlaywrightManager(
        BrowserProfile(name=job.profile, headless=settings.headless),
        telemetry,
        screenshot_dir=settings.screenshot_dir,
        cdp_endpoint=cdp_endpoint,
        cdp_page_url=target_url if cdp_endpoint else None,
        cdp_page_fallback_urls=fallback_urls if cdp_endpoint else None,
    )
    await browser.start()
    try:
        page = await browser.new_page()
        with contextlib.suppress(Exception):
            await page.goto(target_url, wait_until="domcontentloaded", timeout=15000)

        cookies = await page.context.cookies()
        cookie_count, has_session = summarize_cookies(
            cookies, host_fragment=host_fragment
        )

        if platform == "kktix":
            adapter = KKTIXAdapter(telemetry=telemetry, payment=MockPaymentProvider())
            probe = await adapter.probe_page(page, page.url)
            login_state = await probe_login_state(adapter, page)
            page_kind = probe.name
        else:
            is_login_page = cfg["login_path_fragment"] in str(page.url).lower()
            if has_session and not is_login_page:
                login_state = LoginState.LOGGED_IN
                page_kind = "NORMAL"
            elif is_login_page:
                login_state = LoginState.LOGGED_OUT
                page_kind = "LOGIN"
            else:
                login_state = LoginState.UNKNOWN
                page_kind = "UNKNOWN"

        result = {
            "platform": platform,
            "page_kind": page_kind,
            "cookie_count": cookie_count,
            "has_session": has_session,
            "login_state": login_state.value,
        }
        await broker.complete(job.id, worker_id=worker_id, result=result)
    except Exception as exc:
        await broker.fail(job.id, worker_id=worker_id, error=str(exc), retry=False)
    finally:
        await browser.stop()


async def execute_auto_login(
    job: JobRecord,
    *,
    worker_id: str,
    db: Database,
    broker: SqliteTaskBroker,
    outbox: OutboxWriter,
    settings: WorkerSettings,
) -> None:
    await broker.mark_running(job.id, worker_id=worker_id)
    platform = str(job.payload.get("platform", "kktix")).lower()

    if platform == "tixcraft":
        await broker.fail(
            job.id,
            worker_id=worker_id,
            error="tixcraft_requires_manual_login",
            retry=False,
        )
        return

    if platform == "ibon":
        await broker.fail(
            job.id,
            worker_id=worker_id,
            error="ibon_requires_manual_login",
            retry=False,
        )
        return

    vault = EncryptedFileVault.from_env(settings.vault_root)
    pair = None
    if vault is not None and vault.available(platform):
        pair = vault.load(platform)
    if not pair:
        pair = EnvCredentialSource().load(platform)

    if not pair:
        await broker.fail(
            job.id,
            worker_id=worker_id,
            error="no_credentials_configured",
            retry=False,
        )
        return

    account, access_key = pair

    telemetry = TimelineRecorder()
    # 登入表單在 Cloudflare 後面。用 Playwright 自帶的瀏覽器連登入頁都看不到，
    # 於是 login() 必然回 False，再被回報成「帳號或密碼可能有誤」——那是誤導。
    cfg = PLATFORM_SESSION_CONFIG.get(platform, PLATFORM_SESSION_CONFIG["kktix"])
    login_url = cfg["login_url"]
    redirect_url = cfg.get("redirect_url")
    fallback_urls = [redirect_url] if redirect_url else None

    cdp_endpoint = await _ensure_browser_endpoint(
        settings, login_url, fallback_urls=fallback_urls
    )
    browser = PlaywrightManager(
        BrowserProfile(name=job.profile, headless=settings.headless),
        telemetry,
        screenshot_dir=settings.screenshot_dir,
        cdp_endpoint=cdp_endpoint,
        cdp_page_url=login_url if cdp_endpoint else None,
        cdp_page_fallback_urls=fallback_urls if cdp_endpoint else None,
    )
    await browser.start()
    try:
        page = await browser.new_page()
        adapter = KKTIXAdapter(telemetry=telemetry, payment=MockPaymentProvider())

        # 先確認我們真的看得到 KKTIX 的頁面；被擋住時不要去猜帳密對不對。
        probe_before = await adapter.probe_page(page, login_url)
        if probe_before is KKTIXPageKind.CHALLENGE:
            await broker.fail(
                job.id,
                worker_id=worker_id,
                error="blocked_by_bot_check",
                retry=False,
            )
            return

        # 若已是登入狀態（例如訪問 login_url 後因已登入被 302 轉址回首頁），不需重複填表
        login_state_before = await probe_login_state(adapter, page)
        if login_state_before is LoginState.LOGGED_IN:
            cookies = await page.context.cookies()
            cookie_count, has_session = summarize_cookies(cookies)
            result = {
                "success": True,
                "page_kind": probe_before.name,
                "cookie_count": cookie_count,
                "has_session": has_session,
                "login_state": login_state_before.value,
            }
            await broker.complete(job.id, worker_id=worker_id, result=result)
            return

        await adapter.login(page, account, access_key)

        probe = await adapter.probe_page(page, page.url)
        login_state = await probe_login_state(adapter, page)
        # `adapter.login()` 只檢查「有沒有離開登入頁」，而人機驗證頁同樣不是登入頁，
        # 於是被誤判成成功。成功與否一律以實際登入狀態為準。
        success = login_state is LoginState.LOGGED_IN
        needs_human = await _login_needs_human_verification(page)
        cookies = await page.context.cookies()
        cookie_count, has_session = summarize_cookies(cookies)

        result = {
            "success": success,
            "page_kind": probe.name,
            "cookie_count": cookie_count,
            "has_session": has_session,
            "login_state": login_state.value,
        }
        if success:
            await broker.complete(job.id, worker_id=worker_id, result=result)
        else:
            if probe is KKTIXPageKind.CHALLENGE:
                reason = "blocked_by_bot_check"
            elif needs_human:
                reason = "login_requires_human_verification"
            else:
                reason = "auto_login_failed"
            await broker.fail(job.id, worker_id=worker_id, error=reason, retry=False)
    except Exception as exc:
        await broker.fail(job.id, worker_id=worker_id, error=str(exc), retry=False)
    finally:
        await browser.stop()


async def execute_manual_login(
    job: JobRecord,
    *,
    worker_id: str,
    db: Database,
    broker: SqliteTaskBroker,
    outbox: OutboxWriter,
    settings: WorkerSettings,
) -> None:
    await broker.mark_running(job.id, worker_id=worker_id)
    task_id = job.task_id or job.id

    platform = str(job.payload.get("platform", "kktix")).lower()
    cfg = PLATFORM_SESSION_CONFIG.get(platform, PLATFORM_SESSION_CONFIG["kktix"])
    login_url = cfg["login_url"]
    redirect_url = cfg.get("redirect_url")
    fallback_urls = [redirect_url] if redirect_url else None
    host_fragment = cfg["host_fragment"]

    telemetry = TimelineRecorder()
    # 手動登入是「把畫面交給人」，所以更要用那顆過得了人機驗證的真 Chrome；
    # Playwright 自帶的開了視窗也只會停在驗證頁，人一樣登不進去。
    cdp_endpoint = await _ensure_browser_endpoint(
        settings, login_url, fallback_urls=fallback_urls
    )
    browser = PlaywrightManager(
        BrowserProfile(name=job.profile, headless=False),
        telemetry,
        screenshot_dir=settings.screenshot_dir,
        cdp_endpoint=cdp_endpoint,
        cdp_page_url=login_url if cdp_endpoint else None,
        cdp_page_fallback_urls=fallback_urls if cdp_endpoint else None,
    )
    await browser.start()

    control_state = ControlState()
    poller = ControlPoller(
        db,
        task_id,
        control_state,
        outbox=outbox,
        poll_interval_s=max(0.05, settings.control_poll_ms / 1000.0),
    )
    poller_task = asyncio.create_task(poller.run())

    try:
        page = await browser.new_page()
        with contextlib.suppress(Exception):
            await page.goto(login_url, wait_until="domcontentloaded", timeout=15000)

        adapter = (
            KKTIXAdapter(telemetry=telemetry, payment=MockPaymentProvider())
            if platform == "kktix"
            else None
        )
        start_time = time.monotonic()
        last_notice = 0.0

        while True:
            if control_state._aborted.is_set():
                await broker.fail(
                    job.id, worker_id=worker_id, error="cancelled", retry=False
                )
                return

            elapsed = time.monotonic() - start_time
            if elapsed >= settings.manual_login_timeout_s:
                await broker.fail(
                    job.id,
                    worker_id=worker_id,
                    error="manual_login_timeout",
                    retry=False,
                )
                return

            if elapsed - last_notice >= settings.manual_login_notice_s:
                outbox.publish(
                    task_id=task_id,
                    type=ServerMessageType.TASK_LOG.value,
                    payload={
                        "phase": "awaiting_manual_login",
                        "elapsed_s": round(elapsed, 1),
                    },
                    ephemeral=False,
                )
                last_notice = elapsed

            cookies = await page.context.cookies()
            cookie_count, has_session = summarize_cookies(
                cookies, host_fragment=host_fragment
            )

            if platform == "kktix" and adapter is not None:
                probe = await adapter.probe_page(page, page.url)
                if has_session:
                    result = {
                        "platform": platform,
                        "page_kind": probe.name,
                        "cookie_count": cookie_count,
                        "has_session": True,
                    }
                    await broker.complete(job.id, worker_id=worker_id, result=result)
                    return
            else:
                is_login_page = cfg["login_path_fragment"] in str(page.url).lower()
                if has_session and not is_login_page:
                    result = {
                        "platform": platform,
                        "page_kind": "NORMAL",
                        "cookie_count": cookie_count,
                        "has_session": True,
                    }
                    await broker.complete(job.id, worker_id=worker_id, result=result)
                    return

            await asyncio.sleep(1.0)

    except Exception as exc:
        await broker.fail(job.id, worker_id=worker_id, error=str(exc), retry=False)
    finally:
        poller.stop()
        poller_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await poller_task
        await browser.stop()


async def execute_session_job(
    job: JobRecord,
    *,
    worker_id: str,
    db: Database,
    broker: SqliteTaskBroker,
    outbox: OutboxWriter,
    settings: WorkerSettings,
) -> None:
    if job.kind == JobKind.SESSION_CHECK:
        await execute_session_check(
            job,
            worker_id=worker_id,
            db=db,
            broker=broker,
            outbox=outbox,
            settings=settings,
        )
    elif job.kind == JobKind.AUTO_LOGIN:
        await execute_auto_login(
            job,
            worker_id=worker_id,
            db=db,
            broker=broker,
            outbox=outbox,
            settings=settings,
        )
    elif job.kind == JobKind.MANUAL_LOGIN:
        await execute_manual_login(
            job,
            worker_id=worker_id,
            db=db,
            broker=broker,
            outbox=outbox,
            settings=settings,
        )
    else:
        await broker.fail(
            job.id,
            worker_id=worker_id,
            error=f"unsupported_session_job_kind_{job.kind.value}",
            retry=False,
        )
