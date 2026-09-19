from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from domain.event import (
    Event,
    EventCandidate,
    EventStatus,
    PlatformEnum,
    ResolveResult,
    TicketType,
    TicketTypeStatus,
)
from domain.preference import SeatPreference, TicketPreference, TicketPriority
from domain.task import (
    CreditCardProfile,
    PaymentMethod,
    PurchaseTaskRecord,
    PurchaseTaskSpec,
    UserContactProfile,
    VerificationRule,
)
from domain.types import DomainBaseModel

SALE_START = datetime(2026, 1, 10, 4, 0, tzinfo=timezone.utc)


def make_contact() -> UserContactProfile:
    return UserContactProfile(
        name="王小明", phone="0912-345-678", email="ming@example.com"
    )


def make_card() -> CreditCardProfile:
    return CreditCardProfile(
        card_number="4111111111111234",
        expiry_month="09",
        expiry_year="29",
        cvv="987",
        cardholder_name="WANG XIAO MING",
    )


def make_preference(quantity: int = 2) -> TicketPreference:
    return TicketPreference(
        quantity=quantity,
        priorities=[TicketPriority(price=2800, priority=1)],
    )


def make_spec(**overrides: object) -> PurchaseTaskSpec:
    payload: dict[str, object] = {
        "task_id": "task_0001",
        "event_title": "Atarayo Asia Tour 2026 — Taipei",
        "event_url": "https://atarayo.kktix.cc/events/atarayo-taipei-2026",
        "sale_start_at": SALE_START,
        "ticket_preference": make_preference(),
        "contact_profile": make_contact(),
        "payment_method": PaymentMethod.CREDIT_CARD,
        "payment_profile": make_card(),
    }
    payload.update(overrides)
    return PurchaseTaskSpec(**payload)  # type: ignore[arg-type]


@pytest.mark.parametrize("quantity", [1, 10])
def test_quantity_bounds_accepted(quantity: int) -> None:
    assert make_preference(quantity).quantity == quantity


@pytest.mark.parametrize("quantity", [0, 11])
def test_quantity_bounds_rejected(quantity: int) -> None:
    with pytest.raises(ValidationError):
        make_preference(quantity)


def test_priorities_must_not_be_empty() -> None:
    with pytest.raises(ValidationError):
        TicketPreference(quantity=2, priorities=[])


def test_sorted_priorities_order() -> None:
    preference = TicketPreference(
        priorities=[
            TicketPriority(price=1800, priority=2),
            TicketPriority(price=3800, priority=1),
            TicketPriority(price=2800, priority=2),
        ]
    )
    assert [(p.priority, p.price) for p in preference.sorted_priorities] == [
        (1, 3800),
        (2, 2800),
        (2, 1800),
    ]


def test_seat_preference_defaults() -> None:
    seat = SeatPreference()
    assert seat.adjacent is True
    assert seat.strategy == "best_available"
    assert seat.preferred_zones == []


def test_credit_card_payment_requires_profile() -> None:
    with pytest.raises(ValidationError):
        make_spec(payment_profile=None)


def test_mock_payment_needs_no_profile() -> None:
    spec = make_spec(payment_method=PaymentMethod.MOCK, payment_profile=None)
    assert spec.payment_profile is None


def test_to_persistable_dict_excludes_card() -> None:
    spec = make_spec()
    persisted = spec.to_persistable_dict()
    serialized = json.dumps(persisted, ensure_ascii=False)
    assert "payment_profile" not in persisted
    assert "card_number" not in serialized
    assert "cvv" not in serialized
    assert "4111111111111234" not in serialized
    assert "987" not in serialized
    assert persisted["task_id"] == "task_0001"


def test_credit_card_repr_masked() -> None:
    card = make_card()
    assert repr(card) == "CreditCardProfile(card_number='****1234')"
    assert "4111111111111234" not in repr(card)
    assert "987" not in repr(card)


@pytest.mark.parametrize("email", ["ming@example.com", "a.b+c@sub.domain.tw"])
def test_contact_email_valid(email: str) -> None:
    assert UserContactProfile(name="A", phone="0912345678", email=email).email == email


