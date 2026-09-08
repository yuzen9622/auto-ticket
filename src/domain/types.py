from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict


class DomainBaseModel(BaseModel):
    """Base for every domain entity: invariants also hold for post-init assignment."""

    model_config = ConfigDict(validate_assignment=True, extra="forbid")


def ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(
            "naive datetime rejected: all domain datetimes must be timezone-aware UTC"
        )
    return value.astimezone(timezone.utc)


UtcDatetime = Annotated[datetime, AfterValidator(ensure_aware_utc)]
