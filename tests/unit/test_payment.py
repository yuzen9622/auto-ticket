from __future__ import annotations

import json

import pytest

from adapters.payment import (
    REAL_PAYMENT_ENV_VAR,
    TEST_CARD_NUMBER,
    AutomatedCreditCardProvider,
    MockPaymentProvider,
    PaymentOutcome,
    PaymentResult,
    RealPaymentNotEnabledError,
    masked_last4,
)
from adapters.payment.mock import TEST_CARD_SECURITY_CODE
from adapters.ticketing.kktix.selectors import KKTIXSelectors
from domain.task import CreditCardProfile
from telemetry.timeline import TimelineRecorder
from tests.fake_page import FakePage
from tests.netguard import netguard_autouse  # noqa: F401

REAL_PAN = "4111111111111234"
REAL_CODE = "987"


@pytest.fixture
def profile() -> CreditCardProfile:
    return CreditCardProfile(
        card_number=REAL_PAN,
        expiry_month="09",
        expiry_year="2031",
        cvv=REAL_CODE,
        cardholder_name="TEST HOLDER",
    )


@pytest.fixture
def payment_page() -> FakePage:
    return FakePage.from_fixture("kktix_payment.html", url="https://payment.test/pay")


@pytest.fixture
def enabled_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REAL_PAYMENT_ENV_VAR, "1")


# ------------------------------------------------------------------ Mock


async def test_mock_default_simulation_is_checkpoint(payment_page: FakePage) -> None:
    result = await MockPaymentProvider().pay(payment_page, None)
    assert result.outcome is PaymentOutcome.CHECKPOINT_REACHED
    assert result.provider == "mock"


async def test_mock_never_clicks_the_confirm_payment_button(
    payment_page: FakePage, profile: CreditCardProfile
) -> None:
    await MockPaymentProvider().pay(payment_page, profile)
    confirm_ids = {str(e.get("id")) for e in payment_page.clicked_elements}
    assert "submit-payment" not in confirm_ids
    for selector in KKTIXSelectors.BTN_CONFIRM_PAYMENT:
        assert selector not in payment_page.clicks


async def test_mock_selects_credit_card_radio(payment_page: FakePage) -> None:
    await MockPaymentProvider().pay(payment_page, None)
    assert any(e.get("type") == "radio" for e in payment_page.clicked_elements)


async def test_mock_fills_test_card_not_the_real_one(
    payment_page: FakePage, profile: CreditCardProfile
) -> None:
    await MockPaymentProvider().pay(payment_page, profile)
    filled = dict(payment_page.fills)
    assert TEST_CARD_NUMBER in filled.values()
    assert REAL_PAN not in filled.values()
    assert REAL_CODE not in filled.values()


async def test_mock_reports_filled_fields(payment_page: FakePage) -> None:
    result = await MockPaymentProvider().pay(payment_page, None)
    assert result.detail["filled_fields"] == (
        "card_number",
        "card_expiry",
        "card_security_code",
    )
    assert result.detail["submitted"] is False


async def test_mock_records_checkpoint_mark(payment_page: FakePage) -> None:
    telemetry = TimelineRecorder()
    await MockPaymentProvider(telemetry=telemetry).pay(payment_page, None)
    names = [e.name for e in telemetry.events()]
    assert "payment_checkpoint_reached" in names


async def test_mock_detail_contains_no_card_secrets(
    payment_page: FakePage, profile: CreditCardProfile
) -> None:
    telemetry = TimelineRecorder()
    result = await MockPaymentProvider(telemetry=telemetry).pay(payment_page, profile)
    blob = json.dumps({k: str(v) for k, v in result.detail.items()}, ensure_ascii=False)
    blob += json.dumps([str(dict(e.detail)) for e in telemetry.events()], ensure_ascii=False)
    assert REAL_PAN not in blob
    assert REAL_CODE not in blob


async def test_mock_tolerates_page_without_payment_form() -> None:
    result = await MockPaymentProvider(timeout_ms=1).pay(FakePage("<html></html>"), None)
    assert result.outcome is PaymentOutcome.CHECKPOINT_REACHED
    assert result.detail["filled_fields"] == ()


@pytest.mark.parametrize(
    "simulated",
    [
        PaymentOutcome.CHECKPOINT_REACHED,
        PaymentOutcome.DECLINED,
        PaymentOutcome.THREE_DS_REQUIRED,
        PaymentOutcome.THREE_DS_FAILED,
        PaymentOutcome.TIMEOUT,
    ],
)
async def test_mock_simulations_never_submit(
    payment_page: FakePage, simulated: PaymentOutcome
) -> None:
    result = await MockPaymentProvider(simulate=simulated).pay(payment_page, None)
    assert result.outcome is simulated
    assert result.detail["submitted"] is False
    assert "button#submit-payment" not in payment_page.clicks


async def test_mock_security_code_is_not_a_real_one(payment_page: FakePage) -> None:
    await MockPaymentProvider().pay(payment_page, None)
    assert TEST_CARD_SECURITY_CODE in dict(payment_page.fills).values()


# -------------------------------------------------------------- 雙開關


def test_real_payment_blocked_without_any_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(REAL_PAYMENT_ENV_VAR, raising=False)
    with pytest.raises(RealPaymentNotEnabledError):
        AutomatedCreditCardProvider()


def test_real_payment_blocked_with_kwarg_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(REAL_PAYMENT_ENV_VAR, raising=False)
    with pytest.raises(RealPaymentNotEnabledError):
        AutomatedCreditCardProvider(allow_real_payment=True)


