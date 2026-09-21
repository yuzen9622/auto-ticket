"""`TicketPreference` 放寬「至少一筆 priorities」之後的不變式。

改動的理由：拓元與 ibon 的票價要進到票區頁才看得到，開賣前填不出精確價格，
所以 `priorities` 必須可以整組留空、改用 `TicketRule`。真正要守的不變式因此從
「至少一筆精確票種」變成「至少有一種挑得到票的方式」。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from domain.preference import TicketPreference, TicketPriority, TicketRule
from tests.netguard import netguard_autouse  # noqa: F401


def test_rule_alone_is_enough_without_any_exact_price() -> None:
    preference = TicketPreference(quantity=2, rule=TicketRule(max_price=3800))
    assert preference.priorities == []
    assert preference.sorted_priorities == []


def test_fallback_alone_is_enough() -> None:
    assert TicketPreference(quantity=2, fallback_to_any=True).priorities == []


def test_no_way_to_pick_is_rejected() -> None:
    """三種挑票方式都沒有，送出去的就是一個保證買不到票的任務。"""
    with pytest.raises(ValidationError):
        TicketPreference(quantity=2)


def test_assignment_that_removes_the_last_way_to_pick_is_rolled_back() -> None:
    """跨欄位不變式擋下指派時，物件不能被留在沒通過驗證的狀態。"""
    preference = TicketPreference(
        quantity=2, priorities=[TicketPriority(price=2800, priority=1)]
    )
    with pytest.raises(ValidationError):
        preference.priorities = []
    assert len(preference.priorities) == 1


def test_assignment_may_empty_priorities_when_a_rule_remains() -> None:
    preference = TicketPreference(
        quantity=2,
        priorities=[TicketPriority(price=2800, priority=1)],
        rule=TicketRule(max_price=3800),
    )
    preference.priorities = []
    assert preference.priorities == []


def test_price_window_must_not_be_inverted() -> None:
    with pytest.raises(ValidationError):
        TicketRule(min_price=3000, max_price=1000)
