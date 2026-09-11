from __future__ import annotations

from typing import Any

from domain.task import PaymentMethod, PurchaseTaskSpec


def rehydrate_spec(task_spec_dict: dict[str, Any]) -> PurchaseTaskSpec:
    """從 persistable dict 反序列化回 PurchaseTaskSpec。

    強制使用 PaymentMethod.MOCK，嚴格遵守 [D4-4]。
    """
    payload = dict(task_spec_dict)
    payload["payment_method"] = PaymentMethod.MOCK
    return PurchaseTaskSpec.model_validate(payload)
