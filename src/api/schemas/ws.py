from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ServerMessageType(str, Enum):
    STATE_CHANGED = "STATE_CHANGED"
    CLOCK_TICK = "CLOCK_TICK"
    SCREENSHOT_CAPTURED = "SCREENSHOT_CAPTURED"
    TASK_LOG = "TASK_LOG"
    ERROR = "ERROR"


class ClientAction(str, Enum):
    EMERGENCY_STOP = "EMERGENCY_STOP"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    FORCE_TRANSITION = "FORCE_TRANSITION"


class ServerMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: ServerMessageType
    task_id: str
    experiment_id: str | None = None
    timestamp: str
    payload: dict[str, Any] = Field(default_factory=dict)
    outbox_id: int | None = None


class ClientCommand(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: ClientAction
    task_id: str | None = None
    reason: str | None = None
    target_state: str | None = None
