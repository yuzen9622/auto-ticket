"""座位策略決策（純函式）。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from domain.preference import SeatPreference

DOWNGRADE_MARK = "seat_strategy_downgraded"


class SeatAction(str, Enum):
    BEST_AVAILABLE = "BEST_AVAILABLE"
    """電腦配位（KKTIXSelectors.BTN_BEST_AVAILABLE）。"""
    PICK_SEAT = "PICK_SEAT"
    """自行選位（KKTIXSelectors.BTN_PICK_SEAT）。"""


@dataclass(frozen=True, slots=True)
class SeatDecision:
    action: SeatAction
    downgraded: bool
    reason: str


def decide_seat_action(preference: SeatPreference) -> SeatDecision:
    """把座位偏好對應到兩個可自動化的分支。

    `specific_zone` 的座位圖點選互動尚未實作，一律降級為 `PICK_SEAT` 並標記
    `downgraded=True`，由呼叫端記一筆 `seat_strategy_downgraded` mark——
    誠實回報未支援，不假裝支援。
    """
    if preference.strategy == "best_available":
        return SeatDecision(SeatAction.BEST_AVAILABLE, False, "best_available")
    if preference.strategy == "specific_zone":
        return SeatDecision(
            SeatAction.PICK_SEAT,
            True,
            f"specific_zone downgraded to pick_seat (zones={list(preference.preferred_zones)})",
        )
    return SeatDecision(SeatAction.PICK_SEAT, False, preference.strategy)
