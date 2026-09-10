from __future__ import annotations

import pytest

from domain.preference import SeatPreference
from strategy.seat_strategy import DOWNGRADE_MARK, SeatAction, decide_seat_action
from tests.netguard import netguard_autouse  # noqa: F401


def test_best_available_maps_to_auto_assignment() -> None:
    decision = decide_seat_action(SeatPreference(strategy="best_available"))
    assert decision.action is SeatAction.BEST_AVAILABLE
    assert decision.downgraded is False


def test_same_zone_maps_to_pick_seat_without_downgrade() -> None:
    decision = decide_seat_action(SeatPreference(strategy="same_zone"))
    assert decision.action is SeatAction.PICK_SEAT
    assert decision.downgraded is False


def test_specific_zone_downgrades_to_pick_seat() -> None:
    decision = decide_seat_action(
        SeatPreference(strategy="specific_zone", preferred_zones=["A1", "A2"])
    )
    assert decision.action is SeatAction.PICK_SEAT
    assert decision.downgraded is True


def test_downgrade_reason_names_the_requested_zones() -> None:
    decision = decide_seat_action(
        SeatPreference(strategy="specific_zone", preferred_zones=["VIP"])
    )
    assert "VIP" in decision.reason
    assert "specific_zone" in decision.reason


def test_downgrade_mark_name_is_stable() -> None:
    assert DOWNGRADE_MARK == "seat_strategy_downgraded"


def test_decision_is_immutable() -> None:
    decision = decide_seat_action(SeatPreference())
    with pytest.raises(Exception):
        decision.downgraded = True  # type: ignore[misc]


@pytest.mark.parametrize(
    ("strategy", "expected"),
    [
        ("best_available", SeatAction.BEST_AVAILABLE),
        ("same_zone", SeatAction.PICK_SEAT),
        ("specific_zone", SeatAction.PICK_SEAT),
    ],
)
def test_all_declared_strategies_are_mapped(strategy: str, expected: SeatAction) -> None:
    assert decide_seat_action(SeatPreference(strategy=strategy)).action is expected  # type: ignore[arg-type]
