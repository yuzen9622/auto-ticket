"""拓元 (Tixcraft) 活動解析器。"""

from __future__ import annotations

import contextlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

from adapters.ticketing.base import EventResolver, ResolveError
from domain.event import (
    Event,
    EventCandidate,
    EventStatus,
    PlatformEnum,
    ResolveResult,
    TicketType,
    TicketTypeStatus,
)

DEFAULT_BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
}

KNOWN_TIXCRAFT_PROMOTERS = (
    ("相信音樂", "相信音樂國際股份有限公司"),
    ("必應創造", "必應創造股份有限公司"),
    ("超級圓頂", "超級圓頂事業股份有限公司"),
    ("寬宏藝術", "寬宏藝術經紀股份有限公司"),
    ("聯成娛樂", "聯成娛樂 On Line"),
    ("滾石", "滾石國際音樂股份有限公司"),
    ("杰威爾", "杰威爾音樂有限公司"),
    ("華貴娛樂", "華貴娛樂股份有限公司"),
    ("大國文化", "大國文化集團"),
    ("星曜製造", "星曜製造有限公司"),
    ("好玩國際", "好玩國際事股份有限公司"),
    ("KKLIVE", "KKLIVE Taiwan"),
    ("遠雄創藝", "遠雄創藝事業股份有限公司"),
    ("時藝多媒體", "時藝多媒體傳播股份有限公司"),
    ("鼓鼓", "相信音樂國際股份有限公司"),
    ("劉若英", "相信音樂國際股份有限公司"),
    ("孫盛希", "滾石國際音樂股份有限公司"),
    ("派偉俊", "杰威爾音樂有限公司"),
    ("顏社", "顏社企業有限公司"),
)


def _extract_tixcraft_organizer(title: str, text_or_html: str = "") -> str | None:
    """依拓元常見主辦單位名單與活動名稱特徵推導主辦單位。"""
    # 1. 若有內文，從內文解析「主辦單位」
    if text_or_html:
        soup = BeautifulSoup(text_or_html, "html.parser")
        text = soup.get_text("\n")
        for pattern in (
            r"(?:主辦[單位]*|指導[單位]*)[：:｜\|\s]+([^\r\n<，。]{2,50})",
            r"主辦[：:｜\|\s]+([^\r\n<，。]{2,50})",
        ):
            m = re.search(pattern, text)
            if m:
                val = m.group(1).strip()
                if not any(v in val for v in ("保有", "保留", "得", "有權", "規定", "公告", "權利", "變更")):
                    return val

    # 2. 檢查標題中的明確主辦/贊助單位標籤，如【主辦單位】
    m = re.search(r"【([^】]+)】", title)
    if m:
        tag = m.group(1).strip()
        if any(k in tag for k in ("娛樂", "音樂", "文化", "製作", "演藝", "工作室", "唱片", "經紀")):
            return tag

    # 3. 國際大秀／西洋巡演（拓元上絕大多數為 Live Nation Taiwan 理想國演藝主辦）
    title_lower = title.lower()
    if any(k in title_lower for k in (
        "live nation", "brunomars", "bruno mars", "westlife", "maroon 5", "maroon5",
        "stray kids", "5 seconds of summer", "5sos", "khalid", "joji", "fkj",
        "slowdive", "domi", "young k", "boynextdoor", "against the current"
    )):
        return "Live Nation Taiwan 理想國演藝"

    # 4. 台灣知名主辦單位關鍵字比對
    for kw, full_name in KNOWN_TIXCRAFT_PROMOTERS:
        if kw.lower() in title_lower:
            return full_name

    return None


class TixcraftResolveError(ResolveError):
    """拓元活動解析錯誤。"""


