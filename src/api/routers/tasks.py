from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Response, status

from broker.broker import SqliteTaskBroker
from broker.jobs import JobKind
from domain.task import PaymentMethod, PurchaseTaskSpec
from storage.database import Database
from storage.models import PurchaseTaskModel
from storage.repositories.event_repository import EventRepository
from storage.repositories.task_repository import TaskRepository

from .. import queries
from ..deps import get_broker, get_db
from ..errors import ConflictError, NotFoundError, UnsupportedError
from ..schemas.tasks import (
    CreateTaskRequest,
    TaskDetailResponse,
    TaskListResponse,
    TaskResponse,
)

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    req: CreateTaskRequest,
    db: Database = Depends(get_db),
    broker: SqliteTaskBroker = Depends(get_broker),
) -> TaskResponse:
    if req.payment_method.lower() != "mock":
        raise UnsupportedError(
            f"payment_method '{req.payment_method}' is unsupported via API; "
            "use scripts/run_purchase.py --real-payment for credit card checkout"
        )

    task_id = f"task_{uuid.uuid4().hex[:16]}"
    spec = PurchaseTaskSpec(
        task_id=task_id,
        event_title=req.event_title,
        event_url=req.event_url,
        sale_start_at=req.sale_start_at,
        ticket_preference=req.ticket_preference,
        contact_profile=req.contact_profile,
        attendees=tuple(req.attendees),
        payment_method=PaymentMethod.MOCK,
        max_retries=req.max_retries,
        timeout_seconds=req.timeout_seconds,
        verification_rules=tuple(req.verification_rules),
        auto_login=req.auto_login,
        qualification_code=req.qualification_code,
    )

    event_id: str | None = None
    async with db.session() as session:
        event_repo = EventRepository(session)
        ev = await event_repo.get_by_canonical_url(req.event_url)
        if ev is not None:
            event_id = ev.id

    async with db.session() as session:
        task_repo = TaskRepository(session)
        await task_repo.create(
            spec,
            event_id=event_id,
            scheduled_at=spec.sale_start_at,
        )

    warmup_lead = timedelta(minutes=11)
    available_at = spec.sale_start_at - warmup_lead
    now = datetime.now(UTC)
    available_at = max(available_at, now)

    await broker.enqueue(
        kind=JobKind.PURCHASE,
        profile=req.profile,
        task_id=task_id,
        payload={"spec": spec.to_persistable_dict()},
        available_at=available_at,
        max_attempts=1,
    )

    created = await queries.get_task(db, task_id)
    if created is None:
        raise RuntimeError("Failed to retrieve created task")
    return created


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Database = Depends(get_db),
) -> TaskListResponse:
    items, total = await queries.list_tasks(
        db, status=status_filter, limit=limit, offset=offset
    )
    return TaskListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{task_id}", response_model=TaskDetailResponse)
async def get_task_detail(
    task_id: str,
    db: Database = Depends(get_db),
    broker: SqliteTaskBroker = Depends(get_broker),
) -> TaskDetailResponse:
    task = await queries.get_task(db, task_id)
    if task is None:
        raise NotFoundError(f"Task {task_id} not found")
    jobs = await broker.list_jobs(task_id=task_id, limit=1)
    job_id = jobs[0].id if jobs else None
    job_state = jobs[0].state.value if jobs else None
    return TaskDetailResponse(task=task, job_id=job_id, job_state=job_state)


@router.post("/{task_id}/start", status_code=status.HTTP_202_ACCEPTED)
async def start_task(
    task_id: str,
    db: Database = Depends(get_db),
    broker: SqliteTaskBroker = Depends(get_broker),
) -> dict[str, bool | str]:
    task = await queries.get_task(db, task_id)
    if task is None:
        raise NotFoundError(f"Task {task_id} not found")
    ok = await broker.trigger_now(task_id)
    return {"accepted": True, "task_id": task_id, "triggered": ok}


@router.post("/{task_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_task(
    task_id: str,
    db: Database = Depends(get_db),
    broker: SqliteTaskBroker = Depends(get_broker),
) -> dict[str, bool | str]:
    task = await queries.get_task(db, task_id)
    if task is None:
        raise NotFoundError(f"Task {task_id} not found")
    await broker.cancel(task_id)
    current = await queries.get_task(db, task_id)
    current_status = current.status if current else task.status
    return {"accepted": True, "task_id": task_id, "status": current_status}


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: str,
    db: Database = Depends(get_db),
) -> Response:
    task = await queries.get_task(db, task_id)
    if task is None:
        raise NotFoundError(f"Task {task_id} not found")
    if task.status not in ("CREATED", "CANCELLED", "FAILED"):
        raise ConflictError(
            f"Cannot delete task in status {task.status}; only CREATED, CANCELLED, or FAILED tasks may be deleted"
        )
    async with db.session() as session:
        orm = await session.get(PurchaseTaskModel, task_id)
        if orm is not None:
            await session.delete(orm)
            await session.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
