"""拓元 (Tixcraft) 活動解析器。

拓元只有 `/activity` 列表頁沒有人機驗證（走 Varnish 快取）；節目介紹頁與場次頁
一律先回 401 的 JS 驗證頁，純 HTTP 永遠拿不到內容，得由 Worker 用真瀏覽器補。
因此這裡的分工是：

* 列表頁 → 標題、場地、演出日期、是否在最新開賣頁籤（線上即可取得）。
* 節目介紹頁 → 活動說明、主辦單位、場次與購票網址（要瀏覽器，見
  `worker/handlers/hydrate.py`）。

任何情況下都不得用「拓元活動 <代號>」這類佔位字串當標題——寧可讓解析失敗往外
拋，也不要把假資料寫進資料庫。
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

import httpx
from rapidfuzz import fuzz

from adapters.ticketing.base import EventResolver, ResolveError
from adapters.ticketing.tixcraft.pages import (
    ActivityDetail,
    ActivityListing,
    SessionSaleState,
    detail_url,
    game_url,
    parse_activity_detail,
    parse_activity_list,
    slug_of,
)
from domain.event import (
    Event,
    EventCandidate,
    EventStatus,
    PlatformEnum,
    ResolveResult,
)

DEFAULT_BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
}

#: 全站活動列表對每次搜尋都一樣，短時間內重抓只是浪費上游頻寬。
LISTING_TTL_S = 120.0
_listing_cache: tuple[float, list[ActivityListing]] | None = None

#: 節目介紹頁被人機驗證擋掉時，連續重試只會讓整段 IP 被加重封鎖；連錯幾次就
#: 停手一段時間，改由 Worker 的瀏覽器補資料。
DETAIL_FAILURE_THRESHOLD = 3
DETAIL_BACKOFF_S = 600.0
_detail_failures = 0
_detail_blocked_until = 0.0

#: 驗證頁的特徵字串；和 `worker/handlers/hydrate.py` 認的是同一組。
CHALLENGE_MARKERS = (
    "Let's Get Your Identity Verified",
    "Your Browsing Activity Has Been Paused",
    '{"response":"identify"}',
)


def clear_listing_cache() -> None:
    """清掉活動列表與詳情熔斷器的快取；測試之間必須互不影響。"""
    global _listing_cache, _detail_failures, _detail_blocked_until
    _listing_cache = None
    _detail_failures = 0
    _detail_blocked_until = 0.0


def _detail_fetch_allowed() -> bool:
    return time.monotonic() >= _detail_blocked_until


def _note_detail_failure() -> None:
    global _detail_failures, _detail_blocked_until
    _detail_failures += 1
    if _detail_failures >= DETAIL_FAILURE_THRESHOLD:
        _detail_blocked_until = time.monotonic() + DETAIL_BACKOFF_S
        _detail_failures = 0


def _note_detail_success() -> None:
    global _detail_failures, _detail_blocked_until
    _detail_failures = 0
    _detail_blocked_until = 0.0


class TixcraftResolveError(ResolveError):
    """拓元活動解析錯誤。"""


class TixcraftParseError(TixcraftResolveError):
    """拓元頁面資料解析錯誤。"""


def _status_from_detail(detail: ActivityDetail) -> EventStatus:
    """售票狀態只從場次表推，而且推不出來就說推不出來。

    2026-09-22 抓全站 73 個活動比對過：拿列表頁的「最新開賣」頁籤當販售中，
    其中 18 個會判錯——14 個按得下「立即訂購」的活動被標成尚未開賣，4 個標成
    熱賣中的其實買不到（含 2 個已售完）。頁籤是陳列方式，不是售票狀態。
    """
    if not detail.sessions_seen:
        return EventStatus.UNKNOWN
    if not detail.sessions:
        # 場次表在，但寫著「目前無場次資訊」——場次還沒排上去，也就還不能買。
        return EventStatus.ANNOUNCED

    states = {s.state for s in detail.sessions}
    if SessionSaleState.ON_SALE in states:
        return EventStatus.ON_SALE
    if states & {SessionSaleState.SOLD_OUT, SessionSaleState.ZONE_EMPTY}:
        # 沒有任何一場買得到，而且至少一場明確售完：整場活動就是售完。
        return EventStatus.SOLD_OUT
    if states == {SessionSaleState.DEADLINE_PASSED}:
        return EventStatus.CLOSED
    return EventStatus.UNKNOWN


def build_event(
    slug: str,
    *,
    listing: ActivityListing | None = None,
    detail: ActivityDetail | None = None,
) -> Event:
    """把列表資料與（可選的）節目介紹資料合成一個 Event。"""
    title = (detail.title if detail else None) or (listing.title if listing else None)
    if not title:
        raise TixcraftParseError(f"拓元活動 {slug} 沒有解析出標題")

    venue = (detail.venue if detail else None) or (listing.venue if listing else None)
    event_start_at = (detail.event_start_at if detail else None) or (
        listing.event_start_at if listing else None
    )

    raw_metadata: dict[str, Any] = {
        "detail_source": "tixcraft_detail_page" if detail else "tixcraft_activity_list"
    }
    # 活動說明與主辦單位只存在於要瀏覽器才打得開的節目介紹頁。這個旗標得明確
    # 寫 False，否則合併舊 metadata 時會把上一輪的 True 留下來，補完了還顯示缺資料。
    raw_metadata["needs_browser_detail"] = detail is None
    if venue:
        raw_metadata["venue"] = venue
    if listing is not None and listing.image_url:
        raw_metadata["image_url"] = listing.image_url
    if listing is not None and listing.date_text:
        raw_metadata["date_text"] = listing.date_text
    if listing is not None and listing.tabs:
        # 頁籤留著當展示用的線索（近期演出／最新開賣），但不參與售票狀態判定。
        raw_metadata["listing_tabs"] = list(listing.tabs)
    if detail is not None:
        if detail.description:
            raw_metadata["description"] = detail.description
        if detail.organizer:
            raw_metadata["organizer_display"] = detail.organizer
        if detail.sessions:
            raw_metadata["sessions"] = [s.to_dict() for s in detail.sessions]
        if detail.sale_start_at is not None:
            # 開賣時間是從主辦寫的公告文字讀回來的，不是拓元給的欄位。把出處和
            # 原文一起留著，畫面上才講得出「這個時間是哪一句話讀來的」，讀錯也
            # 查得到是哪一段。
            raw_metadata["sale_start_source"] = "tixcraft_intro_text"
            if detail.sale_start_text:
                raw_metadata["sale_start_text"] = detail.sale_start_text

    return Event(
        id=Event.make_id(PlatformEnum.TIXCRAFT, "tixcraft", slug),
        platform=PlatformEnum.TIXCRAFT,
        organizer="tixcraft",
        event_slug=slug,
        title=title,
        canonical_url=detail_url(slug),
        status=(
            _status_from_detail(detail) if detail is not None else EventStatus.UNKNOWN
        ),
        sale_start_at=detail.sale_start_at if detail is not None else None,
        event_start_at=event_start_at,
        ticket_types=[],
        raw_metadata=raw_metadata,
    )


class TixcraftEventResolver(EventResolver):
    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        threshold: float = 0.6,
        base_url: str = "https://tixcraft.com",
        **kwargs: Any,
    ) -> None:
        self._client = client
        self._threshold = threshold
        self._base_url = base_url
        self._kwargs = kwargs

    def _get_client(self) -> httpx.AsyncClient:
        return self._client if self._client is not None else httpx.AsyncClient()

    async def _load_listings(self) -> list[ActivityListing]:
        global _listing_cache
        now = time.monotonic()
        if _listing_cache is not None and now - _listing_cache[0] < LISTING_TTL_S:
            return _listing_cache[1]

        client = self._get_client()
        url = f"{self._base_url.rstrip('/')}/activity"
        try:
            resp = await client.get(url, headers=DEFAULT_BROWSER_HEADERS, timeout=15.0)
            resp.raise_for_status()
        except Exception as exc:
            raise TixcraftResolveError(
                f"Failed to fetch activity list from {url}: {exc}"
            ) from exc

        listings = parse_activity_list(resp.text)
        _listing_cache = (now, listings)
        return listings

    async def listing_for(self, slug: str) -> ActivityListing | None:
        with contextlib.suppress(Exception):
            for listing in await self._load_listings():
                if listing.slug == slug:
                    return listing
        return None

    async def search(self, query: str, *, limit: int = 10) -> list[EventCandidate]:
        listings = await self._load_listings()
        candidates: list[EventCandidate] = []
        for listing in listings:
            score = (
                fuzz.partial_ratio(query.lower(), listing.title.lower()) / 100.0
                if query
                else 1.0
            )
            candidates.append(
                EventCandidate(
                    title=listing.title,
                    url=listing.url,
                    organizer="tixcraft",
                    score=score,
                    published=listing.event_start_at,
                    summary=None,
                    raw={
                        "venue": listing.venue,
                        "date_text": listing.date_text,
                        "event_start_at": (
                            listing.event_start_at.isoformat()
                            if listing.event_start_at
                            else None
                        ),
                        # 列表頁看不出售不售得到票，硬給一個狀態只會騙人；
                        # 真正的狀態等 Worker 用瀏覽器抓場次頁回來補。
                        "status": EventStatus.UNKNOWN.value,
                        "listing_tabs": list(listing.tabs),
                        "image_url": listing.image_url,
                        "needs_browser_detail": True,
                    },
                )
            )
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[:limit]

    async def fetch_event_metadata(self, event_url: str) -> Event:
        slug = slug_of(event_url)
        if not slug:
            raise TixcraftResolveError(f"無法從 {event_url} 取出拓元活動代號")

        listing = await self.listing_for(slug)
        detail = await self._try_http_detail(slug)
        if detail is None and listing is None:
            raise TixcraftResolveError(
                f"拓元活動 {slug} 既不在活動列表也讀不到節目介紹頁"
            )
        return build_event(slug, listing=listing, detail=detail)

    async def _try_http_detail(self, slug: str) -> ActivityDetail | None:
        """節目介紹頁在多數情況下會被人機驗證擋掉；擋掉就回 None，不製造假資料。"""
        if not _detail_fetch_allowed():
            return None
        client = self._get_client()
        try:
            resp = await client.get(
                detail_url(slug), headers=DEFAULT_BROWSER_HEADERS, timeout=10.0
            )
        except Exception:
            _note_detail_failure()
            return None
        if resp.status_code != 200 or any(
            marker in resp.text for marker in CHALLENGE_MARKERS
        ):
            _note_detail_failure()
            return None

        detail = parse_activity_detail(resp.text)
        if not detail.title:
            _note_detail_failure()
            return None
        _note_detail_success()
        if not detail.sessions:
            with contextlib.suppress(Exception):
                game_resp = await client.get(
                    game_url(slug), headers=DEFAULT_BROWSER_HEADERS, timeout=10.0
                )
                if game_resp.status_code == 200:
                    detail = parse_activity_detail(
                        resp.text, game_html=game_resp.text
                    )
        return detail

    async def resolve(self, query: str) -> ResolveResult:
        if query.startswith(("http://", "https://")):
            try:
                ev = await self.fetch_event_metadata(query)
            except Exception:
                return ResolveResult(
                    query=query,
                    auto_selected=False,
                    event=None,
                    candidates=[],
                    threshold=self._threshold,
                )
            return ResolveResult(
                query=query,
                auto_selected=True,
                event=ev,
                candidates=[
                    EventCandidate(
                        title=ev.title,
                        url=ev.canonical_url,
                        organizer=ev.organizer,
                        score=1.0,
                    )
                ],
                threshold=self._threshold,
            )

        candidates = await self.search(query, limit=5)
        top = candidates[0] if candidates else None
        if top is not None and top.score >= self._threshold:
            with contextlib.suppress(Exception):
                ev = await self.fetch_event_metadata(top.url)
                return ResolveResult(
                    query=query,
                    auto_selected=True,
                    event=ev,
                    candidates=candidates,
                    threshold=self._threshold,
                )

        return ResolveResult(
            query=query,
            auto_selected=False,
            event=None,
            candidates=candidates,
            threshold=self._threshold,
        )