class TixcraftParseError(TixcraftResolveError):
    """拓元頁面資料解析錯誤。"""


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

    async def search(self, query: str, *, limit: int = 10) -> list[EventCandidate]:
        """解析 https://tixcraft.com/activity 列表並比對關鍵字。"""
        client = self._get_client()
        url = urljoin(self._base_url, "/activity")
        try:
            resp = await client.get(url, headers=DEFAULT_BROWSER_HEADERS, timeout=10.0)
            if resp.status_code in (401, 403):
                return []
            resp.raise_for_status()
        except Exception as exc:
            raise TixcraftResolveError(f"Failed to fetch activity list from {url}: {exc}") from exc

        soup = BeautifulSoup(resp.text, "html.parser")
        candidates: list[EventCandidate] = []
        seen_urls: set[str] = set()

        for link in soup.select("a[href*='/activity/detail/']"):
            href = link.get("href", "")
            title = link.get_text(strip=True)
            if not title or title == "節目介紹":
                continue

            full_url = urljoin(self._base_url, str(href))
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)

            # 計算比對分數
            score = fuzz.partial_ratio(query.lower(), title.lower()) / 100.0 if query else 1.0
            organizer = _extract_tixcraft_organizer(title)
            candidates.append(
                EventCandidate(
                    title=title,
                    url=full_url,
                    organizer=organizer or "tixcraft",
                    score=score,
                )
            )

        # 依分數排序並去重
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[:limit]

    async def fetch_event_metadata(self, event_url: str) -> Event:
        """解析活動詳細資料；若回 401/403 則從 activity 列表快取依 slug 比對建立基本 Event。"""
        client = self._get_client()
        slug = urlsplit(event_url).path.split("/")[-1]
        event_id = Event.make_id(PlatformEnum.TIXCRAFT, "tixcraft", slug)

        try:
            resp = await client.get(event_url, headers=DEFAULT_BROWSER_HEADERS, timeout=10.0)
            if resp.status_code in (401, 403):
                # 401/403 fallback: 從列表取得基本資訊
                candidates = await self.search(slug, limit=5)
                matched = next((c for c in candidates if slug in c.url), None)
                title = matched.title if matched else f"拓元活動 {slug}"
                organizer_display = (
                    matched.organizer
                    if (matched and matched.organizer and matched.organizer != "tixcraft")
                    else _extract_tixcraft_organizer(title)
                )
                raw_meta: dict[str, Any] = (
                    {"organizer_display": organizer_display}
                    if organizer_display
                    else {}
                )
                return Event(
                    id=event_id,
                    platform=PlatformEnum.TIXCRAFT,
                    organizer="tixcraft",
                    event_slug=slug,
                    title=title,
                    canonical_url=event_url,
                    status=EventStatus.ANNOUNCED,
                    ticket_types=[],
                    raw_metadata=raw_meta,
                )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise TixcraftResolveError(f"HTTP error fetching event metadata: {exc}") from exc
        except Exception as exc:
            raise TixcraftResolveError(f"Failed to fetch event metadata from {event_url}") from exc

        soup = BeautifulSoup(resp.text, "html.parser")
        title = ""
        start_date: datetime | None = None
        ticket_types: list[TicketType] = []

        # 優先解析 JSON-LD
        json_ld = soup.select_one("script[type='application/ld+json']")
        if json_ld and json_ld.string:
            try:
                data = json.loads(json_ld.string)
            except (ValueError, TypeError):
                data = None
            if isinstance(data, dict):
                title = str(data.get("name", ""))
                if "startDate" in data:
                    try:
                        parsed_dt = datetime.fromisoformat(data["startDate"])
                        start_date = parsed_dt.replace(tzinfo=timezone.utc) if parsed_dt.tzinfo is None else parsed_dt.astimezone(timezone.utc)
                    except (ValueError, TypeError):
                        start_date = None
                offers = data.get("offers", {})
                if isinstance(offers, dict) and "lowPrice" in offers:
                    try:
                        low_p = int(offers["lowPrice"])
                        high_p = int(offers.get("highPrice", low_p))
                    except (ValueError, TypeError):
                        low_p = 0
                        high_p = 0
                    if low_p > 0:
                        ticket_types.append(
                            TicketType(
                                id=TicketType.make_id(event_id, "offer_low"),
                                event_id=event_id,
                                name="最低票價",
                                price=low_p,
                                status=TicketTypeStatus.AVAILABLE,
                            )
                        )
                        if high_p != low_p:
                            ticket_types.append(
                                TicketType(
                                    id=TicketType.make_id(event_id, "offer_high"),
                                    event_id=event_id,
                                    name="最高票價",
                                    price=high_p,
                                    status=TicketTypeStatus.AVAILABLE,
                                )
                            )

        if not title:
            title_el = soup.select_one(".activity-title, h1, title")
            title = title_el.get_text(strip=True) if title_el else f"拓元活動 {slug}"

        organizer_display = _extract_tixcraft_organizer(title, resp.text)
        raw_meta: dict[str, Any] = (
            {"organizer_display": organizer_display}
            if organizer_display
            else {}
        )

        return Event(
            id=event_id,
            platform=PlatformEnum.TIXCRAFT,
            organizer="tixcraft",
            event_slug=slug,
            title=title,
            canonical_url=event_url,
            status=EventStatus.ON_SALE,
            sale_start_at=start_date,
            ticket_types=ticket_types,
            raw_metadata=raw_meta,
        )

    async def resolve(self, query: str) -> ResolveResult:
        if query.startswith(("http://", "https://")):
            try:
                ev = await self.fetch_event_metadata(query)
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
            except Exception:
                return ResolveResult(
                    query=query,
                    auto_selected=False,
                    event=None,
                    candidates=[],
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
