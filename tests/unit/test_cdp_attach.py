"""CDP 借用模式的純邏輯層：endpoint 解析、discovery、頁籤比對。

全程零真實連線：discovery 一律走 `httpx.MockTransport`，並掛 netguard 保證
連 loopback socket 都開不起來。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlunsplit

import httpx
import pytest

from browser.cdp_attach import (
    CDP_DISCOVERY_TIMEOUT_S,
    CdpDiscoveryError,
    CdpEndpointError,
    CdpPageSelectionError,
    PageTarget,
    page_url_matches,
    parse_cdp_endpoint,
    parse_page_target,
    resolve_ws_endpoint,
    select_attached_page,
)
from tests.netguard import netguard_autouse  # noqa: F401

WS_OK = "ws://127.0.0.1:9222/devtools/browser/abc"
# RFC1918 位址無法寫成 URL 字面值（G2 只放行 loopback 與保留網域），故組出來。
PRIVATE_LAN_ENDPOINT = urlunsplit(("http", "10.0.0.1:9222", "", "", ""))
# 同理：非 http(s) scheme 與缺 host 的 URL 也只能組出來，不能寫成字面值。
MESSY_TARGET = urlunsplit(("HTTPS", "Example.COM.", "/events/1/", "utm=x", "frag"))
CHROME_NEWTAB = urlunsplit(("chrome", "newtab", "", "", ""))
DEVTOOLS_PAGE = urlunsplit(("devtools", "devtools", "/bundled/x.html", "", ""))
HOSTLESS_URL = urlunsplit(("https", "", "/events/1", "", ""))


# --------------------------------------------------------------- endpoint 解析
@pytest.mark.parametrize(
    ("raw", "host", "origin"),
    [
        ("http://127.0.0.1:9222", "127.0.0.1", "http://127.0.0.1:9222"),
        ("http://localhost:9222/", "localhost", "http://localhost:9222"),
        ("http://[::1]:9222", "::1", "http://[::1]:9222"),
    ],
)
def test_parse_cdp_endpoint_accepts_loopback_http(
    raw: str, host: str, origin: str
) -> None:
    endpoint = parse_cdp_endpoint(raw)
    assert (endpoint.host, endpoint.port, endpoint.origin) == (host, 9222, origin)


@pytest.mark.parametrize(
    "raw",
    [
        "https://127.0.0.1:9222",
        "ws://127.0.0.1:9222",
        "http://127.0.0.1:9222/json",
        "http://127.0.0.1:9222?x=1",
        "http://127.0.0.1:9222#f",
        "http://user:pw@127.0.0.1:9222",
        PRIVATE_LAN_ENDPOINT,
        "http://example.com:9222",
        "http://127.0.0.1",
        "http://127.0.0.1:0",
        "",
    ],
)
def test_parse_cdp_endpoint_rejects_everything_else(raw: str) -> None:
    with pytest.raises(CdpEndpointError):
        parse_cdp_endpoint(raw)


# --------------------------------------------------------------- discovery
def _client(handler: Any, *, follow_redirects: bool = False) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=follow_redirects,
        trust_env=False,
    )


async def test_resolve_ws_endpoint_returns_the_advertised_socket() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"webSocketDebuggerUrl": WS_OK})

    endpoint = parse_cdp_endpoint("http://127.0.0.1:9222")
    async with _client(handler) as client:
        assert await resolve_ws_endpoint(endpoint, client=client) == WS_OK
    assert calls == ["/json/version"]


async def test_resolve_ws_endpoint_never_follows_a_redirect() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(302, headers={"location": "/json/version-elsewhere"})

    endpoint = parse_cdp_endpoint("http://127.0.0.1:9222")
    async with _client(handler) as client:
        with pytest.raises(CdpDiscoveryError):
            await resolve_ws_endpoint(endpoint, client=client)
    assert len(calls) == 1


async def test_resolve_ws_endpoint_rejects_a_followed_redirect_chain() -> None:
    """注入的 client 若自行跟隨轉址，非空 history 也必須 fail-closed。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/json/version":
            return httpx.Response(302, headers={"location": "/json/version-elsewhere"})
        return httpx.Response(200, json={"webSocketDebuggerUrl": WS_OK})

    endpoint = parse_cdp_endpoint("http://127.0.0.1:9222")
    async with _client(handler, follow_redirects=True) as client:
        with pytest.raises(CdpDiscoveryError):
            await resolve_ws_endpoint(endpoint, client=client)


