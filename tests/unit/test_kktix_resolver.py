from __future__ import annotations

from datetime import timezone

import httpx
import pytest

from adapters.ticketing.kktix.resolver import (
    MATCH_THRESHOLD,
    MAX_ORGS_PER_SEARCH,
    KKTIXEventResolver,
    KKTIXParseError,
    KKTIXResolveError,
    match_score,
    normalize_title,
    validate_org_slugs,
)
from adapters.ticketing.kktix.selectors import KKTIXSelectors, css_only
from domain.event import EventStatus, PlatformEnum, TicketType, TicketTypeStatus
from tests.conftest import EXPECTED_EVENT_START_UTC, EXPECTED_SALE_START_UTC

EVENT_URL = "https://atarayo.kktix.cc/events/atarayo-taipei-2026"
FULLWIDTH_URL = "https://atarayo.kktix.cc/events/atarayo-taipei-2026-fullwidth"


def make_resolver(client: httpx.AsyncClient, **kwargs: object) -> KKTIXEventResolver:
    return KKTIXEventResolver(client, orgs=["atarayo"], **kwargs)  # type: ignore[arg-type]


def static_client(payload: object, *, status_code: int = 200) -> httpx.AsyncClient:
    def handler(_request: httpx.Request) -> httpx.Response:
        if status_code != 200:
            return httpx.Response(status_code)
        return httpx.Response(200, json=payload)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def raising_client(exc: Exception) -> httpx.AsyncClient:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise exc

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def recording_client(seen: list[httpx.URL], payload: object) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json=payload)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def html_client(body: str) -> httpx.AsyncClient:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


FEED_ONE_ENTRY: dict[str, object] = {
    "entry": [
        {
            "title": "Atarayo Asia Tour 2026 — Taipei",
            "url": EVENT_URL,
            "author": "Atarayo Live",
        }
    ]
}


def test_normalize_title_folds_fullwidth_and_punctuation() -> None:
    assert normalize_title("ＡＴＡＲＡＹＯ　台北　２０２６") == "atarayo 台北 2026"
    assert normalize_title("Atarayo Asia Tour 2026 — Taipei") == (
        "atarayo asia tour 2026 taipei"
    )
    assert normalize_title("  多重   空白  ") == "多重 空白"


def test_match_score_identical_is_one() -> None:
    assert match_score("Atarayo Taipei 2026", "Atarayo Taipei 2026") == 1.0


def test_match_score_fullwidth_variant_passes_threshold() -> None:
    score = match_score("ATARAYO 台北 2026", "ＡＴＡＲＡＹＯ　台北　２０２６")
    assert score >= MATCH_THRESHOLD


def test_match_score_unrelated_is_below_threshold() -> None:
    assert (
        match_score("Atarayo Asia Tour 2026 — Taipei", "Python Conference Taiwan 2026")
        < MATCH_THRESHOLD
    )
    assert match_score("Atarayo Asia Tour 2026 — Taipei", "完全不相關的關鍵字") == 0.0


@pytest.mark.parametrize(("query", "title"), [("", "abc"), ("abc", ""), ("", "")])
def test_match_score_empty_is_zero(query: str, title: str) -> None:
    assert match_score(query, title) == 0.0


def test_css_only_filters_playwright_syntax() -> None:
    assert css_only("a, b:has-text('x'), c") == ["a", "c"]
    assert css_only(KKTIXSelectors.EVENT_BUY_LINK) == [
        ".order-now-section a.btn-point",
        "#order-now a",
    ]


async def test_search_sorts_by_score_and_respects_limit(
    kktix_client: httpx.AsyncClient,
) -> None:
    candidates = await make_resolver(kktix_client).search(
        "Atarayo Taipei 2026", limit=3
    )
    assert len(candidates) == 3
    scores = [candidate.score for candidate in candidates]
    assert scores == sorted(scores, reverse=True)
    assert candidates[0].url in {EVENT_URL, FULLWIDTH_URL}
    assert candidates[0].organizer == "Atarayo Live"


