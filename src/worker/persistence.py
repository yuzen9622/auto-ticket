from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from purchase.orchestrator import PurchaseReport
from storage.database import Database
from storage.models import (
    ExperimentEventModel,
    ExperimentMetricsModel,
    ExperimentModel,
)
from telemetry.timeline import TimelineEvent


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def save_experiment(
    db: Database,
    *,
    experiment_id: str,
    task_id: str | None = None,
    report: PurchaseReport,
    events: Sequence[TimelineEvent],
    clock_sync_mode: str = "ntp_with_http_fallback",
) -> None:
    now = _utcnow()
    total_duration_ms: float | None = None
    if events:
        first_us = events[0].monotonic_us
        last_us = events[-1].monotonic_us
        total_duration_ms = max(0.0, (last_us - first_us) / 1000.0)

    result_summary: dict[str, Any] = {
        "ticket_trace": list(report.ticket_trace),
        "ticket_failure_reasons": list(report.ticket_failure_reasons),
        "stages": [list(s) for s in report.stages],
        "screenshots": list(report.screenshots),
        "screenshots_expected": report.screenshots_expected,
        "aborted": report.aborted,
        "error": report.error,
        "payment_provider": getattr(report.payment, "provider", None)
        if report.payment
        else None,
    }

    exp_model = ExperimentModel(
        id=experiment_id,
        task_id=task_id,
        strategy_used="priority_first",
        clock_sync_mode=clock_sync_mode,
        sale_time_error_ms=report.sale_time_error_ms,
        total_duration_ms=total_duration_ms,
        final_state=report.final_state,
        success=bool(report.final_state == "COMPLETED"),
        result_summary=result_summary,
        created_at=now,
    )

    event_models: list[ExperimentEventModel] = []
    for ev in events:
        state_from = ev.detail.get("from_state") or ev.detail.get("source")
        state_to = ev.detail.get("to_state") or ev.detail.get("target")
        screenshot_path = ev.detail.get("screenshot_path")

        event_models.append(
            ExperimentEventModel(
                experiment_id=experiment_id,
                sequence=ev.sequence,
                timestamp=ev.wall_time,
                elapsed_ms=ev.monotonic_us / 1000.0,
                stage=ev.event_type.value,
                state_from=str(state_from) if state_from is not None else None,
                state_to=str(state_to) if state_to is not None else None,
                action=ev.name,
                details=dict(ev.detail),
                screenshot_path=str(screenshot_path)
                if screenshot_path is not None
                else None,
            )
        )

    metrics_model = ExperimentMetricsModel(
        experiment_id=experiment_id,
        scheduler_error_ms=report.sale_time_error_ms,
        sale_detection_ms=None,
        event_page_load_ms=None,
        ticket_selection_ms=None,
        seat_selection_ms=None,
        form_fill_ms=None,
        verification_ms=None,
        payment_ms=None,
        retry_count=len(report.ticket_failure_reasons),
        selector_fallback_count=0,
    )

    async with db.session() as session:
        session.add(exp_model)
        await session.flush()
        if event_models:
            session.add_all(event_models)
        session.add(metrics_model)
        await session.flush()
