from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
CDP_DISCOVERY_TIMEOUT_S = 5.0
CDP_CONNECT_TIMEOUT_MS = 10_000
WS_PATH_RE = re.compile(r"\A/[A-Za-z0-9/_.-]*\Z")


class CdpEndpointError(ValueError):
    """Raised when a user-provided CDP endpoint or page target is invalid."""


class CdpDiscoveryError(RuntimeError):
    """Raised when local Chrome discovery does not return a safe websocket URL."""


class CdpAttachError(RuntimeError):
    """Raised when Playwright cannot attach without exposing its raw websocket error."""


class CdpPageSelectionError(RuntimeError):
    """Raised when the target page cannot be selected unambiguously."""


@dataclass(frozen=True, slots=True)
class CdpEndpoint:
    host: str
    port: int
    origin: str


@dataclass(frozen=True, slots=True)
class PageTarget:
    scheme: str
    host: str
    port: int
    path: str
    label: str
    fallback_paths: tuple[str, ...] = ()


def _normalise_host(host: str) -> str:
    return host.lower().rstrip(".")


def _display_host(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def _parsed_port(parts: Any, *, required: bool, default: int | None = None) -> int:
    try:
        port = parts.port
    except ValueError as exc:
        raise CdpEndpointError("port 必須介於 1 到 65535") from exc
    if port is None:
        if required or default is None:
            raise CdpEndpointError("CDP endpoint 必須明確指定 port")
        return default
    if not 1 <= port <= 65535:
        raise CdpEndpointError("port 必須介於 1 到 65535")
    return port


def parse_cdp_endpoint(raw: str) -> CdpEndpoint:
    if not isinstance(raw, str) or not raw.strip():
        raise CdpEndpointError("CDP endpoint 不可為空")
    try:
        parts = urlsplit(raw)
    except ValueError as exc:
        raise CdpEndpointError("CDP endpoint 格式無效") from exc
    if parts.scheme != "http":
        raise CdpEndpointError("CDP endpoint 僅接受本機 http:// URL")
    if parts.username is not None or parts.password is not None:
        raise CdpEndpointError("CDP endpoint 不得包含 userinfo")
    if parts.query or parts.fragment:
        raise CdpEndpointError("CDP endpoint 不得包含 query 或 fragment")
    if parts.path not in ("", "/"):
        raise CdpEndpointError("CDP endpoint 不得包含 path")
    host = _normalise_host(parts.hostname or "")
    if host not in LOOPBACK_HOSTS:
        raise CdpEndpointError("CDP endpoint 只支援本機 loopback host")
    port = _parsed_port(parts, required=True)
    return CdpEndpoint(
        host=host, port=port, origin=f"http://{_display_host(host)}:{port}"
    )


def parse_page_target(
    raw: str, fallback_urls: Sequence[str] | None = None
) -> PageTarget:
    if not isinstance(raw, str) or not raw.strip():
        raise CdpEndpointError("CDP 頁籤 target 不可為空")
    try:
        parts = urlsplit(raw)
    except ValueError as exc:
        raise CdpEndpointError("CDP 頁籤 target 格式無效") from exc
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise CdpEndpointError("CDP 頁籤 target 必須是 http(s) 絕對 URL")
    if parts.username is not None or parts.password is not None:
        raise CdpEndpointError("CDP 頁籤 target 不得包含 userinfo")
    host = _normalise_host(parts.hostname or "")
    if not host:
        raise CdpEndpointError("CDP 頁籤 target 必須包含 host")
    default_port = 80 if scheme == "http" else 443
    port = _parsed_port(parts, required=False, default=default_port)
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    visible_port = "" if port == default_port else f":{port}"
    label = f"{scheme}://{_display_host(host)}{visible_port}{path}"

    fallback_paths: list[str] = []
    if fallback_urls:
        for fb in fallback_urls:
            if not isinstance(fb, str) or not fb.strip():
                continue
            try:
                fb_parts = urlsplit(fb)
            except ValueError as exc:
                raise CdpEndpointError("CDP 頁籤 fallback target 格式無效") from exc
            fb_scheme = fb_parts.scheme.lower()
            if fb_scheme not in {"http", "https"}:
                raise CdpEndpointError(
                    "CDP 頁籤 fallback target 必須是 http(s) 絕對 URL"
                )
            fb_host = _normalise_host(fb_parts.hostname or "")
            if fb_host != host:
                raise CdpEndpointError(
                    "CDP 頁籤 fallback target 的 host 必須與主 target 相同"
                )
            fb_port = _parsed_port(fb_parts, required=False, default=default_port)
            if fb_port != port:
                raise CdpEndpointError(
                    "CDP 頁籤 fallback target 的 port 必須與主 target 相同"
                )
            fb_path = fb_parts.path or "/"
            if fb_path != "/":
                fb_path = fb_path.rstrip("/") or "/"
            if fb_path != path and fb_path not in fallback_paths:
                fallback_paths.append(fb_path)

    return PageTarget(
        scheme=scheme,
        host=host,
        port=port,
        path=path,
        label=label,
        fallback_paths=tuple(fallback_paths),
    )


def _path_matches(expected_path: str, actual_path: str) -> bool:
    if expected_path == "/":
        return actual_path == "/"
    return actual_path == expected_path or actual_path.startswith(f"{expected_path}/")


def page_url_matches(
    target: PageTarget, page_url: str, *, allow_fallback: bool = False
) -> bool:
    try:
        parts = urlsplit(page_url)
        scheme = parts.scheme.lower()
        if scheme not in {"http", "https"}:
            return False
        if parts.username is not None or parts.password is not None:
            return False
        host = _normalise_host(parts.hostname or "")
        default_port = 80 if scheme == "http" else 443
        port = _parsed_port(parts, required=False, default=default_port)
    except (CdpEndpointError, TypeError, ValueError):
        return False
    if (scheme, host, port) != (target.scheme, target.host, target.port):
        return False
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    if _path_matches(target.path, path):
        return True
    if allow_fallback:
        return any(_path_matches(fb, path) for fb in target.fallback_paths)
    return False


def select_attached_page(browser: Any, target: PageTarget) -> Any:
    primary_matches: list[Any] = []
    fallback_matches: list[Any] = []
    scanned = 0
    contexts = list(browser.contexts)
    for context in contexts:
        for page in context.pages:
            if page.is_closed():
                continue
            scanned += 1
            if page_url_matches(target, page.url, allow_fallback=False):
                primary_matches.append(page)
            elif target.fallback_paths and page_url_matches(
                target, page.url, allow_fallback=True
            ):
                fallback_matches.append(page)

    if len(primary_matches) == 1:
        return primary_matches[0]

    if len(primary_matches) == 0 and len(fallback_matches) == 1:
        return fallback_matches[0]

    total_matches = len(primary_matches) + len(fallback_matches)
    raise CdpPageSelectionError(
        f"CDP 頁籤 target {target.label} 命中 {total_matches} 個頁籤"
        f"（已掃描 {scanned} 個頁籤、{len(contexts)} 個可見 context）；必須恰好命中 1 個"
    )


def validate_ws_endpoint(raw: Any, endpoint: CdpEndpoint) -> str:
    if not isinstance(raw, str) or not raw:
        raise CdpDiscoveryError(f"本機 CDP discovery 回應無效：{endpoint.origin}")
    try:
        parts = urlsplit(raw)
        port = parts.port
    except (TypeError, ValueError):
        raise CdpDiscoveryError(
            f"本機 CDP discovery 回應無效：{endpoint.origin}"
        ) from None
    host = _normalise_host(parts.hostname or "")
    valid = (
        parts.scheme == "ws"
        and parts.username is None
        and parts.password is None
        and not parts.query
        and not parts.fragment
        and host in LOOPBACK_HOSTS
        and port == endpoint.port
        and bool(parts.path)
        and WS_PATH_RE.fullmatch(parts.path) is not None
    )
    if not valid:
        raise CdpDiscoveryError(f"本機 CDP discovery 回應無效：{endpoint.origin}")
    return raw


async def _discover_with_client(
    endpoint: CdpEndpoint, client: httpx.AsyncClient
) -> str:
    try:
        response = await client.get(f"{endpoint.origin}/json/version")
    except httpx.HTTPError:
        raise CdpDiscoveryError(
            f"無法讀取本機 CDP discovery：{endpoint.origin}"
        ) from None
    if response.history or response.is_redirect or response.status_code != 200:
        raise CdpDiscoveryError(
            f"本機 CDP discovery 回傳 HTTP {response.status_code}：{endpoint.origin}"
        )
    try:
        payload = response.json()
    except (TypeError, ValueError):
        raise CdpDiscoveryError(
            f"本機 CDP discovery 回應不是有效 JSON：{endpoint.origin}"
        ) from None
    if not isinstance(payload, dict):
        raise CdpDiscoveryError(f"本機 CDP discovery 回應格式無效：{endpoint.origin}")
    return validate_ws_endpoint(payload.get("webSocketDebuggerUrl"), endpoint)


async def resolve_ws_endpoint(
    endpoint: CdpEndpoint,
    *,
    client: httpx.AsyncClient | None = None,
) -> str:
    if client is not None:
        return await _discover_with_client(endpoint, client)
    async with httpx.AsyncClient(
        timeout=CDP_DISCOVERY_TIMEOUT_S,
        follow_redirects=False,
        trust_env=False,
    ) as owned_client:
        return await _discover_with_client(endpoint, owned_client)
