"""ibon 活動解析器。

ibon 的前台是 Angular SPA，HTML 裡沒有活動資料；所有內容都來自三支 JSON API：

* ``POST /api/ActivityInfo/GetIndexData``（form ``pattern``）——全站活動清單。
* ``POST /api/ActivityInfo/GetDetailData``（form ``id``）——單一活動的完整欄位。
* ``POST /api/ActivityInfo/GetGameInfoList``（**JSON** body）——場次清單與購票連結。

第三支一定要用 JSON body 並帶 ``hasDeadline``；少了它伺服器會回 ``Enable=false``
且每個場次的 ``Href`` 都是 null，正是拿不到開賣狀態與購票網址的原因。
"""

from __future__ import annotations

import contextlib
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

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

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

#: 活動說明開頭幾乎都是這幾段全站共用的公告，直接當描述會每個活動長一樣。
BOILERPLATE_MARKERS = (
    "ibon售票網會員登入調整",
    "會員帳號連結",
    "加值優惠",
    "訂購此活動票券可加購高鐵",
    "ibon票劵加購高鐵",
    "ibon票券加購高鐵",
    "為協助您管理帳號登入方式",
    "為避免開賣時登入逾時",
    "了解更多",
    "可加購高鐵車票",
    "高鐵車票並享折扣",
    "可購買去程單程票",
    "單程票到達站",
    "起可加購高鐵",
    "成年禮金",
    "文化幣",
    "請詳見",
)

#: 純日期、純符號、或只有幾個字的行不是描述，是排版殘渣。
_DATE_ONLY_RE = re.compile(r"^[\d\s/()（）\-~～:：.、,，一二三四五六日起至]+$")

ORGANIZER_LABELS = ("主辦單位", "主辦", "指導單位", "共同主辦")
ORGANIZER_REJECT_WORDS = (
    "保有",
    "保留",
    "有權",
    "規定",
    "公告",
    "權利",
    "變更",
    "沒收",
    "不負",
    "得視",
)

#: 中文標籤後面常常再跟一個英文標籤（「主辦單位 Organizer：…」），要一起吃掉，
#: 否則抓到的「主辦單位」會是 "Organizer：某某公司"。
_ORGANIZER_RE = re.compile(
    r"(?:{labels})\s*(?:organizer|organiser|host|presented\s+by)?"
    r"\s*[：:︰/／｜|]\s*([^\r\n]{{2,60}})".format(labels="|".join(ORGANIZER_LABELS)),
    re.IGNORECASE,
)
_SHOW_DATE_RE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})\([^)]*\)\s*(\d{1,2}):(\d{2})")


def _parse_ibon_datetime(raw: Any) -> datetime | None:
    """ibon 的時間字串一律是台北時間，轉成 UTC 後才進 domain。"""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d"):
        try:
            parsed = datetime.strptime(text[: len(fmt) + 4].strip(), fmt)
        except ValueError:
            continue
        return parsed.replace(tzinfo=TAIPEI_TZ).astimezone(timezone.utc)
    with contextlib.suppress(ValueError):
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=TAIPEI_TZ)
        return parsed.astimezone(timezone.utc)
    return None


def _parse_show_sale_date(raw: Any) -> datetime | None:
    """``ShowSaleDate`` 長成 ``2026/10/18(日) 19:00``，沒有現成的 ISO 可用。"""
    if not isinstance(raw, str):
        return None
    match = _SHOW_DATE_RE.search(raw)
    if match is None:
        return None
    year, month, day, hour, minute = (int(g) for g in match.groups())
    with contextlib.suppress(ValueError):
        return datetime(
            year, month, day, hour, minute, tzinfo=TAIPEI_TZ
        ).astimezone(timezone.utc)
    return None


def _html_to_paragraphs(raw: str | None) -> list[str]:
    if not raw:
        return []
    soup = BeautifulSoup(raw, "html.parser")
    text = soup.get_text("\n")
    return [line.strip() for line in text.splitlines() if line.strip()]


def _extract_ibon_description(item: dict[str, Any]) -> str | None:
    """活動說明：跳過全站公告段落，取第一段真的在講這場活動的文字。"""
    short = str(item.get("ActivityDes") or "").strip()
    paragraphs = _html_to_paragraphs(str(item.get("ActivityContent") or ""))
    meaningful = [
        line
        for line in paragraphs
        if len(line) >= 12
        and not _DATE_ONLY_RE.match(line)
        and not any(marker in line for marker in BOILERPLATE_MARKERS)
    ]
    body = " ".join(meaningful[:4]).strip()
    if short and body:
        return f"{short} {body}" if short not in body else body
    return body or short or None


