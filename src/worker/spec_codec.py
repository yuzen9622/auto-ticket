from __future__ import annotations

from typing import Any

from domain.execution import ExecutionMode, coerce_execution_mode
from domain.task import PaymentMethod, PurchaseTaskSpec


def rehydrate_spec(task_spec_dict: dict[str, Any]) -> PurchaseTaskSpec:
    """從 persistable dict 反序列化回 PurchaseTaskSpec。

    執行模式**照原樣保留**——Worker 掛哪一顆付款 adapter 由 `execution_mode` 決定，
    這裡不得覆寫，否則正式任務會被靜默降級成測試任務。舊 payload 沒有這個欄位時，
    退回最保守的 MOCK。

    `payment_profile` 從不落地（卡號永不寫入 DB），所以 persistable dict 一定沒有它；
    `payment_method` 因此只能標成 MOCK，否則 spec 的 validator 會因缺少 profile 而拒絕。
    它是遺留欄位，不參與 adapter 選擇。
    """
    payload = dict(task_spec_dict)
    payload["execution_mode"] = coerce_execution_mode(payload.get("execution_mode"))
    if payload.get("payment_profile") is None:
        payload["payment_method"] = PaymentMethod.MOCK
    return PurchaseTaskSpec.model_validate(payload)


def execution_mode_of(spec: PurchaseTaskSpec) -> ExecutionMode:
    return spec.execution_mode
