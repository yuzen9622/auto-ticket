from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from domain.preference import TicketPreference
from domain.task import AttendeeProfile, UserContactProfile, VerificationRule


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_title: str = Field(min_length=1)
    event_url: str = Field(min_length=1)
    sale_start_at: datetime
    ticket_preference: TicketPreference
    contact_profile: UserContactProfile
    attendees: list[AttendeeProfile] = Field(default_factory=list)
    payment_method: str = Field(default="mock")
    max_retries: int = Field(default=3, ge=0)
    timeout_seconds: int = Field(default=120, gt=0)
    verification_rules: list[VerificationRule] = Field(default_factory=list)
    auto_login: bool = False
    qualification_code: str | None = None
    profile: str = Field(default="live")


class TaskResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    event_id: str | None = None
    status: str
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
