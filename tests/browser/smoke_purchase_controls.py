"""Real worker + Chromium against intercepted, local KKTIX fixtures.

Run against an isolated running API, never against a personal database:
  uv run python tests/browser/smoke_purchase_controls.py --root /tmp/auto-ticket-qa-20261008
All browser requests are fulfilled locally. No ticket provider receives traffic.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import httpx

from broker.broker import SqliteTaskBroker
from broker.outbox import OutboxWriter
from browser.context_factory import build_persistent_context_options
from scheduler.clock_sync import ClockSynchronizer
from storage.database import Database
from worker.browser import StreamingPlaywrightManager
from worker.control import ControllableScheduler
from worker.loop import WorkerLoop
from worker.settings import WorkerSettings

PAGES = {}
FIXTURE = """<!doctype html><meta charset="utf-8"><title>Local purchase QA</title>
<div id="registrationsNewApp"><h1>本機購票流程測試（不連售票網站）</h1>
<div class="ticket-list"><div class="ticket-unit"><div class="ticket-name">QA Ticket</div><div class="ticket-price">100</div>
<input class="ticket-quantity" type="number" value="1"></div></div>
{body}</div><button id="qa-complete" onclick="document.body.innerHTML='<div class=order-complete>本機完成</div>'">完成本機步驟</button>"""
BODIES = {
    "verification": '<div class="custom-captcha-inner"><p>QA question</p><input name="captcha_answer" value="QA"></div>',
    "seats": '<button ng-click="challenge(1)">電腦配位</button><button ng-click="challenge()" onclick="this.dataset.clicked=1">自行選位</button>',
    "queue": '<div id="cf-time">排隊中</div>',
}


class FixtureBrowser(StreamingPlaywrightManager):
    async def new_page(self):
        page = await super().new_page()
        PAGES[self._task_id] = page

        async def local_response(route):
            scenario = next(
                (key for key in BODIES if key in route.request.url), "queue"
            )
            await route.fulfill(
                status=200,
                content_type="text/html",
                body=FIXTURE.format(body=BODIES[scenario]),
            )

        await page.route("**/*", local_response)
        return page


class OfflineScheduler(ControllableScheduler):
    def __init__(self, *args, **kwargs):
        super().__init__(
            *args, clock_synchronizer=ClockSynchronizer(ntp_host=None), **kwargs
        )


async def wait_for(fn, timeout=30):
    async with asyncio.timeout(timeout):
        while True:
            result = await fn()
            if result:
                return result
            await asyncio.sleep(0.1)


async def main(root: Path, api: str, interactive: bool):
    root.mkdir(parents=True, exist_ok=True)
    db = Database(root / "qa.db")
    broker = SqliteTaskBroker(db)
    outbox = OutboxWriter(db, flush_ms=20)
    settings = WorkerSettings(
        db_path=db.db_path,
        screenshot_dir=root / "screenshots",
        timeline_dir=root / "timelines",
        vault_root=root / "vault",
        auto_launch_browser=False,
        headless=False,
        clock_tick_hz=100,
        poll_ms=20,
        control_poll_ms=50,
        ocr_enabled=False,
    )
    worker = WorkerLoop(
        db, broker, outbox, worker_id="local-browser-qa", settings=settings
    )
    results = []
    with (
        patch("worker.handlers.purchase.StreamingPlaywrightManager", FixtureBrowser),
        patch("worker.handlers.purchase.ControllableScheduler", OfflineScheduler),
        patch(
            "browser.manager.build_persistent_context_options",
            lambda profile: {
                **build_persistent_context_options(profile),
                "channel": "chrome",
            },
        ),
    ):
        runner = asyncio.create_task(worker.run_forever())
        try:
            async with httpx.AsyncClient(base_url=api) as client:
                for scenario in (
                    "verification",
                    "seats",
                    "queue",
                    "verification_resume",
                    "seats_resume",
                ):
                    response = await client.post(
                        "/api/v1/tasks",
                        json={
                            "event_title": f"QA {scenario}",
                            "event_url": f"https://kktix.com/events/qa-{scenario}/registrations/new",
                            "execution_mode": "mock",
                            "profile": "qa-controls",
                            "ticket_preference": {
                                "quantity": 1,
                                "priorities": [{"price": 100, "priority": 1}],
                                "seat_preference": {
                                    "strategy": "same_zone"
                                    if scenario.startswith("seats")
                                    else "best_available"
                                },
                            },
                            "contact_profile": {
                                "name": "QA",
                                "phone": "0900000000",
                                "email": "qa@example.test",
                            },
                            "timeout_seconds": 600,
                            "auto_cloudflare": False,
                            "auto_ocr": False,
                        },
                    )
                    response.raise_for_status()
                    task_id = response.json()["id"]
                    print(
                        json.dumps({"scenario": scenario, "task_id": task_id}),
                        flush=True,
                    )
                    (root / "current.json").write_text(
                        json.dumps({"scenario": scenario, "task_id": task_id})
                    )

                    async def ready(task_id=task_id, scenario=scenario):
                        jobs = await broker.list_jobs(task_id=task_id)
                        if jobs and jobs[0].error:
                            raise AssertionError(jobs[0].error)
                        from sqlalchemy import select

                        from broker.models import OutboxEventModel

                        async with db.session() as session:
                            rows = (
                                (
                                    await session.execute(
                                        select(OutboxEventModel).where(
                                            OutboxEventModel.task_id == task_id
                                        )
                                    )
                                )
                                .scalars()
                                .all()
                            )
                            if scenario == "queue":
                                return any(
                                    r.payload.get("name") == "race_loop_state"
                                    and r.payload.get("detail", {}).get("state")
                                    == "QUEUE"
                                    for r in rows
                                )
                            return any(
                                r.payload.get("phase") == "waiting_for_human"
                                for r in rows
                            )

                    await wait_for(ready)
                    await asyncio.sleep(
                        1.1
                    )  # More than 50 clock events after the gate.
                    await PAGES[task_id].screenshot(
                        path=str(root / f"{scenario}-fixture.png")
                    )
                    if interactive and scenario == "verification":
                        print("READY_FOR_DASHBOARD_STOP", flush=True)

                        async def stopped(task_id=task_id):
                            return (
                                await client.get(f"/api/v1/tasks/{task_id}")
                            ).json()["task"]["status"] == "CANCELLED"

                        await wait_for(stopped, timeout=180)
                    elif scenario.endswith("_resume"):
                        await PAGES[task_id].locator("#qa-complete").click()
                    else:
                        await client.post(f"/api/v1/tasks/{task_id}/cancel")

                    async def finished(task_id=task_id):
                        detail = (await client.get(f"/api/v1/tasks/{task_id}")).json()
                        return (
                            detail
                            if detail["task"]["status"]
                            in ("CANCELLED", "FAILED", "COMPLETED")
                            else None
                        )

                    detail = await wait_for(finished)
                    expected = (
                        "COMPLETED" if scenario.endswith("_resume") else "CANCELLED"
                    )
                    assert detail["task"]["status"] == expected, detail
                    job = (await broker.list_jobs(task_id=task_id))[0]
                    assert job.state.value == (
                        "DONE" if expected == "COMPLETED" else "CANCELLED"
                    )
                    results.append(
                        {
                            "scenario": scenario,
                            "task_id": task_id,
                            "status": expected,
                        }
                    )
                (root / "browser-results.json").write_text(
                    json.dumps(results, indent=2)
                )
                print(json.dumps(results), flush=True)
        finally:
            worker.stop()
            await runner
            await db.dispose()


if __name__ == "__main__":
    import os

    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--api", default="http://127.0.0.1:18000")
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args()
    os.environ["AUTO_TICKET_BROWSER_PROFILE_ROOT"] = str(args.root / "profiles")
    asyncio.run(main(args.root, args.api, args.interactive))
