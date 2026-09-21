"""ibon 活動解析器。"""

from __future__ import annotations

import contextlib
import json
import logging
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
)

logger = logging.getLogger(__name__)

KNOWN_IBON_PROMOTERS = (
    ("高鐵", "台灣高速鐵路股份有限公司"),
    ("六福村", "六福開發股份有限公司"),
    ("海洋生物博物館", "國立海洋生物博物館"),
    ("海生館", "國立海洋生物博物館"),
    ("阿里山", "農業部林業及自然保育署嘉義分署"),
    ("野柳", "野柳地質公園"),
    ("西湖渡假村", "西湖渡假村股份有限公司"),
    ("傳說對決", "新加坡商競舞電競有限公司臺灣分公司"),
    ("傳說十週年", "新加坡商競舞電競有限公司臺灣分公司"),
    ("江美琪", "萬力達娛樂事業有限公司、星統寰宇娛樂有限公司"),
    ("厄倫蒂兒", "春魚創意 、這方娛樂"),
    ("暗喻幻想", "東穹國際音樂股份有限公司"),
    ("台灣好行", "交通部觀光署"),
    ("東琉線", "東琉線交通客船聯營處"),
)


def _extract_ibon_organizer(item: dict[str, Any]) -> str | None:
    """從 ibon 資料或內容中解析真實主辦單位。"""
    # 1. 優先檢查 ActivityHost 欄位
    host = str(item.get("ActivityHost") or "").strip()
    if host and host.lower() not in ("none", "null", ""):
        return host

    # 2. 從 ActivityContent / BuyTicketNotice / NoticeMatters 解析
    content = (
        str(item.get("ActivityContent") or "")
        + " "
        + str(item.get("BuyTicketNotice") or "")
        + " "
        + str(item.get("NoticeMatters") or "")
    )
    if content.strip():
        soup = BeautifulSoup(content, "html.parser")
        text = soup.get_text("\n")
        for pattern in (
            r"(?:主辦[單位]*|指導[單位]*)[：:｜\|\s]+([^\r\n<，。]{2,50})",
            r"主辦[：:｜\|\s]+([^\r\n<，。]{2,50})",
        ):
            m = re.search(pattern, text)
            if m:
                val = m.group(1).strip()
                if not any(
                    v in val
                    for v in (
                        "保有",
                        "保留",
                        "得",
                        "有權",
                        "規定",
                        "公告",
                        "權利",
                        "變更",
                        "沒收",
                    )
                ):
                    return val

    # 3. 標題中的【主辦單位】
    title = str(item.get("ActivityName", "")).strip()
    m = re.search(r"【([^】]+)】", title)
    if m:
        tag = m.group(1).strip()
        if any(
            k in tag
            for k in ("娛樂", "音樂", "文化", "製作", "演藝", "協會", "農會", "公司")
        ):
            return tag

    # 4. 常見知名主辦單位與樂園/展覽主辦比對
    title_lower = title.lower()
    for kw, full_name in KNOWN_IBON_PROMOTERS:
        if kw.lower() in title_lower:
            return full_name

    return None


class IbonResolveError(ResolveError):
    """ibon 活動解析錯誤。"""


class IbonParseError(IbonResolveError):
    """ibon 頁面或 API 資料解析錯誤。"""


