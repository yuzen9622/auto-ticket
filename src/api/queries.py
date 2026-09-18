from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from domain.execution import coerce_execution_mode
from storage.models import (
    ExperimentEventModel,
    ExperimentMetricsModel,
    ExperimentModel,
    PurchaseTaskModel,
)

from .schemas.experiments import (
    ExperimentDetailResponse,
    ExperimentEventOut,
    ExperimentMetricsOut,
    ExperimentOut,
)
from .schemas.tasks import TaskResponse


def _to_task_response(orm: PurchaseTaskModel) -> TaskResponse:
    spec = dict(orm.spec or {})
    return TaskResponse(
        id=orm.id,
        event_id=orm.event_id,
        status=orm.status,
        execution_mode=coerce_execution_mode(spec.get("execution_mode")).value,
        spec=spec,
        scheduled_at=orm.scheduled_at,
        started_at=orm.started_at,
        finished_at=orm.finished_at,
        error_message=orm.error_message,
        created_at=orm.created_at,
    )


def _to_experiment_out(orm: ExperimentModel) -> ExperimentOut:
    return ExperimentOut(
        id=orm.id,
        task_id=orm.task_id,
        strategy_used=orm.strategy_used,
        clock_sync_mode=orm.clock_sync_mode,
        sale_time_error_ms=orm.sale_time_error_ms,
        total_duration_ms=orm.total_duration_ms,
        final_state=orm.final_state,
        success=bool(orm.success),
        result_summary=dict(orm.result_summary or {}) if orm.result_summary else None,
        created_at=orm.created_at,
    )


def _to_event_out(orm: ExperimentEventModel) -> ExperimentEventOut:
    url = (
        f"/static/screenshots/{Path(orm.screenshot_path).name}"
        if orm.screenshot_path
        else None
    )
    event_id_val = orm.id
    if not isinstance(event_id_val, int):
        try:
            event_id_val = int(event_id_val)
        except (ValueError, TypeError):
            event_id_val = 0
    return ExperimentEventOut(
        id=event_id_val,
        experiment_id=orm.experiment_id,
        sequence=orm.sequence,
        timestamp=orm.timestamp,
        elapsed_ms=orm.elapsed_ms,
        stage=orm.stage,
        state_from=orm.state_from,
        state_to=orm.state_to,
        action=orm.action,
        details=dict(orm.details or {}) if orm.details else None,
        screenshot_path=orm.screenshot_path,
        screenshot_url=url,
    )


def _to_metrics_out(orm: ExperimentMetricsModel) -> ExperimentMetricsOut:
    return ExperimentMetricsOut(
        experiment_id=orm.experiment_id,
        scheduler_error_ms=orm.scheduler_error_ms,
        sale_detection_ms=orm.sale_detection_ms,
        event_page_load_ms=orm.event_page_load_ms,
        ticket_selection_ms=orm.ticket_selection_ms,
        seat_selection_ms=orm.seat_selection_ms,
        form_fill_ms=orm.form_fill_ms,
        verification_ms=orm.verification_ms,
        payment_ms=orm.payment_ms,
        retry_count=orm.retry_count,
        selector_fallback_count=orm.selector_fallback_count,
    )


async def list_tasks(
    db: Any,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[TaskResponse], int]:
    async with db.session() as session:
        count_stmt = select(func.count(PurchaseTaskModel.id))
        stmt = select(PurchaseTaskModel)
        if status:
            count_stmt = count_stmt.where(PurchaseTaskModel.status == status.upper())
            stmt = stmt.where(PurchaseTaskModel.status == status.upper())

        total = (await session.execute(count_stmt)).scalar() or 0
        stmt = (
            stmt.order_by(PurchaseTaskModel.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = (await session.execute(stmt)).scalars().all()
        count_val = total or 0
        if not isinstance(count_val, int):
            try:
                count_val = int(count_val)
            except (ValueError, TypeError):
                count_val = 0
        return [_to_task_response(r) for r in rows], count_val


async def get_task(db: Any, task_id: str) -> TaskResponse | None:
    async with db.session() as session:
        orm = await session.get(PurchaseTaskModel, task_id)
        if orm is None:
            return None
        return _to_task_response(orm)


async def list_experiments(
    db: Any,
    *,
    task_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[ExperimentOut], int]:
    async with db.session() as session:
        count_stmt = select(func.count(ExperimentModel.id))
        stmt = select(ExperimentModel)
        if task_id:
            count_stmt = count_stmt.where(ExperimentModel.task_id == task_id)
            stmt = stmt.where(ExperimentModel.task_id == task_id)

        total = (await session.execute(count_stmt)).scalar() or 0
        stmt = (
            stmt.order_by(ExperimentModel.created_at.desc()).offset(offset).limit(limit)
        )
        rows = (await session.execute(stmt)).scalars().all()
        count_val = total or 0
        if not isinstance(count_val, int):
            try:
                count_val = int(count_val)
            except (ValueError, TypeError):
                count_val = 0
        return [_to_experiment_out(r) for r in rows], count_val


async def get_experiment_detail(
    db: Any,
    experiment_id: str,
) -> ExperimentDetailResponse | None:
    async with db.session() as session:
        exp = await session.get(ExperimentModel, experiment_id)
        if exp is None:
            return None

        events_stmt = (
            select(ExperimentEventModel)
            .where(ExperimentEventModel.experiment_id == experiment_id)
            .order_by(ExperimentEventModel.sequence.asc())
        )
        event_rows = (await session.execute(events_stmt)).scalars().all()

        metrics = await session.get(ExperimentMetricsModel, experiment_id)

        return ExperimentDetailResponse(
            experiment=_to_experiment_out(exp),
            events=[_to_event_out(e) for e in event_rows],
            metrics=_to_metrics_out(metrics) if metrics is not None else None,
        )
