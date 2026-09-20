from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from domain.execution import ExecutionMode
from domain.task import PurchaseTaskSpec
from tests.netguard import netguard_autouse  # noqa: F401
from worker.spec_codec import rehydrate_spec

BASE_SPEC = {
    "task_id": "task_codec_01",
    "event_title": "Test Concert",
    "event_url": "https://example.test/events/test",
    "sale_start_at": datetime(2026, 10, 1, 12, 0, tzinfo=UTC).isoformat(),
    "ticket_preference": {
        "quantity": 2,
        "priorities": [{"price": 2800, "priority": 1}],
    },
    "contact_profile": {
        "name": "Test User",
        "phone": "0912345678",
        "email": "user@example.test",
    },
    "max_retries": 3,
    "timeout_seconds": 120,
}


def test_live_mode_survives_the_round_trip() -> None:
    """Worker 不得把正式任務靜默降級成測試任務。"""
    spec = rehydrate_spec({**BASE_SPEC, "execution_mode": "live"})
    assert spec.execution_mode is ExecutionMode.LIVE


def test_mock_mode_survives_the_round_trip() -> None:
    spec = rehydrate_spec({**BASE_SPEC, "execution_mode": "mock"})
    assert spec.execution_mode is ExecutionMode.MOCK


def test_legacy_payload_without_mode_falls_back_to_mock() -> None:
    spec = rehydrate_spec(dict(BASE_SPEC))
    assert spec.execution_mode is ExecutionMode.MOCK


def test_unrecognised_mode_falls_back_to_mock() -> None:
    spec = rehydrate_spec({**BASE_SPEC, "execution_mode": "supersonic"})
    assert spec.execution_mode is ExecutionMode.MOCK


def test_persisted_dict_carries_the_mode_but_no_card_secrets() -> None:
    # API 建立 spec 時就是這個形狀：卡片資料不經過本程式，所以遺留欄位標成 mock。
    spec = PurchaseTaskSpec.model_validate(
        {**BASE_SPEC, "execution_mode": "live", "payment_method": "mock"}
    )
    payload = spec.to_persistable_dict()
    assert payload["execution_mode"] == "live"
    assert "payment_profile" not in payload
    assert rehydrate_spec(payload).execution_mode is ExecutionMode.LIVE


def test_legacy_payload_without_automation_fields_takes_defaults() -> None:
    spec = rehydrate_spec(dict(BASE_SPEC))
    assert spec.auto_cloudflare is True
    assert spec.auto_ocr is True
    assert spec.auto_submit_verification is True
    assert spec.ocr_model_path is None
    assert spec.ocr_max_retries == 5
    assert spec.cloudflare_max_retries == 3
    assert spec.debug_screenshots_and_logs is False


def test_automation_fields_survive_the_round_trip(tmp_path: Path) -> None:
    model_file = str(tmp_path / "m.onnx")
    custom_fields = {
        "auto_cloudflare": False,
        "auto_ocr": False,
        "auto_submit_verification": False,
        "ocr_model_path": model_file,
        "ocr_max_retries": 9,
        "cloudflare_max_retries": 1,
        "debug_screenshots_and_logs": True,
    }
    spec = PurchaseTaskSpec.model_validate(
        {**BASE_SPEC, "payment_method": "mock", **custom_fields}
    )
    persisted = spec.to_persistable_dict()
    rehydrated = rehydrate_spec(persisted)
    for k, v in custom_fields.items():
        assert getattr(rehydrated, k) == v


def test_persisted_dict_carries_the_automation_fields() -> None:
    spec = PurchaseTaskSpec.model_validate(
        {**BASE_SPEC, "payment_method": "mock"}
    )
    payload = spec.to_persistable_dict()
    automation_keys = {
        "auto_cloudflare",
        "auto_ocr",
        "auto_submit_verification",
        "ocr_model_path",
        "ocr_max_retries",
        "cloudflare_max_retries",
        "debug_screenshots_and_logs",
    }
    assert automation_keys.issubset(payload.keys())
    assert "payment_profile" not in payload


def test_out_of_range_retries_are_rejected() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PurchaseTaskSpec.model_validate(
            {**BASE_SPEC, "payment_method": "mock", "ocr_max_retries": 0}
        )

    with pytest.raises(ValidationError):
        PurchaseTaskSpec.model_validate(
            {**BASE_SPEC, "payment_method": "mock", "cloudflare_max_retries": -1}
        )


def test_unknown_extra_field_still_rejected() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PurchaseTaskSpec.model_validate(
            {**BASE_SPEC, "payment_method": "mock", "nonsense": 1}
        )