@pytest.mark.parametrize(
    "email", ["ming.example.com", "ming@", "@example.com", "a b@c.tw"]
)
def test_contact_email_invalid(email: str) -> None:
    with pytest.raises(ValidationError):
        UserContactProfile(name="A", phone="0912345678", email=email)


@pytest.mark.parametrize("phone", ["0912345678", "+886 912 345 678", "(02)2345-6789"])
def test_contact_phone_valid(phone: str) -> None:
    assert UserContactProfile(name="A", phone=phone, email="a@b.tw").phone == phone


@pytest.mark.parametrize("phone", ["0912", "abcdefghij", ""])
def test_contact_phone_invalid(phone: str) -> None:
    with pytest.raises(ValidationError):
        UserContactProfile(name="A", phone=phone, email="a@b.tw")


def test_event_make_id_is_deterministic() -> None:
    first = Event.make_id(PlatformEnum.KKTIX, "atarayo", "atarayo-taipei-2026")
    second = Event.make_id(PlatformEnum.KKTIX, "atarayo", "atarayo-taipei-2026")
    other = Event.make_id(PlatformEnum.KKTIX, "atarayo", "atarayo-taipei-2027")
    assert first == second
    assert first != other
    assert re.fullmatch(r"[0-9a-f]{16}", first)


def test_ticket_type_make_id_is_deterministic() -> None:
    event_id = Event.make_id(PlatformEnum.KKTIX, "atarayo", "atarayo-taipei-2026")
    first = TicketType.make_id(event_id, "預售全區站席")
    second = TicketType.make_id(event_id, "預售全區站席")
    other = TicketType.make_id(event_id, "搖滾區站席")
    assert first == second
    assert first != other
    assert re.fullmatch(r"[0-9a-f]{16}", first)


@pytest.mark.parametrize("score", [-0.1, 1.1])
def test_event_candidate_score_range(score: float) -> None:
    with pytest.raises(ValidationError):
        EventCandidate(title="t", url="https://x.kktix.cc/events/y", score=score)


def test_resolve_result_requires_threshold() -> None:
    with pytest.raises(ValidationError):
        ResolveResult(  # type: ignore[call-arg]
            query="q", auto_selected=False, event=None, candidates=[]
        )


def test_event_defaults_status_unknown() -> None:
    event = make_event()
    assert event.status is EventStatus.UNKNOWN
    assert event.sale_start_at is None
    assert event.event_start_at is None
    assert event.ticket_types == []


def make_event() -> Event:
    return Event(
        id="ev_x",
        platform=PlatformEnum.KKTIX,
        organizer="atarayo",
        event_slug="atarayo-taipei-2026",
        title="t",
        canonical_url="https://atarayo.kktix.cc/events/atarayo-taipei-2026",
    )


def make_candidate() -> EventCandidate:
    return EventCandidate(
        title="t", url="https://atarayo.kktix.cc/events/atarayo-taipei-2026", score=0.5
    )


def make_ticket_type() -> TicketType:
    return TicketType(
        id="tt_x",
        event_id="ev_x",
        name="預售全區站席",
        price=2800,
        status=TicketTypeStatus.AVAILABLE,
    )


DOMAIN_ENTITIES = [
    Event,
    TicketType,
    EventCandidate,
    ResolveResult,
    TicketPriority,
    SeatPreference,
    TicketPreference,
    UserContactProfile,
    CreditCardProfile,
    PurchaseTaskSpec,
    PurchaseTaskRecord,
]


@pytest.mark.parametrize("model", DOMAIN_ENTITIES, ids=lambda m: m.__name__)
def test_every_domain_entity_enforces_assignment_and_forbids_extra(
    model: type[DomainBaseModel],
) -> None:
    assert issubclass(model, DomainBaseModel)
    assert model.model_config.get("validate_assignment") is True
    assert model.model_config.get("extra") == "forbid"


def test_post_init_assignment_rejects_out_of_range_quantity() -> None:
    preference = make_preference()
    with pytest.raises(ValidationError):
        preference.quantity = 0
    with pytest.raises(ValidationError):
        preference.quantity = 11
    assert preference.quantity == 2


def test_post_init_assignment_rejects_empty_priorities() -> None:
    preference = make_preference()
    with pytest.raises(ValidationError):
        preference.priorities = []
    assert len(preference.priorities) == 1


