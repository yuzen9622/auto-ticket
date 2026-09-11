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
from adapters.verification import ManualVerificationProvider
from adapters.verification.rule_based import RuleBasedVerificationProvider
from browser.cdp_attach import (  # noqa: E402
    CdpEndpointError,
    parse_cdp_endpoint,
    parse_page_target,
)
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
        epilog=(
            "登入與人機驗證一律由人自己在瀏覽器裡完成："
            "先 `scripts/login.py --profile <name>` 登好，再用 `--profile <name> --no-headless` 執行；"
            "開賣前的就緒閘門會等你把頁面弄到可下單狀態，逾時仍未就緒即中止。"
            "\n\n借用模式（--cdp-endpoint）三步驟："
            "\n  1) 自己啟動一個帶 CDP 的真實 Chrome，--user-data-dir 必須是專屬的非預設目錄"
            "（Chrome 136 起若用預設目錄會忽略 --remote-debugging-port）："
            "\n     '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'"
            ' --remote-debugging-port=9222 --user-data-dir="$HOME/.auto-ticket-chrome"'
            " --no-first-run --no-default-browser-check"
            "\n  2) 在那個 Chrome 裡自己通過 Cloudflare、登入 KKTIX、開好購票登記頁。"
            "請不要開無痕視窗，也不要重複開同一個活動頁籤——"
            "本程式只保證「命中恰好 1 個頁籤否則中止」，看不見的無痕視窗無法偵測。"
            "\n  3) 保持該 Chrome 開著，另開終端機加上 --cdp-endpoint http://127.0.0.1:9222 執行。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--task", type=Path, required=True, help="任務 JSON（PurchaseTaskSpec 欄位）"
    )
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="建立未付款保留訂單檢查點，不扣款發動金流（預設啟用）",
    )
    parser.add_argument(
        "--real-payment",
        action="store_true",
        default=False,
        help=f"啟用真實刷卡；另需環境變數 AUTO_TICKET_ENABLE_REAL_PAYMENT=1 與 {CARD_ENV_NUMBER} 等卡片變數",
    )
    parser.add_argument(
        "--profile",
        default="live",
        help="瀏覽器 profile 名稱（預設 live）。登入狀態存在 .browser_profiles/<profile>／"
        "需要沿用已登入的 profile 時指定它",
    )
    parser.add_argument(
        "--member-code",
        default=None,
        help="KKTIX 會員專屬邀請碼（亦可由環境變數 AUTO_TICKET_MEMBER_CODE 或 task.json 提供）",
    )
    parser.add_argument(
        "--session-gate-timeout",
        type=float,
        default=240.0,
        help="開賣前等待「人完成登入／人機驗證」的秒數上限（預設 240）；逾時即中止不下單",
    )
    parser.add_argument(
        "--timeline", type=Path, default=None, help="Timeline JSON 輸出路徑"
    )
    parser.add_argument("--screenshot-dir", type=Path, default=DEFAULT_SCREENSHOT_DIR)
    parser.add_argument(
        "--headless", action=argparse.BooleanOptionalAction, default=True
    )
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument(
        "--cdp-endpoint",
        default=None,
        help="連上你自己啟動的本機 Chrome（http://127.0.0.1:9222，僅 loopback）。"
        "給了就只走借用模式，連不上直接中止",
    )
    parser.add_argument(
        "--cdp-page-url",
        default=None,
        help="借用模式要挑的頁籤絕對網址（預設 = 任務的 event_url）。"
        "比對 scheme/host/port 與 path 前綴，忽略 query",
    )
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
    try:
        spec = load_spec(args.task)
    except (OSError, ValueError) as exc:
        log.error("invalid_task", error_type=type(exc).__name__)
        return 2
    telemetry = TimelineRecorder()

    if args.real_payment and args.dry_run:
        log.error(
            "conflicting_payment_flags", hint="--real-payment 需搭配 --no-dry-run"
        )
        return 2

    if args.real_payment:
        payment: Any = AutomatedCreditCardProvider(
            allow_real_payment=True, telemetry=telemetry
        )
        profile = card_from_env()
    else:
        payment = MockPaymentProvider(
            simulate=PaymentOutcome.CHECKPOINT_REACHED, telemetry=telemetry
        )
        profile = None

    cdp_endpoint = None
    cdp_target = None
    if args.cdp_endpoint is not None:
        try:
            cdp_endpoint = parse_cdp_endpoint(args.cdp_endpoint)
            cdp_target = parse_page_target(args.cdp_page_url or spec.event_url)
        except CdpEndpointError as exc:
            log.error("invalid_cdp_option", error=str(exc))
            return 2
        if args.headless or args.profile:
            log.warning(
                "cdp_mode_ignores_launch_options",
                hint="借用模式不套用 --headless / --profile 的 user_data_dir；"
                "--profile 僅作為 telemetry 與截圖標籤",
            )

    browser = PlaywrightManager(
        BrowserProfile(name=args.profile or spec.task_id, headless=args.headless),
        telemetry,
        screenshot_dir=args.screenshot_dir,
        cdp_endpoint=args.cdp_endpoint,
        cdp_page_url=(args.cdp_page_url or spec.event_url) if cdp_endpoint else None,
    )
    if args.member_code:
        os.environ["AUTO_TICKET_MEMBER_CODE"] = args.member_code

    if spec.verification_rules:
        verification: Any = RuleBasedVerificationProvider(
            spec.verification_rules, telemetry=telemetry
        )
    else:
        verification = ManualVerificationProvider(
            prompt_for_answer, telemetry=telemetry
        )

    adapter = KKTIXAdapter(
        telemetry=telemetry,
        payment=payment,
        verification=verification,
        attendees=spec.attendees,
    )
    scheduler = WarmupScheduler(telemetry)
    updates: dict[str, Any] = {}
    if profile is not None:
        updates["payment_profile"] = profile
    if args.member_code and not spec.qualification_code:
        updates["qualification_code"] = args.member_code
    effective = spec.model_copy(update=updates) if updates else spec
    gate_hints = {
        "CHALLENGE": "瀏覽器裡出現人機驗證，請自行通過（本程式不會代為繞過）",
        "LOGIN": "被導到登入頁，請在瀏覽器裡自行登入",
        "EVENT": "目前停在活動主頁，請自行點進購票登記頁",
        "ORDER": "目前停在訂單頁，請確認是不是拿錯網址",
        "UNKNOWN": "頁面無法辨識，請自行確認瀏覽器狀態",
    }

    async def announce_gate(kind: str, attempt: int) -> None:
        log.warning(
            "waiting_for_human",
            page_kind=kind,
            attempt=attempt,
            hint=gate_hints.get(kind, "請自行確認瀏覽器狀態"),
        )

    orchestrator = PurchaseOrchestrator(
        effective,
        browser=browser,
        scheduler=scheduler,
        adapter=adapter,
        telemetry=telemetry,
        timeline_path=args.timeline,
        session_gate_timeout_s=args.session_gate_timeout,
        session_gate=announce_gate,
    )
    start_fields: dict[str, Any] = {
        "task_id": spec.task_id,
        "payment_provider": payment.name,
        "dry_run": bool(args.dry_run),
        "browser_profile": browser.profile.name,
    }
    if cdp_endpoint is not None and cdp_target is not None:
        # 借用模式下 user_data_dir 無意義且誤導；只記已去敲的 origin 與 label。
        start_fields["browser_mode"] = "cdp_attach"
        start_fields["cdp_endpoint"] = cdp_endpoint.origin
        start_fields["cdp_page_target"] = cdp_target.label
    else:
        start_fields["user_data_dir"] = str(browser.profile.user_data_dir)
    start_fields["card_last4"] = masked_last4(profile)
    log.info("purchase_start", **start_fields)
    try:
        report = await orchestrator.run()
    finally:
        try:
            await scheduler.shutdown()
        finally:
            await browser.stop()
    log.info(
        "purchase_finished",
        task_id=report.task_id,
        final_state=report.final_state,
        sale_time_error_ms=report.sale_time_error_ms,
        screenshots=len(report.screenshots),
        screenshots_expected=report.screenshots_expected,
    )
    print(
        json.dumps(
            {
                "task_id": report.task_id,
                "final_state": report.final_state,
                "sale_time_error_ms": report.sale_time_error_ms,
                "ticket_trace": list(report.ticket_trace),
                "ticket_failure_reasons": list(report.ticket_failure_reasons),
                "payment_outcome": report.payment.outcome.value
                if report.payment
                else None,
                "screenshots": list(report.screenshots),
                "screenshots_expected": report.screenshots_expected,
                "timeline": str(report.timeline_path) if report.timeline_path else None,
                "stages": [list(s) for s in report.stages],
                "error": report.error,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report.final_state == "COMPLETED" else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