async def test_search_published_is_utc_and_audit_only(
    kktix_client: httpx.AsyncClient,
) -> None:
    candidates = await make_resolver(kktix_client).search("Atarayo Taipei 2026")
    published = [c.published for c in candidates if c.published is not None]
    assert published
    assert all(value.tzinfo is timezone.utc for value in published)


async def test_search_rejects_bad_payload() -> None:
    async with static_client({"data": []}) as client:
        with pytest.raises(KKTIXResolveError):
            await make_resolver(client).search("Atarayo")


async def test_search_rejects_non_2xx() -> None:
    async with static_client(None, status_code=404) as client:
        with pytest.raises(KKTIXResolveError):
            await make_resolver(client).search("Atarayo")


async def test_search_requires_org_scope(kktix_client: httpx.AsyncClient) -> None:
    with pytest.raises(KKTIXResolveError):
        await KKTIXEventResolver(kktix_client).search("Atarayo")


async def test_search_supports_entries_alias() -> None:
    payload = {
        "entries": [
            {
                "title": "Atarayo Asia Tour 2026 — Taipei",
                "url": EVENT_URL,
                "author": "Atarayo Live",
            }
        ]
    }
    async with static_client(payload) as client:
        candidates = await make_resolver(client).search("Atarayo Taipei 2026")
    assert len(candidates) == 1
    assert candidates[0].url == EVENT_URL


async def test_search_rejects_feed_without_any_usable_entry() -> None:
    async with static_client({"entry": [{"summary": "no title or url"}]}) as client:
        with pytest.raises(KKTIXResolveError):
            await make_resolver(client).search("Atarayo")


async def test_fetch_event_metadata_separates_two_datetimes(
    kktix_client: httpx.AsyncClient,
) -> None:
    event = await make_resolver(kktix_client).fetch_event_metadata(EVENT_URL)
    assert event.event_start_at is not None
    assert event.sale_start_at is not None
    assert event.event_start_at == EXPECTED_EVENT_START_UTC
    assert event.sale_start_at == EXPECTED_SALE_START_UTC
    assert event.event_start_at.date() != event.sale_start_at.date()
    assert event.event_start_at.tzinfo is timezone.utc
    assert event.sale_start_at.tzinfo is timezone.utc


async def test_fetch_event_metadata_identity_fields(
    kktix_client: httpx.AsyncClient,
) -> None:
    event = await make_resolver(kktix_client).fetch_event_metadata(EVENT_URL)
    assert event.platform is PlatformEnum.KKTIX
    assert event.organizer == "atarayo"
    assert event.event_slug == "atarayo-taipei-2026"
    assert event.canonical_url == EVENT_URL
    assert event.title == "Atarayo Asia Tour 2026 — Taipei"
    assert event.raw_metadata["organizer_display"] == "Atarayo Live"
    assert event.raw_metadata["jsonld"]["startDate"] == "2026-03-14T19:30:00+08:00"


async def test_fetch_event_metadata_ticket_types(
    kktix_client: httpx.AsyncClient,
) -> None:
    event = await make_resolver(kktix_client).fetch_event_metadata(EVENT_URL)
    assert len(event.ticket_types) == 3
    assert [ticket.price for ticket in event.ticket_types] == [2800, 3800, 0]
    assert [ticket.status for ticket in event.ticket_types] == [
        TicketTypeStatus.AVAILABLE,
        TicketTypeStatus.SOLD_OUT,
        TicketTypeStatus.COMING_SOON,
    ]
    assert event.status is EventStatus.ON_SALE
    assert all(ticket.event_id == event.id for ticket in event.ticket_types)
    assert all(ticket.inventory_estimate is None for ticket in event.ticket_types)


