from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from storage.database import Database

from .. import queries
from ..deps import get_db
from ..errors import NotFoundError
from ..schemas.experiments import (
    ExperimentDetailResponse,
    ExperimentListResponse,
)

router = APIRouter(prefix="/api/v1/experiments", tags=["experiments"])


@router.get("", response_model=ExperimentListResponse)
async def list_experiments(
    task_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Database = Depends(get_db),
) -> ExperimentListResponse:
    items, total = await queries.list_experiments(
        db, task_id=task_id, limit=limit, offset=offset
    )
    return ExperimentListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{experiment_id}", response_model=ExperimentDetailResponse)
async def get_experiment(
    experiment_id: str,
    db: Database = Depends(get_db),
) -> ExperimentDetailResponse:
    detail = await queries.get_experiment_detail(db, experiment_id)
    if detail is None:
        raise NotFoundError(f"Experiment {experiment_id} not found")
    return detail