def _extract_ibon_organizer(item: dict[str, Any]) -> str | None:
    """主辦單位：先看 ``ActivityHost``，沒有就從活動說明裡的「主辦單位｜…」撈。"""
    host = str(item.get("ActivityHost") or "").strip()
    if host and host.lower() not in ("none", "null"):
        return host

    haystack = "\n".join(
        _html_to_paragraphs(
            " ".join(
                str(item.get(key) or "")
                for key in ("ActivityContent", "BuyTicketNotice", "NoticeMatters")
            )
        )
    )
    for match in _ORGANIZER_RE.finditer(haystack):
        value = _clean_organizer(match.group(1))
        if value is None:
            continue
        return value
    return None


#: 中英並列的標題（「主辦單位/Organizer：…」）會把英文標籤一起帶進來。
_ORGANIZER_LABEL_PREFIX_RE = re.compile(
    r"^(?:organizer|organiser|presented\s+by|host)\s*[：:︰/／｜|]\s*", re.IGNORECASE
)


def _clean_organizer(raw: str) -> str | None:
    value = _ORGANIZER_LABEL_PREFIX_RE.sub("", raw.strip(" 　:：/｜|"))
    value = re.split(r"[。；;]", value)[0].strip(" 　:：/｜|")
    if len(value) < 2:
        return None
    if any(word in value for word in ORGANIZER_REJECT_WORDS):
        return None
    return value[:60]