def test_derive_status_marks_mixed_sold_out_and_coming_soon_as_announced(
    kktix_client: httpx.AsyncClient,
) -> None:
    event_id = "ev_test"
    tickets = [
        TicketType(
            id="tt_sold_out",
            event_id=event_id,
            name="早鳥票",
            price=1000,
            status=TicketTypeStatus.SOLD_OUT,
        ),
        TicketType(
            id="tt_coming_soon",
            event_id=event_id,
            name="一般票",
            price=1200,
            status=TicketTypeStatus.COMING_SOON,
        ),
    ]

    assert make_resolver(kktix_client)._derive_status(tickets) is EventStatus.ANNOUNCED


async def test_fetch_event_metadata_fallback_chain(
    kktix_client: httpx.AsyncClient,
) -> None:
    event = await make_resolver(kktix_client).fetch_event_metadata(FULLWIDTH_URL)
    assert "jsonld" not in event.raw_metadata
    assert event.event_start_at is not None
    assert event.sale_start_at is not None
    assert event.event_start_at == EXPECTED_EVENT_START_UTC
    assert event.sale_start_at == EXPECTED_SALE_START_UTC
    assert event.event_start_at.date() != event.sale_start_at.date()
    assert event.ticket_types == []
    assert event.status is EventStatus.UNKNOWN


async def test_fetch_event_metadata_rejects_foreign_url(
    kktix_client: httpx.AsyncClient,
) -> None:
    with pytest.raises(KKTIXParseError):
        await make_resolver(kktix_client).fetch_event_metadata(
            "https://tixcraft.com/activity/detail/26_atarayo"
        )


async def test_fetch_event_metadata_rejects_404(
    kktix_client: httpx.AsyncClient,
) -> None:
    with pytest.raises(KKTIXResolveError):
        await make_resolver(kktix_client).fetch_event_metadata(
            "https://atarayo.kktix.cc/events/missing-event"
        )


async def test_resolve_auto_selects_high_score(kktix_client: httpx.AsyncClient) -> None:
    result = await make_resolver(kktix_client).resolve(
        "Atarayo Asia Tour 2026 — Taipei"
    )
    assert result.auto_selected is True
    assert result.event is not None
    assert result.threshold == MATCH_THRESHOLD
    assert result.query == "Atarayo Asia Tour 2026 — Taipei"
    assert result.candidates[0].score >= MATCH_THRESHOLD


async def test_resolve_low_score_returns_candidates(
    kktix_client: httpx.AsyncClient,
) -> None:
    result = await make_resolver(kktix_client).resolve("完全不相關的關鍵字")
    assert result.auto_selected is False
    assert result.event is None
    assert result.candidates
    assert result.threshold == MATCH_THRESHOLD
    assert result.candidates[0].score < MATCH_THRESHOLD


async def test_resolve_direct_canonical_url(kktix_client: httpx.AsyncClient) -> None:
    result = await KKTIXEventResolver(kktix_client).resolve(EVENT_URL)
    assert result.auto_selected is True
    assert result.event is not None
    assert result.candidates[0].score == 1.0
    assert result.threshold == MATCH_THRESHOLD
    assert result.query == EVENT_URL


async def test_resolver_honours_custom_threshold(
    kktix_client: httpx.AsyncClient,
) -> None:
    result = await make_resolver(kktix_client, threshold=0.99).resolve(
        "完全不相關的關鍵字"
    )
    assert result.threshold == 0.99
    assert result.candidates[0].score < 0.99
    assert result.auto_selected is False
    assert result.event is None


# --- BLOCKING 3: org slug allowlist / host injection ------------------------


@pytest.mark.parametrize(
    "org",
    [
        "evil.com",
        "evil.com/atarayo",
        "atarayo?x=1",
        "user@evil.com",
        "atarayo:8080",
        "../../etc/passwd",
        "atarayo#frag",
        "ata rayo",
        "atarayo.kktix.cc",
        "127.0.0.1",
        "",
        "a" * 65,
        "org\nInjected: header",
    ],
)
def test_illegal_org_slug_rejected_at_construction(
    kktix_client: httpx.AsyncClient, org: str
) -> None:
    with pytest.raises(ValueError):
        KKTIXEventResolver(kktix_client, orgs=[org])


