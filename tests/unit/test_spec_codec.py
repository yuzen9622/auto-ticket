from __future__ import annotations

from datetime import UTC, datetime

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
