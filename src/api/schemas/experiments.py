from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ExperimentMetricsOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    experiment_id: str
    scheduler_error_ms: float | None = None
    sale_detection_ms: float | None = None
    event_page_load_ms: float | None = None
    ticket_selection_ms: float | None = None
    seat_selection_ms: float | None = None
    form_fill_ms: float | None = None
    verification_ms: float | None = None
    payment_ms: float | None = None
    retry_count: int = 0
    selector_fallback_count: int = 0


class ExperimentEventOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    experiment_id: str | None = None
    sequence: int
    timestamp: datetime
    elapsed_ms: float
    stage: str
    state_from: str | None = None
    state_to: str | None = None
    action: str
    details: dict[str, Any] | None = None
    screenshot_path: str | None = None
    screenshot_url: str | None = None


class ExperimentOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    task_id: str | None = None
    strategy_used: str
    clock_sync_mode: str
    sale_time_error_ms: float | None = None
    total_duration_ms: float | None = None
    final_state: str
    success: bool
    result_summary: dict[str, Any] | None = None
    created_at: datetime


class ExperimentListResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[ExperimentOut]
    total: int
    limit: int
    offset: int


class ExperimentDetailResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    experiment: ExperimentOut
    events: list[ExperimentEventOut] = Field(default_factory=list)
    metrics: ExperimentMetricsOut | None = None