async def test_illegal_org_slug_rejected_in_search(
    kktix_client: httpx.AsyncClient,
) -> None:
    with pytest.raises(ValueError):
        await make_resolver(kktix_client).search("Atarayo", orgs=["evil.com/atarayo"])


async def test_illegal_org_in_query_url_never_builds_feed_host() -> None:
    seen: list[httpx.URL] = []
    async with recording_client(seen, FEED_ONE_ENTRY) as client:
        with pytest.raises(ValueError):
            await KKTIXEventResolver(client).search(
                "https://中文組織.kktix.cc/events/evil"
            )
    assert seen == []


async def test_feed_host_stays_within_kktix_domain() -> None:
    seen: list[httpx.URL] = []
    async with recording_client(seen, FEED_ONE_ENTRY) as client:
        await KKTIXEventResolver(
            client, orgs=["atarayo", "kktix-official", "org_123"]
        ).search("Atarayo")

    assert {url.host for url in seen} == {
        "atarayo.kktix.cc",
        "kktix-official.kktix.cc",
        "org_123.kktix.cc",
    }
    assert all(url.host.endswith(".kktix.cc") for url in seen)
    assert all(url.scheme == "https" for url in seen)
    assert all(url.path == "/events.json" for url in seen)


@pytest.mark.parametrize(
    "invalid_orgs",
    [
        ["atarayo\n"],
        [123],
        ["\natarayo"],
        ["atarayo\r"],
        [None],
    ],
)
def test_validate_org_slugs_rejects_newline_and_non_string(
    invalid_orgs: list[object],
) -> None:
    with pytest.raises(ValueError):
        validate_org_slugs(invalid_orgs)  # type: ignore[arg-type]


@pytest.mark.parametrize("org", ["atarayo", "kktix-official", "org_123", "A" * 64, "0"])
def test_legal_org_slug_accepted(org: str) -> None:
    assert validate_org_slugs([org]) == [org]


def test_org_slugs_are_deduped_preserving_order() -> None:
    assert validate_org_slugs(["b", "a", "b", "a"]) == ["b", "a"]


async def test_duplicate_orgs_fetch_feed_once() -> None:
    seen: list[httpx.URL] = []
    async with recording_client(seen, FEED_ONE_ENTRY) as client:
        await KKTIXEventResolver(client, orgs=["atarayo", "atarayo"]).search("Atarayo")
    assert len(seen) == 1


def test_org_count_is_capped(kktix_client: httpx.AsyncClient) -> None:
    at_cap = [f"org{index}" for index in range(MAX_ORGS_PER_SEARCH)]
    assert len(validate_org_slugs(at_cap)) == MAX_ORGS_PER_SEARCH
    with pytest.raises(ValueError):
        KKTIXEventResolver(kktix_client, orgs=[*at_cap, "one-too-many"])


# --- BLOCKING 4: network errors unified into KKTIXResolveError --------------


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("read timed out"),
        httpx.ConnectTimeout("connect timed out"),
        httpx.RemoteProtocolError("peer closed connection"),
    ],
    ids=lambda e: type(e).__name__,
)
async def test_search_wraps_http_errors(exc: httpx.HTTPError) -> None:
    async with raising_client(exc) as client:
        with pytest.raises(KKTIXResolveError) as info:
            await make_resolver(client).search("Atarayo")
    assert info.value.__cause__ is exc
    assert "HTTP request failed" in str(info.value)