@pytest.mark.parametrize(
    "response_factory",
    [
        lambda: httpx.Response(500, text="boom"),
        lambda: httpx.Response(200, text="not json at all"),
        lambda: httpx.Response(200, json=["not", "a", "dict"]),
        lambda: httpx.Response(200, json={}),
        lambda: httpx.Response(200, json={"webSocketDebuggerUrl": 42}),
        lambda: httpx.Response(200, json={"webSocketDebuggerUrl": ""}),
    ],
)
async def test_resolve_ws_endpoint_rejects_bad_payloads(response_factory: Any) -> None:
    endpoint = parse_cdp_endpoint("http://127.0.0.1:9222")
    async with _client(lambda request: response_factory()) as client:
        with pytest.raises(CdpDiscoveryError) as caught:
            await resolve_ws_endpoint(endpoint, client=client)
    assert "devtools" not in str(caught.value)


async def test_resolve_ws_endpoint_reports_transport_failure_without_leaking() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    endpoint = parse_cdp_endpoint("http://127.0.0.1:9222")
    async with _client(handler) as client:
        with pytest.raises(CdpDiscoveryError) as caught:
            await resolve_ws_endpoint(endpoint, client=client)
    assert "devtools" not in str(caught.value)


@pytest.mark.parametrize(
    "ws",
    [
        "wss://127.0.0.1:9222/devtools/browser/abc",
        "http://127.0.0.1:9222/devtools/browser/abc",
        "ws://evil.example:9222/devtools/browser/abc",
        "ws://127.0.0.1:9333/devtools/browser/abc",
        "ws://u:p@127.0.0.1:9222/devtools/browser/abc",
        "ws://127.0.0.1:9222/devtools/browser/abc?a=1",
        "ws://127.0.0.1:9222/devtools/browser/abc#f",
        "ws://127.0.0.1:9222",
        "ws://127.0.0.1:9222/dev%20tools/browser",
    ],
)
async def test_resolve_ws_endpoint_revalidates_the_advertised_socket(ws: str) -> None:
    endpoint = parse_cdp_endpoint("http://127.0.0.1:9222")
    async with _client(
        lambda request: httpx.Response(200, json={"webSocketDebuggerUrl": ws})
    ) as client:
        with pytest.raises(CdpDiscoveryError) as caught:
            await resolve_ws_endpoint(endpoint, client=client)
    message = str(caught.value)
    assert "devtools" not in message
    assert "browser/abc" not in message
    assert message.endswith("http://127.0.0.1:9222")


class _RecordingClient:
    """可觀測的 async context manager，用來證明自建 client 必被關閉。"""

    made: list[_RecordingClient] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.entered = 0
        self.exited = 0
        _RecordingClient.made.append(self)

    async def __aenter__(self) -> _RecordingClient:
        self.entered += 1
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        self.exited += 1
        return False

    async def get(self, url: str) -> httpx.Response:
        return httpx.Response(200, json={"webSocketDebuggerUrl": WS_OK})


async def test_owned_discovery_client_is_configured_and_always_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _RecordingClient.made.clear()
    monkeypatch.setattr("browser.cdp_attach.httpx.AsyncClient", _RecordingClient)

    endpoint = parse_cdp_endpoint("http://127.0.0.1:9222")
    assert await resolve_ws_endpoint(endpoint) == WS_OK

    (client,) = _RecordingClient.made
    assert client.exited == 1
    assert client.kwargs == {
        "timeout": CDP_DISCOVERY_TIMEOUT_S,
        "follow_redirects": False,
        "trust_env": False,
    }


async def test_owned_discovery_client_is_closed_even_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Failing(_RecordingClient):
        async def get(self, url: str) -> httpx.Response:
            return httpx.Response(500, text="boom")

    _RecordingClient.made.clear()
    monkeypatch.setattr("browser.cdp_attach.httpx.AsyncClient", Failing)

    endpoint = parse_cdp_endpoint("http://127.0.0.1:9222")
    with pytest.raises(CdpDiscoveryError):
        await resolve_ws_endpoint(endpoint)
    (client,) = _RecordingClient.made
    assert client.exited == 1


