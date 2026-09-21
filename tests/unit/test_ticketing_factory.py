"""票券平台工廠單元測試。"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from adapters.ticketing.factory import (
    UnsupportedPlatformError,
    build_adapter,
    build_resolver,
    detect_platform,
)
from adapters.ticketing.ibon.adapter import IbonAdapter
from adapters.ticketing.ibon.resolver import IbonEventResolver
from adapters.ticketing.kktix.adapter import KKTIXAdapter
from adapters.ticketing.kktix.resolver import KKTIXEventResolver
from adapters.ticketing.tixcraft.adapter import TixcraftAdapter
from adapters.ticketing.tixcraft.resolver import TixcraftEventResolver
from domain.event import PlatformEnum
from domain.preference import TicketPreference, TicketPriority


def test_detect_platform_kktix() -> None:
    assert detect_platform("https://kktix.com/events/12345") == PlatformEnum.KKTIX
    assert detect_platform("https://org.kktix.cc/events/abc") == PlatformEnum.KKTIX
    assert detect_platform("http://sub.org.kktix.com/events/new") == PlatformEnum.KKTIX


def test_detect_platform_tixcraft() -> None:
    assert (
        detect_platform("https://tixcraft.com/activity/detail/24_concert")
        == PlatformEnum.TIXCRAFT
    )
    assert (
        detect_platform("https://sub.tixcraft.com/ticket/area/1")
        == PlatformEnum.TIXCRAFT
    )
    assert (
        detect_platform("https://ticketmaster.sg/activity/detail/123")
        == PlatformEnum.TIXCRAFT
    )


def test_detect_platform_ibon() -> None:
    assert (
        detect_platform("https://ticket.ibon.com.tw/ActivityInfo/Details/38000")
        == PlatformEnum.IBON
    )
    assert detect_platform("https://ibon.com.tw/some/path") == PlatformEnum.IBON


def test_detect_platform_unknown_and_invalid() -> None:
    with pytest.raises(UnsupportedPlatformError):
        detect_platform("https://example.com/events/123")

    with pytest.raises(UnsupportedPlatformError):
        detect_platform("https://notkktix.com/events/123")

    with pytest.raises(UnsupportedPlatformError):
        detect_platform("https://tixcraft.com.evil.com/events")

    with pytest.raises(UnsupportedPlatformError):
        detect_platform("")


def test_detect_platform_malicious_query_embedding() -> None:
    # 惡意網站在 query 中嵌入 kktix.com，不應被誤判為 kktix
    with pytest.raises(UnsupportedPlatformError):
        detect_platform("https://evil.com/events?redirect=https://kktix.com")

    # 合法 kktix 網站在 query 帶有其他網址，仍應判定為 kktix
    assert (
        detect_platform("https://kktix.com/events?other=https://tixcraft.com")
        == PlatformEnum.KKTIX
    )


def test_build_adapter_ticket_preference_propagation() -> None:
    pref = TicketPreference(
        quantity=4,
        priorities=[TicketPriority(price=3200, ticket_name_pattern="A區")],
    )
    telemetry = MagicMock()
    payment = MagicMock()

    # KKTIX
    kktix_adapter = build_adapter(
        PlatformEnum.KKTIX,
        ticket_preference=pref,
        telemetry=telemetry,
        payment=payment,
    )
    assert isinstance(kktix_adapter, KKTIXAdapter)
    assert kktix_adapter.ticket_preference == pref
    assert kktix_adapter._target_quantity == 4

    # Tixcraft
    tix_adapter = build_adapter(
        PlatformEnum.TIXCRAFT,
        ticket_preference=pref,
    )
    assert isinstance(tix_adapter, TixcraftAdapter)
    assert tix_adapter.ticket_preference == pref
    assert tix_adapter._target_quantity == 4

    # ibon
    ibon_adapter = build_adapter(
        "ibon",  # 支援字串
        ticket_preference=pref,
    )
    assert isinstance(ibon_adapter, IbonAdapter)
    assert ibon_adapter.ticket_preference == pref
    assert ibon_adapter._target_quantity == 4


def test_build_adapter_unsupported_platform() -> None:
    with pytest.raises(UnsupportedPlatformError):
        build_adapter("unknown_platform")


def test_build_resolver_dispatch() -> None:
    client = httpx.AsyncClient()

    kktix_res = build_resolver(PlatformEnum.KKTIX, client=client)
    assert isinstance(kktix_res, KKTIXEventResolver)

    tix_res = build_resolver(PlatformEnum.TIXCRAFT, client=client)
    assert isinstance(tix_res, TixcraftEventResolver)

    ibon_res = build_resolver("ibon", client=client)
    assert isinstance(ibon_res, IbonEventResolver)

    with pytest.raises(UnsupportedPlatformError):
        build_resolver("invalid_platform", client=client)
