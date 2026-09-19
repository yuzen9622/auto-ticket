from __future__ import annotations

import pytest

from domain.preference import SeatPreference, TicketPreference, TicketPriority
from strategy.ticket_strategy import (
    EXCLUDED,
    INSUFFICIENT_REMAINING,
    INVALID_PATTERN,
    NO_NAME_MATCH,
    NO_PRICE_MATCH,
    UNAVAILABLE,
    TicketOption,
    decide_ticket,
    is_selectable,
)
from tests.netguard import netguard_autouse  # noqa: F401


def opt(index: int, name: str, price: int, *, available: bool = True, remaining: int | None = None) -> TicketOption:
    return TicketOption(
        index=index, name=name, price=price, available=available,
        remaining=remaining, status_text=f"{name}/{price}",
    )


def pref(*priorities: TicketPriority, quantity: int = 2, fallback: bool = False) -> TicketPreference:
    return TicketPreference(
        quantity=quantity,
        priorities=list(priorities),
        seat_preference=SeatPreference(),
        fallback_to_any=fallback,
    )


def test_selects_first_priority_when_price_matches() -> None:
    decision = decide_ticket([opt(0, "A", 3200), opt(1, "B", 2400)], pref(TicketPriority(price=3200)))
    assert decision.status == "SELECTED"
    assert decision.option is not None and decision.option.index == 0
    assert decision.fallback_used is False


def test_quantity_comes_from_preference() -> None:
    decision = decide_ticket([opt(0, "A", 3200)], pref(TicketPriority(price=3200), quantity=4))
    assert decision.quantity == 4


def test_matched_priority_is_reported() -> None:
    first = TicketPriority(price=2400, priority=1)
    second = TicketPriority(price=3200, priority=2)
    decision = decide_ticket([opt(0, "A", 3200), opt(1, "B", 2400)], pref(second, first))
    assert decision.matched_priority is first


def test_priority_order_follows_sorted_priorities() -> None:
    cheap = TicketPriority(price=2400, priority=2)
    pricey = TicketPriority(price=3200, priority=1)
    decision = decide_ticket([opt(0, "A", 3200), opt(1, "B", 2400)], pref(cheap, pricey))
    assert decision.option is not None and decision.option.price == 3200


def test_skips_sold_out_priority_and_falls_to_next() -> None:
    decision = decide_ticket(
        [opt(0, "A", 3200, available=False), opt(1, "B", 2400)],
        pref(TicketPriority(price=3200, priority=1), TicketPriority(price=2400, priority=2)),
    )
    assert decision.option is not None and decision.option.price == 2400
    assert UNAVAILABLE in decision.trace[0]


def test_name_pattern_must_also_match() -> None:
    decision = decide_ticket(
        [opt(0, "全票 A 區", 3200), opt(1, "全票 B 區", 3200)],
        pref(TicketPriority(price=3200, ticket_name_pattern="B 區")),
    )
    assert decision.option is not None and decision.option.index == 1


def test_name_pattern_miss_records_no_name_match() -> None:
    decision = decide_ticket(
        [opt(0, "全票 A 區", 3200)],
        pref(TicketPriority(price=3200, ticket_name_pattern="搖滾")),
    )
    assert decision.status == "SOLD_OUT"
    assert NO_NAME_MATCH in decision.trace[0]


def test_name_is_matched_literally_not_as_a_regex() -> None:
    """票種名稱是子字串比對，不是樣式。

    真實票種名常含 `$`、`+`、`(`：當成 regex 編譯時 `$` 會變成行尾錨點，
    名稱永遠配不到自己，整場被誤判成售罄（實際發生過：「A＋$32 手續費」）。
    """
    options = [opt(0, "A＋$32 手續費", 3200)]
    decision = decide_ticket(
        options, pref(TicketPriority(price=3200, ticket_name_pattern="A＋$32 手續費"))
    )
    assert decision.status == "SELECTED"

    # regex 的特殊字元一律按字面處理，不再是語法錯誤。
    literal = decide_ticket(
        [opt(0, "[unclosed] 區", 3200)],
        pref(TicketPriority(price=3200, ticket_name_pattern="[unclosed")),
    )
    assert literal.status == "SELECTED"

    # 配不到就是配不到，照常記錄原因。
    missing = decide_ticket(
        options, pref(TicketPriority(price=3200, ticket_name_pattern="VIP"))
    )
    assert missing.status == "SOLD_OUT"
    assert NO_NAME_MATCH in missing.trace[0]


def test_price_mismatch_records_no_price_match() -> None:
    decision = decide_ticket([opt(0, "A", 3200)], pref(TicketPriority(price=999)))
    assert NO_PRICE_MATCH in decision.trace[0]


def test_remaining_less_than_quantity_is_not_selectable() -> None:
    decision = decide_ticket(
        [opt(0, "A", 3200, remaining=1)], pref(TicketPriority(price=3200), quantity=2)
    )
    assert decision.status == "SOLD_OUT"
    assert INSUFFICIENT_REMAINING in decision.trace[0]


def test_remaining_equal_to_quantity_is_selectable() -> None:
    decision = decide_ticket(
        [opt(0, "A", 3200, remaining=2)], pref(TicketPriority(price=3200), quantity=2)
    )
    assert decision.status == "SELECTED"


