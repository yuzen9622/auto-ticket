"""票況補齊工作的派工與邊界。

售票狀態對外只分「尚未開賣／販售中」，所以這裡沒有售完探測：KKTIX 的購票登記頁與
ibon 的訂購頁都擋掉無頭瀏覽器，只有借使用者本機的 Chrome 才讀得到，不值得為了一個
狀態在背景彈視窗。
"""

from __future__ import annotations

import pytest

from worker.handlers.hydrate import MAX_EVENTS_PER_JOB, parse_requests
from worker.settings import WorkerSettings


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


async def test_status_job_never_launches_a_browser_window(monkeypatch) -> None:
    """補票況不得自己開使用者的 Chrome。

    每搜尋一次就彈出一個瀏覽器視窗完全不成比例；而且自帶的無頭瀏覽器沒裝時，
    每一批都會走到這條退路。沒有現成的瀏覽器可借就放棄，讓票況停在未確認。
    """
    from worker.handlers import hydrate

    launched: list[str] = []

    async def fake_ensure(*args: object, **kwargs: object) -> object:
        launched.append("launched")
        raise AssertionError("補票況不該呼叫 ensure_system_chrome")

    monkeypatch.setattr(
        "browser.system_chrome.ensure_system_chrome", fake_ensure, raising=True
    )
    # 自帶的無頭瀏覽器不存在——這正是會走到退路的情況。
    monkeypatch.setattr(
        hydrate,
        "_headless_pool",
        lambda: _raising_cm("Executable doesn't exist at .../chrome-headless-shell"),
    )
    # 偵錯埠沒有東西在聽。
    async def no_port(endpoint: str, **kwargs: object) -> bool:
        return False

    monkeypatch.setattr(hydrate, "probe_debug_port", no_port)

    class _Db:
        def session(self) -> object:  # pragma: no cover - 不該被用到
            raise AssertionError("沒有補到任何活動就不該寫入資料庫")

    statuses = await hydrate.resolve_statuses(
        [
            hydrate.StatusRequest(
                event_id="a",
                platform="tixcraft",
                slug="26_x",
                canonical_url="https://tixcraft.com/activity/detail/26_x",
            )
        ],
        db=_Db(),
        settings=WorkerSettings(),
    )

    assert statuses == {}
    assert launched == []


def _raising_cm(message: str):
    import contextlib

    @contextlib.asynccontextmanager
    async def cm():
        raise RuntimeError(message)
        yield  # pragma: no cover

    return cm()
