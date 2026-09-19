from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Response, status

from accounts.service import AccountService
from broker.broker import SqliteTaskBroker
from broker.jobs import JobKind
from domain.execution import ExecutionMode
from domain.task import PaymentMethod, PurchaseTaskSpec, StartTiming
from storage.database import Database
from storage.models import PurchaseTaskModel
from storage.repositories.event_repository import EventRepository
from storage.repositories.task_repository import TaskRepository

from .. import queries
from ..deps import get_accounts, get_broker, get_db
from ..errors import (
    InvalidRequestError,
    NotFoundError,
    UnsupportedError,
)
from ..schemas.tasks import (
    CreateTaskRequest,
    TaskDetailResponse,
    TaskListResponse,
    TaskResponse,
)

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])

#: 目前只有 kktix 有 resolver 與帳號流程；正式模式的前置檢查對準這個平台。
LIVE_PLATFORM = "kktix"


def _resolve_start_timing(
    requested_sale_start_at: datetime | None, now: datetime
) -> tuple[StartTiming, datetime]:
    """決定這筆任務是等開賣還是立即執行，並給出本次執行的 T=0。

    只有「使用者指定了一個還沒到的時間」才算預約搶票。沒指定、或指定的時間已經過去，
    代表活動當下就在賣——沒有東西可等，T=0 就是現在，之後的預熱與閘門都照立即執行算。
    """
    if (
        requested_sale_start_at is not None
        and requested_sale_start_at.utcoffset() is None
    ):
        # 沒有時區的時間在這裡就攔下來：拿它跟現在比會炸，硬當 UTC 則會整整差掉八小時。
        raise InvalidRequestError(
            "sale_start_at must include a timezone offset",
            details={"reason": "naive_datetime", "field": "sale_start_at"},
        )
    if requested_sale_start_at is None or requested_sale_start_at <= now:
        return StartTiming.IMMEDIATE, now
    return StartTiming.SCHEDULED, requested_sale_start_at


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    req: CreateTaskRequest,
    db: Database = Depends(get_db),
    broker: SqliteTaskBroker = Depends(get_broker),
    accounts: AccountService = Depends(get_accounts),
) -> TaskResponse:
    # 前端不得指定付款 adapter：這裡只認遺留的 "mock" 值，其餘一律拒絕。
    if req.payment_method is not None and req.payment_method.lower() != "mock":
        raise UnsupportedError(
            f"payment_method '{req.payment_method}' is unsupported via API; "
            "the execution mode decides the payment adapter"
        )

    if req.execution_mode is ExecutionMode.LIVE and not accounts.status(
        LIVE_PLATFORM
    ).configured:
        raise InvalidRequestError(
            "live execution requires a configured ticketing account",
            details={"reason": "account_not_configured", "platform": LIVE_PLATFORM},
        )

    task_id = uuid.uuid4().hex[:16]
    now = datetime.now(UTC)
    start_timing, sale_start_at = _resolve_start_timing(req.sale_start_at, now)
    spec = PurchaseTaskSpec(
        task_id=task_id,
        event_title=req.event_title,
        event_url=req.event_url,
        sale_start_at=sale_start_at,
        start_timing=start_timing,
        ticket_preference=req.ticket_preference,
        contact_profile=req.contact_profile,
        attendees=tuple(req.attendees),
        execution_mode=req.execution_mode,
        # 卡片資料從不經過 API，因此 spec 一定沒有 payment_profile；
        # 這個遺留欄位不參與 adapter 選擇，實際 adapter 看 execution_mode。
        payment_method=PaymentMethod.MOCK,
        max_retries=req.max_retries,
        timeout_seconds=req.timeout_seconds,
        verification_rules=tuple(req.verification_rules),
        auto_login=req.auto_login,
        qualification_code=req.qualification_code,
        session_preference=req.session_preference,
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

    # 預約搶票要留預熱的時間；立即執行沒有開賣可等，晚一秒領取就是晚一秒進登記頁。
    if start_timing is StartTiming.IMMEDIATE:
        available_at = now
    else:
        warmup_lead = timedelta(minutes=11)
        available_at = max(spec.sale_start_at - warmup_lead, now)

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
    broker: SqliteTaskBroker = Depends(get_broker),
) -> Response:
    task = await queries.get_task(db, task_id)
    if task is None:
        raise NotFoundError(f"Task {task_id} not found")
    if task.status not in ("COMPLETED", "FAILED", "CANCELLED"):
        # 行程外直接刪列會留下開著的瀏覽器；中止一律交由 Worker 收斂。
        await broker.cancel(task_id)
    async with db.session() as session:
        orm = await session.get(PurchaseTaskModel, task_id)
        if orm is not None:
            await session.delete(orm)
            await session.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
