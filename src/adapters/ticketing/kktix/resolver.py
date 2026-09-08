from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup
from bs4.element import Tag
from rapidfuzz import fuzz

from adapters.ticketing.base import EventResolver
from adapters.ticketing.kktix.selectors import (
    KKTIX_EVENT_URL_RE,
    KKTIXSelectors,
    css_only,
)
from domain.event import (
    Event,
    EventCandidate,
    EventStatus,
    PlatformEnum,
    ResolveResult,
    TicketType,
    TicketTypeStatus,
)

MATCH_THRESHOLD = 0.85
FEED_URL_TEMPLATE = "https://{org}.kktix.cc/events.json"
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
HTML_PARSER = "html.parser"
MAX_ORGS_PER_SEARCH = 20

# An org slug is interpolated into the feed host, so anything that could terminate
# the host label (/, ?, @, :, .) must never reach the URL template.
ORG_SLUG_RE = re.compile(r"\A[a-zA-Z0-9_-]{1,64}\Z")

# Markers that prove the page shipped a ticket block; an empty parse against these
# means the DOM changed, not that the event has no tickets.
TICKET_BLOCK_SELECTORS = (
    ".tickets",
    "#tickets",
    ".ticket-unit",
    ".ticket-list",
    "table.tickets",
    "table[class*='ticket']",
    "[id^='ticket_']",
    ".display-table",
)

_WEEKDAY_PAREN_RE = re.compile(r"\((?:週|星期|周)[一二三四五六日]\)")
_OFFSET_SUFFIX_RE = re.compile(r"\(?\s*(?:GMT|UTC)?\s*([+-]\d{2}):?(\d{2})\s*\)?\s*$")
_DATETIME_SCAN_RE = re.compile(
    r"\d{4}[/-]\d{1,2}[/-]\d{1,2}"
    r"(?:\s*\((?:週|星期|周)[一二三四五六日]\))?"
    r"(?:\s+\d{1,2}:\d{2})?"
    r"(?:\s*\(?[+-]\d{2}:?\d{2}\)?)?"
)
_DATETIME_FORMATS = (
    "%Y/%m/%d %H:%M",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d",
    "%Y-%m-%d",
)


class KKTIXResolveError(Exception):
    """Raised when the KKTIX feed or event page cannot be retrieved / interpreted."""


class KKTIXParseError(KKTIXResolveError):
    """Raised when retrieved KKTIX content cannot be parsed into a domain object."""


def validate_org_slugs(orgs: Sequence[str]) -> list[str]:
    if isinstance(orgs, (str, bytes)) or not isinstance(orgs, Sequence):
        raise TypeError(
            f"orgs must be a Sequence[str] (e.g. list or tuple), got {type(orgs).__name__}"
        )
    deduped: list[str] = []
    for org in orgs:
        if not isinstance(org, str) or ORG_SLUG_RE.fullmatch(org) is None:
            raise ValueError(
                f"invalid KKTIX org slug {org!r}: must match {ORG_SLUG_RE.pattern}"
            )
        if org not in deduped:
            deduped.append(org)
    if len(deduped) > MAX_ORGS_PER_SEARCH:
        raise ValueError(f"too many orgs: {len(deduped)} > {MAX_ORGS_PER_SEARCH}")
    return deduped


def normalize_title(text: str) -> str:
    s = unicodedata.normalize("NFKC", text)
    s = s.casefold()
    s = re.sub(r"[^\w\u4e00-\u9fff]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def match_score(query: str, title: str) -> float:
    nq, nt = normalize_title(query), normalize_title(title)
    if not nq or not nt:
        return 0.0
    best = max(fuzz.WRatio(nq, nt), fuzz.token_set_ratio(nq, nt))
    return round(best / 100.0, 4)


def _parse_datetime(text: str) -> datetime:
    s = unicodedata.normalize("NFKC", text)
    s = _WEEKDAY_PAREN_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()

    tzinfo: timezone | None = None
    offset_match = _OFFSET_SUFFIX_RE.search(s)
    if offset_match:
        try:
            hours = int(offset_match.group(1))
            minutes = int(offset_match.group(2))
            sign = -1 if hours < 0 else 1
            tzinfo = timezone(timedelta(hours=hours, minutes=sign * minutes))
            s = s[: offset_match.start()].strip()
        except (ValueError, TypeError) as exc:
            raise KKTIXParseError(f"invalid timezone offset in {text!r}") from exc

    dt: datetime | None = None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        for fmt in _DATETIME_FORMATS:
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue

    if dt is None:
        raise KKTIXParseError(f"unparseable datetime: {text!r}")

    if tzinfo is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=tzinfo)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TAIPEI_TZ)
    return dt.astimezone(timezone.utc)