def test_real_payment_blocked_with_env_only(enabled_env: None) -> None:
    with pytest.raises(RealPaymentNotEnabledError):
        AutomatedCreditCardProvider()


def test_real_payment_blocked_when_env_value_is_not_exactly_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(REAL_PAYMENT_ENV_VAR, "true")
    with pytest.raises(RealPaymentNotEnabledError):
        AutomatedCreditCardProvider(allow_real_payment=True)


def test_real_payment_allowed_only_with_both_switches(enabled_env: None) -> None:
    provider = AutomatedCreditCardProvider(allow_real_payment=True)
    assert provider.name == "automated_credit_card"


# ------------------------------------------------------------ Automated


async def test_automated_without_profile_fails_closed(
    enabled_env: None, payment_page: FakePage
) -> None:
    result = await AutomatedCreditCardProvider(allow_real_payment=True).pay(payment_page, None)
    assert result.outcome is PaymentOutcome.FAILED
    assert result.detail["submitted"] is False


async def test_automated_submits_and_reports_submitted(
    enabled_env: None, payment_page: FakePage, profile: CreditCardProfile
) -> None:
    result = await AutomatedCreditCardProvider(allow_real_payment=True).pay(payment_page, profile)
    assert result.outcome is PaymentOutcome.SUBMITTED
    assert any(e.get("id") == "submit-payment" for e in payment_page.clicked_elements)
    assert dict(payment_page.fills)["input#card-number"] == REAL_PAN


async def test_automated_expiry_is_two_digit_year(
    enabled_env: None, payment_page: FakePage, profile: CreditCardProfile
) -> None:
    await AutomatedCreditCardProvider(allow_real_payment=True).pay(payment_page, profile)
    assert dict(payment_page.fills)["input#card-expiry"] == "09/31"


async def test_automated_detail_only_exposes_last_four(
    enabled_env: None, payment_page: FakePage, profile: CreditCardProfile
) -> None:
    telemetry = TimelineRecorder()
    result = await AutomatedCreditCardProvider(
        allow_real_payment=True, telemetry=telemetry
    ).pay(payment_page, profile)
    blob = json.dumps({k: str(v) for k, v in result.detail.items()}, ensure_ascii=False)
    blob += json.dumps([str(dict(e.detail)) for e in telemetry.events()], ensure_ascii=False)
    assert REAL_PAN not in blob
    assert REAL_CODE not in blob
    assert result.detail["card_last4"] == "1234"


async def test_automated_reports_three_ds_from_url(
    enabled_env: None, profile: CreditCardProfile
) -> None:
    page = FakePage.from_fixture("kktix_payment.html", url="https://acs.bank.test/3ds/challenge")
    result = await AutomatedCreditCardProvider(allow_real_payment=True).pay(page, profile)
    assert result.outcome is PaymentOutcome.THREE_DS_REQUIRED


async def test_automated_reports_declined_from_page_text(
    enabled_env: None, profile: CreditCardProfile
) -> None:
    page = FakePage(
        "<div id='paymentApp'><input id='card-number'><input id='card-expiry'>"
        "<input id='card-ccv'><button id='submit-payment'>確認付款</button>"
        "<p>付款失敗，請確認卡片資訊</p></div>"
    )
    result = await AutomatedCreditCardProvider(allow_real_payment=True).pay(page, profile)
    assert result.outcome is PaymentOutcome.DECLINED


async def test_automated_reports_three_ds_failed(
    enabled_env: None, profile: CreditCardProfile
) -> None:
    page = FakePage(
        "<div id='paymentApp'><input id='card-number'><input id='card-expiry'>"
        "<input id='card-ccv'><button id='submit-payment'>確認付款</button>"
        "<p>驗證失敗</p></div>"
    )
    result = await AutomatedCreditCardProvider(allow_real_payment=True).pay(page, profile)
    assert result.outcome is PaymentOutcome.THREE_DS_FAILED


async def test_automated_reports_timeout(
    enabled_env: None, payment_page: FakePage, profile: CreditCardProfile
) -> None:
    payment_page.fail_load_state = True
    result = await AutomatedCreditCardProvider(allow_real_payment=True).pay(payment_page, profile)
    assert result.outcome is PaymentOutcome.TIMEOUT
    assert result.detail["card_last4"] == "1234"


async def test_automated_missing_card_field_fails_closed(
    enabled_env: None, profile: CreditCardProfile
) -> None:
    page = FakePage("<div id='paymentApp'><button id='submit-payment'>確認付款</button></div>")
    result = await AutomatedCreditCardProvider(allow_real_payment=True, timeout_ms=1).pay(page, profile)
    assert result.outcome is PaymentOutcome.FAILED
    assert str(result.detail["reason"]).startswith("field_not_found")


async def test_automated_missing_confirm_button_fails_closed(
    enabled_env: None, profile: CreditCardProfile
) -> None:
    page = FakePage(
        "<div id='paymentApp'><input id='card-number'><input id='card-expiry'><input id='card-ccv'></div>"
    )
    result = await AutomatedCreditCardProvider(allow_real_payment=True, timeout_ms=1).pay(page, profile)
    assert result.detail["reason"] == "confirm_button_not_found"


def test_masked_last4_handles_missing_profile() -> None:
    assert masked_last4(None) == "none"


def test_payment_result_submitted_property() -> None:
    assert PaymentResult(PaymentOutcome.SUBMITTED, "x").submitted is True
    assert PaymentResult(PaymentOutcome.CHECKPOINT_REACHED, "x").submitted is False