def _ibon_status(
    detail: dict[str, Any],
    sessions: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> EventStatus:
    """開賣狀態一律由場次的可購買旗標決定，售票時間只在沒有場次時當備援。"""
    if sessions:
        if any(s.get("can_buy") for s in sessions):
            return EventStatus.ON_SALE
        if all(s.get("sold_out") for s in sessions):
            return EventStatus.SOLD_OUT

    current = now or datetime.now(timezone.utc)
    sale_start = _parse_ibon_datetime(detail.get("ActivityTicketSDate"))
    sale_end = _parse_ibon_datetime(detail.get("ActivityTicketEDate"))
    if sale_end is not None and current > sale_end:
        return EventStatus.CLOSED
    if sale_start is not None and current < sale_start:
        return EventStatus.ANNOUNCED
    if sale_start is not None:
        return EventStatus.ON_SALE
    return EventStatus.UNKNOWN


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

    def _event_url(self, activity_id: str) -> str:
        return urljoin(self._base_url, f"/ActivityInfo/Details/{activity_id}")

    async def search(self, query: str, *, limit: int = 10) -> list[EventCandidate]:
        """呼叫 ``POST /api/ActivityInfo/GetIndexData`` 取全站活動清單後比對關鍵字。"""
        client = self._get_client()
        url = urljoin(self._base_url, "/api/ActivityInfo/GetIndexData")

        try:
            resp = await client.post(
                url, data={"pattern": "entertainment"}, timeout=10.0
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            raise IbonResolveError(f"Failed to post {url}: {exc}") from exc

        item = payload.get("Item")
        items = item.get("List", []) if isinstance(item, dict) else payload.get("Data", [])
        if not isinstance(items, list):
            items = []

        candidates: list[EventCandidate] = []
        seen_ids: set[str] = set()
        for entry in items:
            if not isinstance(entry, dict):
                continue
            activity_id = str(
                entry.get("ActivityID") or entry.get("ActivityId") or ""
            ).strip()
            title = str(entry.get("ActivityName", "")).strip()
            if not activity_id or not title or activity_id in seen_ids:
                continue
            seen_ids.add(activity_id)

            score = (
                fuzz.partial_ratio(query.lower(), title.lower()) / 100.0 if query else 1.0
            )
            event_start = _parse_ibon_datetime(
                entry.get("GameStartDateMin") or entry.get("ActivitySDate")
            )
            summary = str(entry.get("ActivityDes") or "").strip() or None
            candidates.append(
                EventCandidate(
                    title=title,
                    url=self._event_url(activity_id),
                    organizer="ibon",
                    score=score,
                    published=event_start,
                    summary=summary,
                    raw={
                        "activity_id": activity_id,
                        "event_start_at": event_start.isoformat() if event_start else None,
                        "sale_end_at": (
                            dt.isoformat()
                            if (dt := _parse_ibon_datetime(entry.get("ActivityEDate")))
                            else None
                        ),
                        "image_url": entry.get("ActivityImage"),
                        "category": entry.get("ActivityCategoryCode"),
                    },
                )
            )

        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[:limit]

    async def _fetch_detail(self, activity_id: str) -> dict[str, Any]:
        client = self._get_client()
        url = urljoin(self._base_url, "/api/ActivityInfo/GetDetailData")
        resp = await client.post(url, data={"id": activity_id}, timeout=10.0)
        resp.raise_for_status()
        payload = resp.json()
        item = payload.get("Item") if isinstance(payload, dict) else None
        return item if isinstance(item, dict) else {}

    async def fetch_sessions(self, activity_id: str) -> list[dict[str, Any]]:
        """場次清單。``hasDeadline`` 少給就拿不到 ``Href``，開賣狀態也會全錯。"""
        client = self._get_client()
        url = urljoin(self._base_url, "/api/ActivityInfo/GetGameInfoList")
        try:
            numeric_id: int | str = int(activity_id)
        except ValueError:
            numeric_id = activity_id
        try:
            resp = await client.post(
                url,
                json={"id": numeric_id, "hasDeadline": True, "SystemBrowseType": 0},
                timeout=10.0,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            logger.debug("ibon_get_game_info_list_failed: %s (%s)", activity_id, exc)
            return []

        item = payload.get("Item") if isinstance(payload, dict) else None
        rows = item.get("GIHtmls") if isinstance(item, dict) else None
        if not isinstance(rows, list):
            return []

        sessions: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            starts_at = _parse_show_sale_date(row.get("ShowSaleDate"))
            sessions.append(
                {
                    "name": str(row.get("GameInfoName") or "").strip(),
                    "venue": str(row.get("VenueRegion") or "").strip() or None,
                    "display_date": str(row.get("ShowSaleDate") or "").strip(),
                    "starts_at": starts_at.isoformat() if starts_at else None,
                    "can_buy": bool(row.get("CanBuy")),
                    "sold_out": bool(row.get("SoldOut")),
                    "purchase_url": _absolute_go_ticket_url(row.get("Href")),
                }
            )
        return sessions

    async def fetch_event_metadata(self, event_url: str) -> Event:
        activity_id = urlsplit(event_url).path.rstrip("/").split("/")[-1]
        event_id = Event.make_id(PlatformEnum.IBON, "ibon", activity_id)

        try:
            detail = await self._fetch_detail(activity_id)
        except Exception as exc:
            raise IbonResolveError(
                f"Failed to fetch ibon activity {activity_id}: {exc}"
            ) from exc

        title = str(detail.get("ActivityName", "")).strip()
        if not title:
            raise IbonParseError(
                f"ibon activity {activity_id} returned no activity name"
            )

        sessions = await self.fetch_sessions(activity_id)

        venue = str(detail.get("ActivityLocation") or "").strip() or None
        if venue is None:
            venue = next((s["venue"] for s in sessions if s.get("venue")), None)

        session_starts = [
            dt
            for s in sessions
            if (dt := _parse_ibon_datetime(s.get("starts_at"))) is not None
        ]
        event_start_at = (
            min(session_starts)
            if session_starts
            else _parse_ibon_datetime(detail.get("ActivitySDate"))
        )

        raw_metadata: dict[str, Any] = {"detail_source": "ibon_api"}
        description = _extract_ibon_description(detail)
        if description:
            raw_metadata["description"] = description
        organizer_display = _extract_ibon_organizer(detail)
        if organizer_display:
            raw_metadata["organizer_display"] = organizer_display
        if venue:
            raw_metadata["venue"] = venue
        if sessions:
            raw_metadata["sessions"] = sessions
        image = str(detail.get("ActivityImageURL") or "").strip()
        if image:
            raw_metadata["image_url"] = image

        return Event(
            id=event_id,
            platform=PlatformEnum.IBON,
            organizer="ibon",
            event_slug=activity_id,
            title=title,
            canonical_url=self._event_url(activity_id),
            status=_ibon_status(detail, sessions),
            sale_start_at=_parse_ibon_datetime(detail.get("ActivityTicketSDate")),
            sale_end_at=_parse_ibon_datetime(detail.get("ActivityTicketEDate")),
            event_start_at=event_start_at,
            ticket_types=[],
            raw_metadata=raw_metadata,
        )

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


def _absolute_go_ticket_url(href: Any) -> str | None:
    """``Href`` 是 ``/ActivityInfo/GoTicketURL?GoUrl=<訂購頁>``，直接取出訂購頁。"""
    if not isinstance(href, str) or not href.strip():
        return None
    from urllib.parse import parse_qs, unquote

    if "GoUrl=" in href:
        query = urlsplit(href).query
        target = parse_qs(query).get("GoUrl", [""])[0]
        if target:
            return unquote(target)
    if href.startswith("http"):
        return href
    return urljoin("https://ticket.ibon.com.tw", href)
