from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AccountStatusResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    platform: str
    source: str
    configured: bool
    masked_account: str | None = None


class StoreCredentialsRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    account: str = Field(min_length=1)
    access_key: str = Field(min_length=1)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mode: str = Field(default="auto")
    profile: str = Field(default="live")


class JobOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    job_id: str
    kind: str
    state: str
    result: dict[str, Any] | None = None
    error: str | None = None


class SessionCheckResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    job_id: str
    kind: str
    state: str