def test_post_init_assignment_rejects_naive_datetime() -> None:
    event = make_event()
    with pytest.raises(ValidationError):
        event.sale_start_at = datetime(2026, 1, 10, 12, 0)
    with pytest.raises(ValidationError):
        event.event_start_at = datetime(2026, 3, 14, 19, 30)
    assert event.sale_start_at is None
    assert event.event_start_at is None


def test_post_init_assignment_normalizes_aware_datetime_to_utc() -> None:
    event = make_event()
    event.sale_start_at = datetime(
        2026, 1, 10, 12, 0, tzinfo=timezone(timedelta(hours=8))
    )
    assert event.sale_start_at == SALE_START
    assert event.sale_start_at.tzinfo is timezone.utc


def test_post_init_assignment_rejects_negative_price() -> None:
    ticket = make_ticket_type()
    with pytest.raises(ValidationError):
        ticket.price = -1
    assert ticket.price == 2800


def test_post_init_assignment_rejects_out_of_range_score() -> None:
    candidate = make_candidate()
    with pytest.raises(ValidationError):
        candidate.score = 1.5
    with pytest.raises(ValidationError):
        candidate.score = -0.1
    assert candidate.score == 0.5


def test_purchase_task_spec_is_frozen_against_direct_mutation() -> None:
    spec = make_spec()
    original_card = spec.payment_profile
    with pytest.raises(ValidationError):
        spec.payment_profile = None
    assert spec.payment_profile is original_card
    assert spec.payment_profile is not None


def test_purchase_task_spec_copy_update_validates_invariants() -> None:
    spec = make_spec()
    original_card = spec.payment_profile
    with pytest.raises(ValidationError):
        spec.model_copy(update={"payment_profile": None})
    assert spec.payment_profile is original_card
    assert spec.payment_profile is not None
    assert spec.payment_profile.card_number == "4111111111111234"


def test_post_init_assignment_rejects_negative_max_retries() -> None:
    spec = make_spec()
    with pytest.raises(ValidationError):
        spec.max_retries = -1
    with pytest.raises(ValidationError):
        spec.timeout_seconds = 0
    assert spec.max_retries == 3
    assert spec.timeout_seconds == 120


def test_post_init_assignment_rejects_invalid_contact_and_card() -> None:
    contact = make_contact()
    with pytest.raises(ValidationError):
        contact.email = "not-an-email"
    card = make_card()
    with pytest.raises(ValidationError):
        card.cvv = "12"
    assert contact.email == "ming@example.com"
    assert card.cvv == "987"


def test_construction_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        TicketPriority.model_validate({"price": 2800, "priority": 1, "unexpected": "x"})


def test_assignment_to_unknown_field_is_rejected() -> None:
    preference = make_preference()
    unknown_field = "unexpected"
    with pytest.raises(ValueError):
        setattr(preference, unknown_field, "x")


def test_new_optional_spec_fields_default_to_off() -> None:
    spec = make_spec()
    assert spec.verification_rules == ()
    assert spec.auto_login is False
    assert spec.qualification_code is None


def test_verification_rules_round_trip_through_json() -> None:
    spec = make_spec(
        payment_method=PaymentMethod.MOCK,
        payment_profile=None,
        verification_rules=[
            {"pattern": "主辦單位", "answer": "KKTIX"},
            {"pattern": r"^\d+ \+ \d+", "answer": "4", "is_regex": True},
        ],
        auto_login=True,
        qualification_code="VIP-2026",
    )
    assert [r.answer for r in spec.verification_rules] == ["KKTIX", "4"]
    assert spec.verification_rules[1].is_regex is True

    restored = PurchaseTaskSpec.model_validate(
        json.loads(json.dumps(spec.to_persistable_dict()))
    )
    assert restored.verification_rules == spec.verification_rules
    assert restored.auto_login is True
    assert restored.qualification_code == "VIP-2026"


def test_verification_rule_rejects_empty_pattern_or_answer() -> None:
    with pytest.raises(ValidationError):
        VerificationRule(pattern="", answer="A")
    with pytest.raises(ValidationError):
        VerificationRule(pattern="x", answer="")


def test_spec_never_accepts_a_credential_field() -> None:
    for field_name in ("password", "user_password", "secret_token"):
        with pytest.raises(ValidationError):
            make_spec(**{field_name: "hunter2"})
