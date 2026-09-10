"""`scripts/run_purchase.py` 的 CLI 契約：預設值、金流閘門、CDP 選項早期驗證。"""

from __future__ import annotations

import json
import sys
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlunsplit

import pytest

from tests.netguard import netguard_autouse  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

run_purchase = import_module("run_purchase")

CDP_ENDPOINT = "http://127.0.0.1:9222"
EVENT_URL = "https://example.com/events/1"
# 非 http(s) scheme 的 URL 只能組出來（G2 只放行 loopback 與保留網域字面值）。
CHROME_NEWTAB = urlunsplit(("chrome", "newtab", "", "", ""))


def _task_file(tmp_path: Path) -> Path:
    path = tmp_path / "task.json"
    path.write_text(
        json.dumps(
            {
                "task_id": "t1",
                "event_title": "Demo",
                "event_url": EVENT_URL,
                "sale_start_at": "2030-01-01T04:00:00+00:00",
                "ticket_preference": {
                    "quantity": 1,
                    "priorities": [{"price": 100, "priority": 1}],
                    "seat_preference": {},
                },
                "contact_profile": {
                    "name": "n",
                    "phone": "0912345678",
                    "email": "a@b.co",
                },
                "payment_method": "mock",
            }
        ),
        encoding="utf-8",
    )
    return path


def test_cdp_options_default_to_off() -> None:
    args = run_purchase.build_parser().parse_args(["--task", "t.json"])
    assert args.cdp_endpoint is None
    assert args.cdp_page_url is None


def test_payment_gates_keep_their_defaults() -> None:
    """金流閘門回歸保護：CDP 選項的加入不得改動任何既有預設值。"""
    args = run_purchase.build_parser().parse_args(["--task", "t.json"])
    assert args.dry_run is True
    assert args.real_payment is False
    assert args.headless is True
    assert args.session_gate_timeout == 240.0
    assert args.profile is None


def test_cdp_options_are_parsed() -> None:
    args = run_purchase.build_parser().parse_args(
        [
            "--task",
            "t.json",
            "--cdp-endpoint",
            CDP_ENDPOINT,
            "--cdp-page-url",
            EVENT_URL,
        ]
    )
    assert args.cdp_endpoint == CDP_ENDPOINT
    assert args.cdp_page_url == EVENT_URL


def test_help_states_the_manual_preconditions() -> None:
    epilog = run_purchase.build_parser().epilog or ""
    assert "--user-data-dir" in epilog
    assert "不要開無痕視窗" in epilog
    assert "--remote-debugging-port=9222" in epilog


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://127.0.0.1:9222",
        "http://example.com:9222",
        "http://127.0.0.1",
        "not-a-url",
    ],
)
async def test_bad_cdp_endpoint_aborts_before_any_browser(
    tmp_path: Path,
    endpoint: str,
) -> None:
    args = run_purchase.build_parser().parse_args(
        ["--task", str(_task_file(tmp_path)), "--cdp-endpoint", endpoint]
    )
    assert await run_purchase.run(args) == 2


async def test_bad_cdp_page_url_aborts_before_any_browser(tmp_path: Path) -> None:
    args = run_purchase.build_parser().parse_args(
        [
            "--task",
            str(_task_file(tmp_path)),
            "--cdp-endpoint",
            CDP_ENDPOINT,
            "--cdp-page-url",
            CHROME_NEWTAB,
        ]
    )
    assert await run_purchase.run(args) == 2


async def test_conflicting_payment_flags_still_abort(tmp_path: Path) -> None:
    args = run_purchase.build_parser().parse_args(
        ["--task", str(_task_file(tmp_path)), "--real-payment"]
    )
    assert await run_purchase.run(args) == 2


async def test_browser_stops_even_when_scheduler_shutdown_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stopped = 0

    class FakeBrowser:
        def __init__(
            self, profile: object, telemetry: object, **kwargs: object
        ) -> None:
            self.profile = profile

        async def stop(self) -> None:
            nonlocal stopped
            stopped += 1

    class FailingScheduler:
        def __init__(self, telemetry: object) -> None:
            pass

        async def shutdown(self) -> None:
            raise RuntimeError("scheduler cleanup failed")

    class FakeOrchestrator:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def run(self) -> object:
            return SimpleNamespace()

    monkeypatch.setattr(run_purchase, "PlaywrightManager", FakeBrowser)
    monkeypatch.setattr(run_purchase, "WarmupScheduler", FailingScheduler)
    monkeypatch.setattr(run_purchase, "PurchaseOrchestrator", FakeOrchestrator)
    monkeypatch.setattr(run_purchase, "KKTIXAdapter", lambda **kwargs: object())

    args = run_purchase.build_parser().parse_args(["--task", str(_task_file(tmp_path))])
    with pytest.raises(RuntimeError, match="scheduler cleanup failed"):
        await run_purchase.run(args)
    assert stopped == 1