@pytest.mark.parametrize(
    "exc",
    [httpx.ConnectError("connection refused"), httpx.ReadTimeout("read timed out")],
    ids=lambda e: type(e).__name__,
)
async def test_fetch_event_metadata_wraps_http_errors(exc: httpx.HTTPError) -> None:
    async with raising_client(exc) as client:
        with pytest.raises(KKTIXResolveError) as info:
            await make_resolver(client).fetch_event_metadata(EVENT_URL)
    assert info.value.__cause__ is exc


async def test_resolve_wraps_http_errors() -> None:
    exc = httpx.ReadTimeout("read timed out")
    async with raising_client(exc) as client:
        with pytest.raises(KKTIXResolveError) as info:
            await make_resolver(client).resolve("Atarayo")
    assert info.value.__cause__ is exc


# --- BLOCKING 2: empty ticket parse must not degrade to [] -----------------

BROKEN_TICKET_PAGE = """<!DOCTYPE html>
<html><body>
  <div class="header-title"><h1>Atarayo Asia Tour 2026 — Taipei</h1></div>
  <div class="tickets"><table><tbody>
    <tr><td class="renamed-name">預售全區站席</td><td class="renamed-price">2800</td></tr>
  </tbody></table></div>
</body></html>
"""

EMPTY_TABLE_PAGE = """<!DOCTYPE html>
<html><body>
  <div class="header-title"><h1>Atarayo Asia Tour 2026 — Taipei</h1></div>
  <div class="tickets"><table></table></div>
</body></html>
"""


UNRELATED_TABLE_PAGE = """<!DOCTYPE html>
<html><body>
  <div class="header-title"><h1>Atarayo Asia Tour 2026 — Taipei</h1></div>
  <table class="schedule-table">
    <tbody>
      <tr><td>2026-03-14</td><td>台北小巨蛋</td></tr>
    </tbody>
  </table>
</body></html>
"""


@pytest.mark.parametrize("body", [BROKEN_TICKET_PAGE, EMPTY_TABLE_PAGE])
async def test_ticket_block_present_but_unparseable_raises(body: str) -> None:
    async with html_client(body) as client:
        with pytest.raises(KKTIXParseError) as info:
            await make_resolver(client).fetch_event_metadata(EVENT_URL)
    assert "ticket block present" in str(info.value)


async def test_page_with_unrelated_table_does_not_falsely_detect_ticket_block() -> None:
    async with html_client(UNRELATED_TABLE_PAGE) as client:
        event = await make_resolver(client).fetch_event_metadata(EVENT_URL)
    assert event.ticket_types == []


async def test_page_without_ticket_block_may_have_no_tickets(
    kktix_client: httpx.AsyncClient,
) -> None:
    event = await make_resolver(kktix_client).fetch_event_metadata(FULLWIDTH_URL)
    assert event.ticket_types == []


# --- Advisory: threshold / limit input validation --------------------------


@pytest.mark.parametrize("threshold", [-0.01, 1.01, -1.0, 2.0])
def test_invalid_threshold_rejected(
    kktix_client: httpx.AsyncClient, threshold: float
) -> None:
    with pytest.raises(ValueError):
        KKTIXEventResolver(kktix_client, orgs=["atarayo"], threshold=threshold)


@pytest.mark.parametrize("threshold", [0.0, 0.5, 1.0])
def test_boundary_threshold_accepted(
    kktix_client: httpx.AsyncClient, threshold: float
) -> None:
    resolver = KKTIXEventResolver(kktix_client, orgs=["atarayo"], threshold=threshold)
    assert resolver._threshold == threshold


@pytest.mark.parametrize("limit", [0, -1])
async def test_invalid_limit_rejected(
    kktix_client: httpx.AsyncClient, limit: int
) -> None:
    with pytest.raises(ValueError):
        await make_resolver(kktix_client).search("Atarayo", limit=limit)


async def test_limit_one_accepted(kktix_client: httpx.AsyncClient) -> None:
    assert len(await make_resolver(kktix_client).search("Atarayo", limit=1)) == 1
