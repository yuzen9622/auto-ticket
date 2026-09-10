#!/usr/bin/env python
"""購票流程可執行入口。

**預設 `--dry-run`**：使用 `MockPaymentProvider`，在送出付款鈕之前停止，絕不發動金流。
真實刷卡必須同時 `--real-payment` 與環境變數 `AUTO_TICKET_ENABLE_REAL_PAYMENT=1`，
缺一即拒絕啟動。

卡片資料只從環境變數讀入（`AUTO_TICKET_CARD_*`），**不接受命令列參數**——
命令列會留在 shell 歷史。全程日誌只印末四碼。

用法：
    uv run python scripts/run_purchase.py --task task.json
    uv run python scripts/run_purchase.py --task task.json --timeline out/timeline.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from adapters.payment import (  # noqa: E402
    AutomatedCreditCardProvider,
    MockPaymentProvider,
    PaymentOutcome,
    masked_last4,
)
from adapters.ticketing.kktix.adapter import KKTIXAdapter  # noqa: E402
from adapters.verification import ManualVerificationProvider  # noqa: E402
from browser.context_factory import DEFAULT_SCREENSHOT_DIR, BrowserProfile  # noqa: E402
from browser.manager import PlaywrightManager  # noqa: E402
from domain.task import CreditCardProfile, PurchaseTaskSpec  # noqa: E402
from purchase.orchestrator import PurchaseOrchestrator  # noqa: E402
from scheduler.scheduler import WarmupScheduler  # noqa: E402
from telemetry.logging import configure_logging, get_logger  # noqa: E402
from telemetry.timeline import TimelineRecorder  # noqa: E402

CARD_ENV_NUMBER = "AUTO_TICKET_CARD_NUMBER"
CARD_ENV_MONTH = "AUTO_TICKET_CARD_EXP_MONTH"
CARD_ENV_YEAR = "AUTO_TICKET_CARD_EXP_YEAR"
CARD_ENV_CODE = "AUTO_TICKET_CARD_SECURITY_CODE"
CARD_ENV_HOLDER = "AUTO_TICKET_CARD_HOLDER"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_purchase",
        description="KKTIX 購票流程研究執行器（預設 dry-run，不發動金流）",
    )
    parser.add_argument("--task", type=Path, required=True, help="任務 JSON（PurchaseTaskSpec 欄位）")
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="使用 Mock 付款並停在送出鈕之前（預設啟用）",
    )
    parser.add_argument(
        "--real-payment",
        action="store_true",
        default=False,
        help=f"啟用真實刷卡；另需環境變數 AUTO_TICKET_ENABLE_REAL_PAYMENT=1 與 {CARD_ENV_NUMBER} 等卡片變數",
    )
    parser.add_argument("--timeline", type=Path, default=None, help="Timeline JSON 輸出路徑")
    parser.add_argument("--screenshot-dir", type=Path, default=DEFAULT_SCREENSHOT_DIR)
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--log-level", default="INFO")
    return parser


def load_spec(path: Path) -> PurchaseTaskSpec:
    return PurchaseTaskSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))


def card_from_env() -> CreditCardProfile | None:
    number = os.environ.get(CARD_ENV_NUMBER)
    if not number:
        return None
    return CreditCardProfile(
        card_number=number,
        expiry_month=os.environ.get(CARD_ENV_MONTH, ""),
        expiry_year=os.environ.get(CARD_ENV_YEAR, ""),
        cvv=os.environ.get(CARD_ENV_CODE, ""),
        cardholder_name=os.environ.get(CARD_ENV_HOLDER, ""),
    )


async def prompt_for_answer(challenge: Any) -> str:
    print(f"[verification] {challenge.question}", file=sys.stderr)
    return await asyncio.to_thread(input, "answer> ")


async def run(args: argparse.Namespace) -> int:
    configure_logging(args.log_level)
    log = get_logger("run_purchase")
    spec = load_spec(args.task)
    telemetry = TimelineRecorder()

    if args.real_payment and args.dry_run:
        log.error("conflicting_payment_flags", hint="--real-payment 需搭配 --no-dry-run")
        return 2

    if args.real_payment:
        payment: Any = AutomatedCreditCardProvider(allow_real_payment=True, telemetry=telemetry)
        profile = card_from_env()
    else:
        payment = MockPaymentProvider(
            simulate=PaymentOutcome.CHECKPOINT_REACHED, telemetry=telemetry
        )
        profile = None

    browser = PlaywrightManager(
        BrowserProfile(name=spec.task_id, headless=args.headless),
        telemetry,
        screenshot_dir=args.screenshot_dir,
    )
    adapter = KKTIXAdapter(
        telemetry=telemetry,
        payment=payment,
        verification=ManualVerificationProvider(prompt_for_answer, telemetry=telemetry),
        attendees=spec.attendees,
    )
    scheduler = WarmupScheduler(telemetry)
    effective = spec if profile is None else spec.model_copy(update={"payment_profile": profile})
    orchestrator = PurchaseOrchestrator(
        effective,
        browser=browser,
        scheduler=scheduler,
        adapter=adapter,
        telemetry=telemetry,
        timeline_path=args.timeline,
    )
    log.info(
        "purchase_start",
        task_id=spec.task_id,
        payment_provider=payment.name,
        dry_run=bool(args.dry_run),
        card_last4=masked_last4(profile),
    )
    try:
        report = await orchestrator.run()
    finally:
        await scheduler.shutdown()
        await browser.stop()
    log.info(
        "purchase_finished",
        task_id=report.task_id,
        final_state=report.final_state,
        sale_time_error_ms=report.sale_time_error_ms,
        screenshots=len(report.screenshots),
    )
    print(json.dumps({
        "task_id": report.task_id,
        "final_state": report.final_state,
        "sale_time_error_ms": report.sale_time_error_ms,
        "ticket_trace": list(report.ticket_trace),
        "payment_outcome": report.payment.outcome.value if report.payment else None,
        "screenshots": list(report.screenshots),
        "timeline": str(report.timeline_path) if report.timeline_path else None,
        "stages": [list(s) for s in report.stages],
        "error": report.error,
    }, ensure_ascii=False, indent=2))
    return 0 if report.final_state == "COMPLETED" else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
