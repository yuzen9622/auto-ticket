from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from domain.execution import ExecutionMode
from domain.preference import TicketPreference
from domain.task import AttendeeProfile, UserContactProfile, VerificationRule


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_title: str = Field(min_length=1)
    event_url: str = Field(min_length=1)
    sale_start_at: datetime | None = None
    """要等的開賣時間。

    活動已經在販售時留空：沒有東西要等，任務建立後立即執行。填了過去的時間也一樣——
    後端只認「這個時間還沒到」才排預約搶票。
    """
    ticket_preference: TicketPreference
    contact_profile: UserContactProfile
    attendees: list[AttendeeProfile] = Field(default_factory=list)
    execution_mode: ExecutionMode = ExecutionMode.LIVE
    """正式或測試；付款 adapter 由後端依此決定，前端無法指定 adapter。"""
    payment_method: str | None = None
    """遺留欄位。只接受空值或 `"mock"`，其餘一律拒絕。"""
    max_retries: int = Field(default=3, ge=0)
    timeout_seconds: int = Field(default=120, gt=0)
    verification_rules: list[VerificationRule] = Field(default_factory=list)
    auto_login: bool = False
    qualification_code: str | None = None
    session_preference: str | None = None
    auto_cloudflare: bool = True
    auto_ocr: bool = True
    auto_submit_verification: bool = True
    ocr_model_path: str | None = None
    ocr_max_retries: int = Field(default=5, ge=1, le=20)
    cloudflare_max_retries: int = Field(default=3, ge=0, le=20)
    debug_screenshots_and_logs: bool = False
    profile: str = Field(default="live")


class TaskResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    event_id: str | None = None
    status: str
    execution_mode: str = ExecutionMode.MOCK.value
    spec: dict[str, Any] = Field(default_factory=dict)
    scheduled_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_message: str | None = None
    created_at: datetime


class TaskListResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[TaskResponse]
    total: int
    limit: int
    offset: int


class TaskDetailResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task: TaskResponse
    job_id: str | None = None
    job_state: str | None = None
