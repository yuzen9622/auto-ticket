"""售完判定：KKTIX 購票登記頁與 ibon 訂購頁。

fixture 是 2026-09-22 用真瀏覽器抓下來的實際頁面剪出來的。這兩頁純 HTTP 都打不開
（KKTIX 回 403、ibon 回 403），而且一個是 AngularJS、一個是 ASP.NET 的座位圖，
手寫 fixture 只會讓測試對著不存在的 DOM 通過。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from adapters.ticketing.ibon.pages import (
    parse_zone_availability,
    zones_are_sold_out,
)
from adapters.ticketing.kktix.pages import (
    parse_registration_tickets,
    registration_is_sold_out,
)
from worker.handlers.hydrate import MAX_EVENTS_PER_JOB, parse_requests

FIXTURES = Path(__file__).parent.parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_kktix_registration_page_reports_sold_out() -> None:
    tickets = parse_registration_tickets(
        load_fixture("kktix_registration_sold_out_page.html")
    )
    assert [t.name for t in tickets] == ["加購福利券"]
    assert tickets[0].selectable is False
    assert registration_is_sold_out(tickets) is True


def test_kktix_registration_page_reports_still_on_sale() -> None:
    """還買得到的票種一定有張數輸入框——這比字樣可靠，字樣主辦可以自己改。"""
    tickets = parse_registration_tickets(
        load_fixture("kktix_registration_available_page.html")
    )
    assert len(tickets) == 2
    assert all(t.selectable for t in tickets)
    assert registration_is_sold_out(tickets) is False


def test_kktix_sold_out_is_unknown_when_no_ticket_parsed() -> None:
    """一個票種都沒解析到是「不知道」，不是售完。"""
    assert registration_is_sold_out([]) is None


def test_ibon_zone_map_reports_sold_out() -> None:
    zones = parse_zone_availability(load_fixture("ibon_zone_map_sold_out.html"))
    assert sorted(z.name for z in zones) == ["包子", "培根"]
    assert all(z.remaining == 0 for z in zones)
    assert zones_are_sold_out(zones) is True


def test_ibon_zone_map_reports_remaining_seats() -> None:
    zones = parse_zone_availability(load_fixture("ibon_zone_map_available.html"))
    assert len(zones) > 1
    assert zones_are_sold_out(zones) is False
    # 「熱賣中」是「還很多」，不是售完；解析不出數字時剩餘張數留空而不是填 0。
    plenty = [z for z in zones if z.remaining_text == "熱賣中"]
    assert plenty and all(z.remaining is None for z in plenty)
    assert all(not z.sold_out for z in plenty)


def test_ibon_sold_out_is_unknown_when_no_zone_parsed() -> None:
    assert zones_are_sold_out([]) is None


def test_status_job_payload_accepts_a_batch() -> None:
    """一張 job 帶一整批活動——一場一張的話 Worker 會排成十幾輪。"""
    requests = parse_requests(
        {
            "events": [
                {
                    "event_id": "a",
                    "platform": "tixcraft",
                    "slug": "26_x",
                    "canonical_url": "https://tixcraft.com/activity/detail/26_x",
                },
                {
                    "event_id": "b",
                    "platform": "kktix",
                    "slug": "y",
                    "canonical_url": "https://org.kktix.cc/events/y",
                },
                # 重複與缺欄位的都該被丟掉。
                {"event_id": "a", "platform": "tixcraft"},
                {"platform": "ibon"},
            ]
        }
    )
    assert [r.event_id for r in requests] == ["a", "b"]
    assert [r.platform for r in requests] == ["tixcraft", "kktix"]


def test_status_job_payload_still_accepts_the_single_event_form() -> None:
    requests = parse_requests(
        {
            "event_id": "a",
            "platform": "tixcraft",
            "slug": "26_x",
            "canonical_url": "https://tixcraft.com/activity/detail/26_x",
        }
    )
    assert len(requests) == 1
    assert requests[0].slug == "26_x"


@pytest.mark.parametrize("count", [MAX_EVENTS_PER_JOB + 5])
def test_status_job_payload_is_capped(count: int) -> None:
    requests = parse_requests(
        {
            "events": [
                {
                    "event_id": f"ev{i}",
                    "platform": "kktix",
                    "slug": f"s{i}",
                    "canonical_url": f"https://org.kktix.cc/events/s{i}",
                }
                for i in range(count)
            ]
        }
    )
    assert len(requests) == MAX_EVENTS_PER_JOB
