"""票券平台工廠：依網址或平台代碼派發對應的 Adapter 與 Resolver。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from domain.event import PlatformEnum

if TYPE_CHECKING:
    import httpx

    from adapters.ticketing.base import EventResolver, TicketingAdapter
    from domain.preference import TicketPreference


class UnsupportedPlatformError(ValueError):
    """URL 主機名或平台代碼不屬於支援的票券平台。"""


def _matches_host(hostname: str, domain: str) -> bool:
    return hostname == domain or hostname.endswith(f".{domain}")


def detect_platform(event_url: str) -> PlatformEnum:
    """依網址的主機名判定票券平台。

    只依 `urlsplit(url).hostname` 比對白名單，避免 query 或 path 嵌入的第三方網址影響判定。
    未知 host 丟 `UnsupportedPlatformError`。
    """
    try:
        parsed = urlsplit(event_url)
    except Exception as exc:
        raise UnsupportedPlatformError(f"Invalid URL format: {event_url}") from exc

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise UnsupportedPlatformError(f"No hostname found in URL: {event_url}")

    if _matches_host(hostname, "kktix.com") or _matches_host(hostname, "kktix.cc"):
        return PlatformEnum.KKTIX

    if _matches_host(hostname, "tixcraft.com") or _matches_host(hostname, "ticketmaster.sg"):
        return PlatformEnum.TIXCRAFT

    if _matches_host(hostname, "ibon.com.tw"):
        return PlatformEnum.IBON

    raise UnsupportedPlatformError(f"Unsupported ticketing platform host: {hostname}")


def build_adapter(
    platform: PlatformEnum | str,
    *,
    ticket_preference: TicketPreference | None = None,
    **kwargs: Any,
) -> TicketingAdapter:
    """建構指定平台的 TicketingAdapter。"""
    try:
        plat = PlatformEnum(platform) if isinstance(platform, str) else platform
    except ValueError as exc:
        raise UnsupportedPlatformError(f"Unsupported platform: {platform}") from exc

    if plat is PlatformEnum.KKTIX:
        from adapters.ticketing.kktix.adapter import KKTIXAdapter

        return KKTIXAdapter(ticket_preference=ticket_preference, **kwargs)

    if plat is PlatformEnum.TIXCRAFT:
        from adapters.ticketing.tixcraft.adapter import TixcraftAdapter

        return TixcraftAdapter(ticket_preference=ticket_preference, **kwargs)

    if plat is PlatformEnum.IBON:
        from adapters.ticketing.ibon.adapter import IbonAdapter

        return IbonAdapter(ticket_preference=ticket_preference, **kwargs)

    raise UnsupportedPlatformError(f"Unsupported platform: {plat}")


def build_resolver(
    platform: PlatformEnum | str,
    client: httpx.AsyncClient | None = None,
    **kwargs: Any,
) -> EventResolver:
    """建構指定平台的 EventResolver。"""
    try:
        plat = PlatformEnum(platform) if isinstance(platform, str) else platform
    except ValueError as exc:
        raise UnsupportedPlatformError(f"Unsupported platform: {platform}") from exc

    if client is None:
        import httpx

        client = httpx.AsyncClient()

    if plat is PlatformEnum.KKTIX:
        from adapters.ticketing.kktix.resolver import KKTIXEventResolver

        return KKTIXEventResolver(client=client, **kwargs)

    if plat is PlatformEnum.TIXCRAFT:
        from adapters.ticketing.tixcraft.resolver import TixcraftEventResolver

        return TixcraftEventResolver(client=client, **kwargs)

    if plat is PlatformEnum.IBON:
        from adapters.ticketing.ibon.resolver import IbonEventResolver

        return IbonEventResolver(client=client, **kwargs)

    raise UnsupportedPlatformError(f"Unsupported platform: {plat}")