def _extract_first_datetime(text: str) -> datetime | None:
    for match in _DATETIME_SCAN_RE.finditer(text):
        try:
            return _parse_datetime(match.group(0))
        except KKTIXParseError:
            continue
    return None


def _parse_price(text: str) -> int:
    lowered = text.casefold()
    if "免費" in text or "free" in lowered:
        return 0
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return 0
    try:
        return int(digits)
    except (ValueError, TypeError):
        return 0


def _parse_status(text: str) -> TicketTypeStatus:
    lowered = text.casefold()
    if "售完" in text or "已售完" in text or "sold out" in lowered:
        return TicketTypeStatus.SOLD_OUT
    if "尚未開賣" in text or "即將" in text or "coming" in lowered:
        return TicketTypeStatus.COMING_SOON
    return TicketTypeStatus.AVAILABLE


def _select_first_text(
    soup: BeautifulSoup | Tag, selectors: str | list[str]
) -> str | None:
    for selector in css_only(selectors):
        node = soup.select_one(selector)
        if node is not None:
            text = node.get_text(strip=True)
            if text:
                return text
    return None


class KKTIXEventResolver(EventResolver):
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        orgs: Sequence[str] | None = None,
        threshold: float = MATCH_THRESHOLD,
        feed_url_template: str = FEED_URL_TEMPLATE,
    ) -> None:
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"threshold must be within [0.0, 1.0], got {threshold!r}")
        self._client = client
        self._orgs = validate_org_slugs(orgs) if orgs is not None else []
        self._threshold = threshold
        self._feed_url_template = feed_url_template

    async def search(
        self,
        query: str,
        *,
        orgs: Sequence[str] | None = None,
        limit: int = 10,
    ) -> list[EventCandidate]:
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit!r}")

        scope = validate_org_slugs(orgs) if orgs is not None else list(self._orgs)
        if not scope:
            direct = KKTIX_EVENT_URL_RE.match(query.strip())
            if direct:
                scope = validate_org_slugs([direct.group("org")])
        if not scope:
            raise KKTIXResolveError("organizer feed scope required")

        payloads = await asyncio.gather(*(self._fetch_feed(org) for org in scope))

        candidates: list[EventCandidate] = []
        for org, payload in zip(scope, payloads, strict=True):
            entries = payload.get("entry")
            if not isinstance(entries, list):
                entries = payload.get("entries")
            if not isinstance(entries, list):
                raise KKTIXResolveError(
                    f"unexpected feed payload for org={org!r}: missing 'entry' list"
                )
            parsed_any = False
            for raw in entries:
                candidate = self._parse_feed_entry(raw)
                if candidate is None:
                    continue
                parsed_any = True
                candidate.score = match_score(query, candidate.title)
                candidates.append(candidate)
            if entries and not parsed_any:
                raise KKTIXResolveError(
                    f"feed for org={org!r} has entries but none carry title+url"
                )

        candidates.sort(key=lambda c: (-c.score, c.title))
        return candidates[:limit]

    async def fetch_event_metadata(self, event_url: str) -> Event:
        match = KKTIX_EVENT_URL_RE.match(event_url.strip())
        if match is None:
            raise KKTIXParseError(f"not a KKTIX event url: {event_url!r}")
        org, slug = match.group("org"), match.group("slug")

        try:
            response = await self._client.get(event_url)
        except httpx.HTTPError as exc:
            raise KKTIXResolveError(f"HTTP request failed: {exc}") from exc
        if response.status_code // 100 != 2:
            raise KKTIXResolveError(
                f"event page fetch failed: url={event_url} status={response.status_code}"
            )
        soup = BeautifulSoup(response.text, HTML_PARSER)

        jsonld = self._extract_jsonld_event(soup)
        title = _select_first_text(soup, KKTIXSelectors.EVENT_TITLE)
        if title is None:
            name = jsonld.get("name") if jsonld else None
            title = name.strip() if isinstance(name, str) and name.strip() else None
        if title is None:
            raise KKTIXParseError(f"event title not found: {event_url!r}")

        raw_metadata: dict[str, Any] = {}
        organizer_display = _select_first_text(soup, KKTIXSelectors.EVENT_ORGANIZER)
        if organizer_display:
            raw_metadata["organizer_display"] = organizer_display
        if jsonld:
            raw_metadata["jsonld"] = jsonld

        event_start_at = self._parse_event_start_at(soup, jsonld)
        sale_start_at, sale_periods = self._parse_sale_start_at(soup)
        if sale_periods:
            raw_metadata["sale_periods"] = sale_periods

        event_id = Event.make_id(PlatformEnum.KKTIX, org, slug)
        ticket_types = self._parse_ticket_types(soup, event_id)

        return Event(
            id=event_id,
            platform=PlatformEnum.KKTIX,
            organizer=org,
            event_slug=slug,
            title=title,
            canonical_url=f"https://{org}.kktix.cc/events/{slug}",
            sale_start_at=sale_start_at,
            event_start_at=event_start_at,
            status=self._derive_status(ticket_types),
            raw_metadata=raw_metadata,
            ticket_types=ticket_types,
        )

    async def resolve(self, query: str) -> ResolveResult:
        direct = KKTIX_EVENT_URL_RE.match(query.strip())
        if direct:
            event = await self.fetch_event_metadata(query.strip())
            return ResolveResult(
                query=query,
                auto_selected=True,
                event=event,
                candidates=[
                    EventCandidate(
                        title=event.title,
                        url=event.canonical_url,
                        organizer=event.organizer,
                        score=1.0,
                    )
                ],
                threshold=self._threshold,
            )

        candidates = await self.search(query)
        top = candidates[0] if candidates else None
        if top is not None and top.score >= self._threshold:
            return ResolveResult(
                query=query,
                auto_selected=True,
                event=await self.fetch_event_metadata(top.url),
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

    async def _fetch_feed(self, org: str) -> dict[str, Any]:
        url = self._feed_url_template.format(org=org)
        try:
            response = await self._client.get(url)
        except httpx.HTTPError as exc:
            raise KKTIXResolveError(f"HTTP request failed: {exc}") from exc
        if response.status_code // 100 != 2:
            raise KKTIXResolveError(
                f"feed fetch failed: org={org} status={response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise KKTIXResolveError(f"feed payload is not JSON: org={org}") from exc
        if not isinstance(payload, dict):
            raise KKTIXResolveError(f"feed payload is not an object: org={org}")
        return payload

    def _parse_feed_entry(self, raw: Any) -> EventCandidate | None:
        if not isinstance(raw, dict):
            return None
        title = raw.get("title")
        url = raw.get("url")
        if not isinstance(title, str) or not title.strip():
            return None
        if not isinstance(url, str) or not url.strip():
            return None

        published: datetime | None = None
        published_raw = raw.get("published")
        if isinstance(published_raw, str) and published_raw.strip():
            try:
                published = _parse_datetime(published_raw)
            except KKTIXParseError:
                published = None

        author = raw.get("author")
        summary = raw.get("summary")
        return EventCandidate(
            title=title.strip(),
            url=url.strip(),
            organizer=author.strip()
            if isinstance(author, str) and author.strip()
            else None,
            score=0.0,
            published=published,
            summary=summary if isinstance(summary, str) else None,
            raw={"content": raw.get("content")},
        )

    def _extract_jsonld_event(self, soup: BeautifulSoup) -> dict[str, Any] | None:
        for script in soup.select(KKTIXSelectors.EVENT_JSONLD_SCRIPT):
            text = script.get_text(strip=True)
            if not text:
                continue
            try:
                payload = json.loads(text)
            except ValueError:
                continue
            node = self._first_event_node(payload)
            if node is not None:
                return node
        return None

    def _first_event_node(self, payload: Any) -> dict[str, Any] | None:
        if isinstance(payload, dict):
            node_type = payload.get("@type")
            types = node_type if isinstance(node_type, list) else [node_type]
            if "Event" in types:
                return payload
            graph = payload.get("@graph")
            if isinstance(graph, list):
                return self._first_event_node(graph)
            return None
        if isinstance(payload, list):
            for item in payload:
                node = self._first_event_node(item)
                if node is not None:
                    return node
        return None

    def _parse_event_start_at(
        self, soup: BeautifulSoup, jsonld: dict[str, Any] | None
    ) -> datetime | None:
        if jsonld:
            start_date = jsonld.get("startDate")
            if isinstance(start_date, str) and start_date.strip():
                try:
                    return _parse_datetime(start_date)
                except KKTIXParseError:
                    pass

        for selector in css_only(KKTIXSelectors.EVENT_DESCRIPTION):
            for node in soup.select(selector):
                found = _extract_first_datetime(node.get_text(" ", strip=True))
                if found is not None:
                    return found
        return None

    def _parse_sale_start_at(
        self, soup: BeautifulSoup
    ) -> tuple[datetime | None, list[dict[str, str]]]:
        starts: list[datetime] = []
        periods: list[dict[str, str]] = []
        for row in soup.select(KKTIXSelectors.EVENT_TICKET_TABLE_ROWS):
            times = row.select(KKTIXSelectors.EVENT_TICKET_PERIOD_TIMES)
            if not times:
                continue
            start_text = times[0].get_text(" ", strip=True)
            try:
                start = _parse_datetime(start_text)
            except KKTIXParseError:
                continue
            starts.append(start)
            period: dict[str, str] = {"start": start.isoformat()}
            if len(times) > 1:
                end_text = times[1].get_text(" ", strip=True)
                try:
                    period["end"] = _parse_datetime(end_text).isoformat()
                except KKTIXParseError:
                    period["end_raw"] = end_text
            periods.append(period)

        if starts:
            return min(starts), periods

        for selector in css_only(KKTIXSelectors.EVENT_SALE_TIME):
            for node in soup.select(selector):
                text = node.get_text(" ", strip=True)
                if not text:
                    continue
                found = _extract_first_datetime(text)
                if found is not None:
                    return found, periods
        return None, periods

    def _parse_ticket_types(
        self, soup: BeautifulSoup, event_id: str
    ) -> list[TicketType]:
        tickets: list[TicketType] = []
        for row in soup.select(KKTIXSelectors.EVENT_TICKET_TABLE_ROWS):
            name = _select_first_text(row, KKTIXSelectors.EVENT_TICKET_ROW_NAME)
            price_text = _select_first_text(row, KKTIXSelectors.EVENT_TICKET_ROW_PRICE)
            if not name or not price_text:
                continue
            status_text = (
                _select_first_text(row, KKTIXSelectors.EVENT_TICKET_ROW_STATUS) or ""
            )
            tickets.append(
                TicketType(
                    id=TicketType.make_id(event_id, name),
                    event_id=event_id,
                    name=name,
                    price=_parse_price(price_text),
                    status=_parse_status(status_text),
                    inventory_estimate=None,
                    raw_id=row.get("id") if isinstance(row.get("id"), str) else None,
                )
            )
        if not tickets and any(
            soup.select_one(selector) is not None for selector in TICKET_BLOCK_SELECTORS
        ):
            raise KKTIXParseError(
                f"ticket block present but no ticket type parsed: event_id={event_id}"
            )
        return tickets

    def _derive_status(self, tickets: list[TicketType]) -> EventStatus:
        if not tickets:
            return EventStatus.UNKNOWN
        statuses = {ticket.status for ticket in tickets}
        if statuses == {TicketTypeStatus.SOLD_OUT}:
            return EventStatus.SOLD_OUT
        if TicketTypeStatus.AVAILABLE in statuses:
            return EventStatus.ON_SALE
        if statuses == {TicketTypeStatus.COMING_SOON}:
            return EventStatus.ANNOUNCED
        return EventStatus.UNKNOWN