class IbonEventResolver(EventResolver):
    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        threshold: float = 0.6,
        base_url: str = "https://ticket.ibon.com.tw",
        **kwargs: Any,
    ) -> None:
        self._client = client
        self._threshold = threshold
        self._base_url = base_url
        self._kwargs = kwargs

    def _get_client(self) -> httpx.AsyncClient:
        return self._client if self._client is not None else httpx.AsyncClient()

    async def search(self, query: str, *, limit: int = 10) -> list[EventCandidate]:
        """呼叫 POST /api/ActivityInfo/GetIndexData 並解析 JSON，不解析 Angular SPA 空 HTML。"""
        client = self._get_client()
        url = urljoin(self._base_url, "/api/ActivityInfo/GetIndexData")

        all_items: list[dict[str, Any]] = []
        try:
            resp = await client.post(url, data={"pattern": "entertainment"}, timeout=10.0)
            resp.raise_for_status()
            payload = resp.json()
            items = (
                payload.get("Item", {}).get("List", [])
                if isinstance(payload.get("Item"), dict)
                else payload.get("Data", [])
            )
            if isinstance(items, list):
                all_items.extend(items)
        except Exception as exc:
            raise IbonResolveError(f"Failed to post {url}: {exc}") from exc

        candidates: list[EventCandidate] = []
        seen_ids: set[str] = set()

        for item in all_items:
            if not isinstance(item, dict):
                continue
            activity_id = str(item.get("ActivityID") or item.get("ActivityId") or "").strip()
            title = str(item.get("ActivityName", "")).strip()
            if not activity_id or not title or activity_id in seen_ids:
                continue
            seen_ids.add(activity_id)

            event_url = urljoin(self._base_url, f"/ActivityInfo/Details/{activity_id}")
            score = fuzz.partial_ratio(query.lower(), title.lower()) / 100.0 if query else 1.0

            published: datetime | None = None
            date_str = item.get("StartDate")
            if isinstance(date_str, str):
                try:
                    published = datetime.strptime(date_str.split()[0], "%Y/%m/%d").replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    published = None

            organizer = _extract_ibon_organizer(item)
            candidates.append(
                EventCandidate(
                    title=title,
                    url=event_url,
                    organizer=organizer or "ibon",
                    score=score,
                    published=published,
                    summary=item.get("Location"),
                    raw=item,
                )
            )

        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[:limit]

    async def fetch_event_metadata(self, event_url: str) -> Event:
        """優先呼叫 /api/ActivityInfo/GetDetailData 取得活動資訊，退回解析 HTML 與 JSON-LD。"""
        client = self._get_client()
        activity_id = urlsplit(event_url).path.split("/")[-1]
        event_id = Event.make_id(PlatformEnum.IBON, "ibon", activity_id)

        # 優先透過 API 取得詳情
        detail_url = urljoin(self._base_url, "/api/ActivityInfo/GetDetailData")
        try:
            resp = await client.post(detail_url, data={"id": activity_id}, timeout=10.0)
            if resp.status_code == 200:
                payload = resp.json()
                item = payload.get("Item", {}) if isinstance(payload, dict) else {}
                title = str(item.get("ActivityName", "")).strip()
                if title:
                    start_date: datetime | None = None
                    date_str = item.get("StartDate")
                    if isinstance(date_str, str):
                        try:
                            start_date = datetime.strptime(date_str.split()[0], "%Y/%m/%d").replace(tzinfo=timezone.utc)
                        except (ValueError, TypeError):
                            start_date = None
                    organizer_display = _extract_ibon_organizer(item)
                    raw_meta: dict[str, Any] = (
                        {"organizer_display": organizer_display}
                        if organizer_display
                        else {}
                    )
                    return Event(
                        id=event_id,
                        platform=PlatformEnum.IBON,
                        organizer="ibon",
                        event_slug=activity_id,
                        title=title,
                        canonical_url=event_url,
                        status=EventStatus.ON_SALE,
                        sale_start_at=start_date,
                        ticket_types=[],
                        raw_metadata=raw_meta,
                    )
        except Exception as exc:
            logger.debug("ibon_get_detail_data_failed: %s (%s)", activity_id, exc)

        # 退回直接抓取 HTML 解析
        try:
            resp = await client.get(event_url, timeout=10.0)
            resp.raise_for_status()
        except Exception as exc:
            raise IbonResolveError(f"Failed to fetch event metadata from {event_url}") from exc

        soup = BeautifulSoup(resp.text, "html.parser")
        title = ""
        start_date: datetime | None = None

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

        if not title:
            title_el = soup.select_one(".activity-info h1, h1, title")
            title = title_el.get_text(strip=True) if title_el else f"ibon 活動 {activity_id}"

        organizer_display = _extract_ibon_organizer(
            {"ActivityContent": resp.text, "ActivityName": title}
        )
        raw_meta: dict[str, Any] = (
            {"organizer_display": organizer_display}
            if organizer_display
            else {}
        )

        return Event(
            id=event_id,
            platform=PlatformEnum.IBON,
            organizer="ibon",
            event_slug=activity_id,
            title=title,
            canonical_url=event_url,
            status=EventStatus.ON_SALE,
            sale_start_at=start_date,
            ticket_types=[],
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