def test_unknown_remaining_does_not_block_selection() -> None:
    decision = decide_ticket(
        [opt(0, "A", 3200, remaining=None)], pref(TicketPriority(price=3200), quantity=9)
    )
    assert decision.status == "SELECTED"


def test_fallback_picks_first_available_when_enabled() -> None:
    decision = decide_ticket(
        [opt(0, "A", 3200, available=False), opt(1, "B", 2400)],
        pref(TicketPriority(price=9999), fallback=True),
    )
    assert decision.status == "SELECTED"
    assert decision.fallback_used is True
    assert decision.matched_priority is None
    assert decision.trace[-1].startswith("fallback")


def test_fallback_disabled_returns_sold_out() -> None:
    decision = decide_ticket([opt(0, "B", 2400)], pref(TicketPriority(price=9999)))
    assert decision.status == "SOLD_OUT"
    assert decision.trace[-1] == "fallback -> DISABLED"


def test_fallback_enabled_but_nothing_available() -> None:
    decision = decide_ticket(
        [opt(0, "B", 2400, available=False)], pref(TicketPriority(price=9999), fallback=True)
    )
    assert decision.status == "SOLD_OUT"
    assert decision.trace[-1] == f"fallback -> {UNAVAILABLE}"


def test_empty_option_list_is_sold_out() -> None:
    decision = decide_ticket([], pref(TicketPriority(price=3200), fallback=True))
    assert decision.status == "SOLD_OUT"
    assert decision.option is None


def test_trace_has_one_entry_per_priority_plus_fallback() -> None:
    decision = decide_ticket(
        [opt(0, "A", 100)],
        pref(TicketPriority(price=1, priority=1), TicketPriority(price=2, priority=2)),
    )
    assert len(decision.trace) == 3


def test_trace_stops_at_the_selected_priority() -> None:
    decision = decide_ticket(
        [opt(0, "A", 3200)],
        pref(TicketPriority(price=3200, priority=1), TicketPriority(price=2400, priority=2)),
    )
    assert len(decision.trace) == 1
    assert decision.trace[0].endswith("SELECTED index=0 name=A")


def test_decision_and_option_are_immutable() -> None:
    decision = decide_ticket([opt(0, "A", 3200)], pref(TicketPriority(price=3200)))
    with pytest.raises(Exception):
        decision.status = "SOLD_OUT"  # type: ignore[misc]
    assert decision.option is not None
    with pytest.raises(Exception):
        decision.option.price = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    ("available", "remaining", "quantity", "expected"),
    [
        (True, None, 2, True),
        (True, 5, 2, True),
        (True, 2, 2, True),
        (True, 1, 2, False),
        (False, 99, 1, False),
    ],
)
def test_is_selectable_matrix(available: bool, remaining: int | None, quantity: int, expected: bool) -> None:
    assert is_selectable(opt(0, "A", 100, available=available, remaining=remaining), quantity) is expected


def test_excluded_name_is_skipped_within_a_single_priority() -> None:
    """同一個 priority 下有 A/B 兩個同價票種：A 搶輸被排除後必須選到 B。"""
    decision = decide_ticket(
        [opt(0, "A區", 3200), opt(1, "B區", 3200)],
        pref(TicketPriority(price=3200)),
        excluded_names=("A區",),
    )
    assert decision.status == "SELECTED"
    assert decision.option is not None and decision.option.name == "B區"


def test_excluded_name_falls_through_to_next_priority() -> None:
    decision = decide_ticket(
        [opt(0, "A", 3200), opt(1, "B", 2400)],
        pref(TicketPriority(price=3200, priority=1), TicketPriority(price=2400, priority=2)),
        excluded_names={"A"},
    )
    assert decision.option is not None and decision.option.price == 2400


def test_exclusion_also_applies_to_fallback_to_any() -> None:
    decision = decide_ticket(
        [opt(0, "A", 3200), opt(1, "B", 2400)],
        pref(TicketPriority(price=9999), fallback=True),
        excluded_names=("A", "B"),
    )
    assert decision.status == "SOLD_OUT"
    assert decision.option is None


def test_fallback_to_any_picks_the_first_non_excluded_option() -> None:
    decision = decide_ticket(
        [opt(0, "A", 3200), opt(1, "B", 2400)],
        pref(TicketPriority(price=9999), fallback=True),
        excluded_names=("A",),
    )
    assert decision.status == "SELECTED"
    assert decision.option is not None and decision.option.name == "B"


def test_exclusion_is_recorded_in_the_trace() -> None:
    decision = decide_ticket(
        [opt(0, "A", 3200), opt(1, "B", 3200)],
        pref(TicketPriority(price=3200)),
        excluded_names=("A",),
    )
    assert decision.trace[0].startswith(EXCLUDED)
    assert "remaining_options=1/2" in decision.trace[0]


def test_empty_exclusion_adds_no_trace_noise() -> None:
    decision = decide_ticket([opt(0, "A", 3200)], pref(TicketPriority(price=3200)))
    assert not any(line.startswith(EXCLUDED) for line in decision.trace)


def test_exclusion_matches_by_name_not_by_index() -> None:
    """票種順序在重新讀取後可能改變；排除必須綁名稱，不得綁 index。"""
    decision = decide_ticket(
        [opt(5, "B", 3200), opt(9, "A", 3200)],
        pref(TicketPriority(price=3200)),
        excluded_names=("A",),
    )
    assert decision.option is not None and decision.option.index == 5
