from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from accounts.vault import EncryptedFileVault, EnvCredentialSource
from adapters.payment.mock import MockPaymentProvider
from adapters.ticketing.kktix.adapter import KKTIXAdapter
from api.schemas.ws import ServerMessageType
from broker.broker import SqliteTaskBroker
from broker.jobs import JobKind, JobRecord
from broker.outbox import OutboxWriter
from browser.context_factory import BrowserProfile
from browser.manager import PlaywrightManager
from storage.database import Database
from telemetry.timeline import TimelineRecorder

from ..control import ControlPoller, ControlState
from ..settings import WorkerSettings


def summarize_cookies(
    cookies: list[dict[str, Any]], host_fragment: str = "kktix"
) -> tuple[int, bool]:
    """只回報數量與「是否看起來有 session」——**絕不印出 cookie 內容**。"""
    relevant = [c for c in cookies if host_fragment in str(c.get("domain", ""))]
    has_session = any(
        "session" in str(c.get("name", "")).lower()
        or "token" in str(c.get("name", "")).lower()
        for c in relevant
    )
    return len(relevant), has_session


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
    telemetry = TimelineRecorder()
    browser = PlaywrightManager(
        BrowserProfile(name=job.profile, headless=True),
        telemetry,
        screenshot_dir=settings.screenshot_dir,
    )
    await browser.start()
    try:
        page = await browser.new_page()
        target_url = "https://kktix.com"
        with contextlib.suppress(Exception):
            await page.goto(target_url, wait_until="domcontentloaded", timeout=15000)

        adapter = KKTIXAdapter(telemetry=telemetry, payment=MockPaymentProvider())
        probe = await adapter.probe_page(page, page.url)
        cookies = await page.context.cookies()
        cookie_count, has_session = summarize_cookies(cookies)

        result = {
            "page_kind": probe.name,
            "cookie_count": cookie_count,
            "has_session": has_session,
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
    browser = PlaywrightManager(
        BrowserProfile(name=job.profile, headless=True),
        telemetry,
        screenshot_dir=settings.screenshot_dir,
    )
    await browser.start()
    try:
        page = await browser.new_page()
        adapter = KKTIXAdapter(telemetry=telemetry, payment=MockPaymentProvider())
        success = await adapter.login(page, account, access_key)

        probe = await adapter.probe_page(page, page.url)
        cookies = await page.context.cookies()
        cookie_count, has_session = summarize_cookies(cookies)

        result = {
            "success": success,
            "page_kind": probe.name,
            "cookie_count": cookie_count,
            "has_session": has_session,
        }
        if success:
            await broker.complete(job.id, worker_id=worker_id, result=result)
        else:
            await broker.fail(
                job.id, worker_id=worker_id, error="auto_login_failed", retry=False
            )
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

    telemetry = TimelineRecorder()
    browser = PlaywrightManager(
        BrowserProfile(name=job.profile, headless=False),
        telemetry,
        screenshot_dir=settings.screenshot_dir,
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
        target_url = "https://kktix.com/users/sign_in"
        with contextlib.suppress(Exception):
            await page.goto(target_url, wait_until="domcontentloaded", timeout=15000)

        adapter = KKTIXAdapter(telemetry=telemetry, payment=MockPaymentProvider())
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
            _, has_session = summarize_cookies(cookies)
            probe = await adapter.probe_page(page, page.url)

            if has_session:
                cookie_count, _ = summarize_cookies(cookies)
                result = {
                    "page_kind": probe.name,
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
