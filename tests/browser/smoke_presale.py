"""Real Chrome + real adapters/orchestrator. All provider HTTP is fulfilled locally.

.venv/bin/python tests/browser/smoke_presale.py --output /tmp/presale-qa
Navigation, session resolution, state detection and ticket selection are production code.
The only workflow override stops immediately AFTER selection; no order is submitted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from playwright.async_api import async_playwright

from adapters.payment import MockPaymentProvider
from adapters.ticketing.ibon.adapter import IbonAdapter
from adapters.ticketing.ibon.resolver import IbonEventResolver
from adapters.ticketing.kktix.adapter import KKTIXAdapter
from adapters.ticketing.tixcraft.adapter import TixcraftAdapter
from domain.preference import TicketPreference
from domain.task import PurchaseTaskSpec, UserContactProfile
from fsm.machine import PurchaseWorkflow
from purchase.orchestrator import PurchaseOrchestrator
from telemetry.timeline import TimelineRecorder

FIXTURES = Path(__file__).parents[1] / "fixtures"
URLS = {
    "kktix": "https://kktix.com/events/qa",
    "tixcraft": "https://tixcraft.com/activity/detail/qa",
    "ibon": "https://ticket.ibon.com.tw/ActivityInfo/Details/123",
}
REGISTRATION = {
    "kktix": URLS["kktix"] + "/registrations/new",
    "tixcraft": "https://tixcraft.com/ticket/area/qa/B",
    "ibon": "https://orders.ibon.com.tw/application/UTK02/UTK0201_000.aspx?PERFORMANCE_ID=B",
}


def kktix_html(opened: bool, mode: str):
    button = '<button class="btn-default plus" onclick="document.querySelector(\'input.ticket-quantity\').value++">+</button>'
    if not opened:
        button = (
            button.replace("onclick=", "disabled onclick=")
            if mode == "disabled"
            else ""
        )
    return f"""<!doctype html><title>Local KKTIX QA</title><h1>本機 KKTIX 選票</h1>
    <div id="registrationsNewApp"><div class="ticket-list"><div class="ticket-unit">
    <span class="ticket-name">QA ticket</span><span class="ticket-price">100</span>
    <input type="number" class="ticket-quantity" value="0">{button}
    </div></div><input id="person_agree_terms" type="checkbox"></div>"""


def tix_html(opened: bool):
    # A is already open. B has the identical name and becomes buyable later.
    return (
        '<h1>本機拓元場次</h1><div id="gameList"><table><tbody>'
        + "".join(
            f'<tr data-key="{key}"><td>2027/05/0{n}</td><td>同名演出</td><td>QA</td><td>'
            + (
                f'<button data-href="https://tixcraft.com/ticket/area/qa/{key}">立即訂購</button>'
                if key == "A" or opened
                else "尚未開賣"
            )
            + "</td></tr>"
            for n, key in enumerate(["A", "B"], 1)
        )
        + "</tbody></table></div>"
    )


class SelectionCheckpoint(PurchaseOrchestrator):
    selected = False

    async def _handle_ticket_selection(self, page):
        self.selected = await super()._handle_ticket_selection(page)
        assert self.selected, "must reach actual selection, not SOLD_OUT"
        return False


async def scenario(browser, output: Path, platform: str, delay: float, mode: str):
    name = f"{platform}-{mode}-{delay}"
    context = await browser.new_context()
    page = await context.new_page()
    requests = []
    opened_at = float("inf")

    def opened():
        return time.monotonic() >= opened_at

    def api_response(request):
        assert request.url.path == "/api/ActivityInfo/GetGameInfoList", request.url
        rows = []
        for day, key in enumerate(["A", "B"], 1):
            ready = key == "A" or opened()
            rows.append(
                dict(
                    GameInfoName="同名演出",
                    ShowSaleDate=f"2027/05/0{day}(日) 19:00",
                    VenueRegion="QA",
                    CanBuy=ready,
                    SoldOut=False,
                    Href=REGISTRATION["ibon"].replace("=B", f"={key}")
                    if ready
                    else None,
                    StartDT="2027-01-01T12:00:00",
                    NowDT="2026-10-08T12:00:00",
                )
            )
        return httpx.Response(200, json={"Item": {"GIHtmls": rows}})

    async def route_request(route):
        url = route.request.url
        requests.append(url)
        assert route.request.method == "GET", "No browser POST is permitted"
        if "queue-it.net" in url:
            body = '<div id="waiting-room">本機排隊頁</div>'
        elif platform == "kktix":
            if mode == "loading" and not opened():
                body = '<h1 class="event-title">載入中</h1><div id="registrationsNewApp">{{\'new.i_read_and_agree_to\'}}</div>'
            else:
                body = kktix_html(opened(), mode)
        elif platform == "tixcraft":
            if "/ticket/area/" in url:
                assert url.endswith("/B"), "selected wrong performance"
                body = '<ul class="area-list"><li><a href="/ticket/ticket/qa/B">QA 100 熱賣中</a></li></ul>'
            elif "/ticket/ticket/" in url:
                body = "<h1>本機已選 B 場票區</h1>"
            else:
                body = tix_html(opened())
        else:
            if "UTK0201_000" in url:
                assert "PERFORMANCE_ID=B" in url, "selected wrong performance"
                body = (
                    (FIXTURES / "ibon_utk0201_000.html")
                    .read_text()
                    .replace("尚餘：0", "尚餘：10")
                    if opened()
                    else "<h1>尚未開放</h1>"
                )
            elif "UTK0205_" in url:
                body = "<h1>本機已選 B 場票區</h1>"
            else:
                body = "<h1>本機 ibon 活動頁</h1><button>購買 A 場</button>"
        await route.fulfill(status=200, content_type="text/html", body=body)

    await context.route("**/*", route_request)
    telemetry = TimelineRecorder()
    adapter = {"kktix": KKTIXAdapter, "tixcraft": TixcraftAdapter, "ibon": IbonAdapter}[
        platform
    ](
        telemetry=telemetry,
        payment=MockPaymentProvider(),
        timeout_ms=300,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(api_response)) as client:
        # Replace HTTP transport only. fetch_sessions and all navigation stay real.
        with patch.object(IbonEventResolver, "_get_client", lambda self: client):
            preference = None
            if platform == "tixcraft":
                preference = "id:B"
            if platform == "ibon":
                sessions = await IbonEventResolver().fetch_sessions("123")
                preference = "id:" + sessions[1]["key"]
            event_url = (
                REGISTRATION[platform] if mode == "empty-zone" else URLS[platform]
            )
            spec = PurchaseTaskSpec(
                task_id=name,
                event_title=name,
                event_url=event_url,
                sale_start_at=datetime.now(UTC) + timedelta(minutes=5),
                ticket_preference=TicketPreference(quantity=1, fallback_to_any=True),
                contact_profile=UserContactProfile(
                    name="Local QA", phone="0900000000", email="qa@example.test"
                ),
                session_preference=preference,
                timeout_seconds=30,
                payment_method="mock",
            )
            orchestrator = SelectionCheckpoint(
                spec,
                browser=SimpleNamespace(),
                scheduler=SimpleNamespace(),
                adapter=adapter,
                telemetry=telemetry,
                detect_timeout_ms=50,
                sale_advance_retry_s=0.25,
                micro_wait_s=0.05,
                queue_poll_s=0.05,
            )
            orchestrator._rt.page = page
            orchestrator.fsm = PurchaseWorkflow(spec, telemetry)
            orchestrator.fsm.send("prepare_session")
            if mode == "queue":
                await page.goto("https://qa.queue-it.net/wait")
            await orchestrator._check_session(None)
            await orchestrator._navigate_page(None)
            await orchestrator._enter_ready(None)
            orchestrator.fsm.send("session_ready")
            orchestrator.fsm.send("sale_triggered")
            if mode == "queue":
                assert requests == ["https://qa.queue-it.net/wait"], requests
            await page.screenshot(path=str(output / f"{name}-standby.png"))
            opened_at = time.monotonic() + delay
            start = time.monotonic()
            trigger = asyncio.create_task(orchestrator._trigger_purchase(None))
            if mode == "queue":
                await asyncio.sleep(max(delay, 0.2))
                assert requests == ["https://qa.queue-it.net/wait"], requests
                # The site's own script leaves the queue. No adapter goto/reload.
                await page.evaluate(
                    "url => location.href = url", REGISTRATION[platform]
                )
            if mode == "loading":
                await asyncio.sleep(max(delay, 0.1))
                assert requests == [REGISTRATION[platform]], requests
                # Angular rendering mutates DOM on the existing document.
                await page.evaluate(
                    "html => document.body.innerHTML = html", kktix_html(True, mode)
                )
            await trigger
            assert orchestrator.selected
            if platform == "kktix":
                assert await page.locator("input.ticket-quantity").input_value() == "1"
            await page.screenshot(path=str(output / f"{name}-selected.png"))
            result = dict(
                scenario=name,
                selected=True,
                elapsed_s=round(time.monotonic() - start, 3),
                requests=requests,
            )
            print(json.dumps(result, ensure_ascii=False), flush=True)
    await context.close()
    return result


async def main(output):
    output.mkdir(parents=True, exist_ok=True)
    results = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=False)
        try:
            for platform in URLS:
                for delay in (0, 0.5, 2, 5):
                    results.append(
                        await scenario(browser, output, platform, delay, "refresh")
                    )
                results.append(await scenario(browser, output, platform, 0.5, "queue"))
            results.append(await scenario(browser, output, "kktix", 0.5, "disabled"))
            results.append(await scenario(browser, output, "kktix", 0.5, "loading"))
            results.append(await scenario(browser, output, "ibon", 0.5, "empty-zone"))
        finally:
            await browser.close()
            (output / "results.json").write_text(
                json.dumps(results, ensure_ascii=False, indent=2)
            )
    assert len(results) == 18


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(main(args.output))
