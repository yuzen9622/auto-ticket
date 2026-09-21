from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, ValidationError

_UNSET = object()


class DomainBaseModel(BaseModel):
    """Base for every domain entity: invariants also hold for post-init assignment."""

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    def __setattr__(self, name: str, value: Any) -> None:
        """指派被驗證擋下來時，把欄位還原成原值。

        `validate_assignment` 對欄位層 constraint 是先驗證再寫入，但跨欄位的
        model validator 是**先寫入再驗證**——擋下來時物件已經被改掉了。少了這層，
        「不變式在建構後的指派上也成立」就只剩「會拋例外」，接住例外的人拿到的是
        一個沒通過驗證的物件。
        """
        previous = getattr(self, name, _UNSET)
        fields_set = set(self.__pydantic_fields_set__)
        try:
            super().__setattr__(name, value)
        except ValidationError:
            # 繞開 pydantic 的 __setattr__ 直接寫回舊值：再走一次驗證只會再擋一次。
            if previous is not _UNSET:
                object.__setattr__(self, name, previous)
            self.__pydantic_fields_set__.clear()
            self.__pydantic_fields_set__.update(fields_set)
            raise


def ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(
            "naive datetime rejected: all domain datetimes must be timezone-aware UTC"
        )
    return value.astimezone(timezone.utc)


UtcDatetime = Annotated[datetime, AfterValidator(ensure_aware_utc)]