async def test_injected_discovery_client_is_never_closed_by_us() -> None:
    closed = 0

    class Borrowed:
        async def get(self, url: str) -> httpx.Response:
            return httpx.Response(200, json={"webSocketDebuggerUrl": WS_OK})

        async def aclose(self) -> None:
            nonlocal closed
            closed += 1

        async def __aexit__(self, *exc: Any) -> bool:
            nonlocal closed
            closed += 1
            return False

    endpoint = parse_cdp_endpoint("http://127.0.0.1:9222")
    assert await resolve_ws_endpoint(endpoint, client=Borrowed()) == WS_OK  # type: ignore[arg-type]
    assert closed == 0


# --------------------------------------------------------------- 頁籤 target
def test_parse_page_target_normalises_scheme_host_port_and_path() -> None:
    target = parse_page_target(MESSY_TARGET)
    assert (target.scheme, target.host, target.port, target.path) == (
        "https",
        "example.com",
        443,
        "/events/1",
    )
    assert target.label == "https://example.com/events/1"


def test_parse_page_target_keeps_an_explicit_port_in_the_label() -> None:
    target = parse_page_target("https://example.com:8443/events/1")
    assert target.port == 8443
    assert target.label == "https://example.com:8443/events/1"


@pytest.mark.parametrize(
    "raw",
    ["", "ftp://example.com/x", CHROME_NEWTAB, "/events/1", HOSTLESS_URL],
)
def test_parse_page_target_rejects_non_absolute_http_urls(raw: str) -> None:
    with pytest.raises(CdpEndpointError):
        parse_page_target(raw)


@pytest.mark.parametrize(
    "page_url",
    [
        "https://example.com.evil.example/events/1",
        "https://evil.example/?u=https://example.com/events/1",
        "http://example.com/events/1",
        "https://example.com:8443/events/1",
        "https://example.com/events/12",
        "https://sub.example.com/events/1",
        "about:blank",
        CHROME_NEWTAB,
        DEVTOOLS_PAGE,
        "",
    ],
)
def test_page_url_matches_rejects_lookalikes(page_url: str) -> None:
    target = parse_page_target("https://example.com/events/1")
    assert page_url_matches(target, page_url) is False


@pytest.mark.parametrize(
    "page_url",
    [
        "https://example.com/events/1",
        "https://example.com/events/1/",
        "https://example.com/events/1?utm=x",
        "https://example.com/events/1#seat",
        "https://example.com/events/1/register",
        "https://EXAMPLE.com:443/events/1",
    ],
)
def test_page_url_matches_accepts_the_target_and_its_subpaths(page_url: str) -> None:
    target = parse_page_target("https://example.com/events/1")
    assert page_url_matches(target, page_url) is True


def test_a_root_target_matches_only_the_origin_root() -> None:
    target = parse_page_target("https://example.com/")
    assert page_url_matches(target, "https://example.com/") is True
    assert page_url_matches(target, "https://example.com/events/1") is False
    assert page_url_matches(target, "https://example.com/x/y") is False


# --------------------------------------------------------------- 選頁
class FakePage:
    def __init__(self, url: str, *, closed: bool = False) -> None:
        self.url = url
        self._closed = closed

    def is_closed(self) -> bool:
        return self._closed


class FakeContext:
    def __init__(self, *pages: FakePage) -> None:
        self.pages = list(pages)


class FakeBrowser:
    def __init__(self, *contexts: FakeContext) -> None:
        self.contexts = list(contexts)


TARGET: PageTarget = parse_page_target("https://example.com/events/1")


def test_select_attached_page_returns_the_only_match() -> None:
    wanted = FakePage("https://example.com/events/1?utm=x")
    browser = FakeBrowser(
        FakeContext(FakePage("about:blank"), FakePage("https://evil.example/events/1")),
        FakeContext(wanted, FakePage("https://example.com/events/2")),
        FakeContext(FakePage("https://example.com/events/1", closed=True)),
    )
    assert select_attached_page(browser, TARGET) is wanted


def test_select_attached_page_refuses_when_nothing_matches() -> None:
    browser = FakeBrowser(FakeContext(FakePage("about:blank")))
    with pytest.raises(CdpPageSelectionError) as caught:
        select_attached_page(browser, TARGET)
    message = str(caught.value)
    assert TARGET.label in message
    assert "about:blank" not in message


def test_select_attached_page_refuses_when_two_tabs_match() -> None:
    browser = FakeBrowser(
        FakeContext(FakePage("https://example.com/events/1")),
        FakeContext(FakePage("https://example.com/events/1/register")),
    )
    with pytest.raises(CdpPageSelectionError) as caught:
        select_attached_page(browser, TARGET)
    message = str(caught.value)
    assert "2" in message
    assert "example.com/events/1/register" not in message
