"""雙軌時鐘同步與 SSRF 白名單。"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest

from scheduler.clock_sync import (
    DEFAULT_KKTIX_ALLOWED_HOSTS,
    DEFAULT_NTP_HOST,
    ClockOffsetUpdate,
    ClockSample,
    ClockSource,
    ClockSyncError,
    ClockSynchronizer,
    NtpClockSync,
    ServerHeaderClockSync,
    TimeReference,
)
from tests.netguard import netguard_autouse  # noqa: F401

HEADER_DATE = "Sun, 01 Mar 2026 12:00:00 GMT"
HEADER_DATE_UTC = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
PROBE_URL = "https://kktix.com/events/1"


class FakePerf:
    def __init__(self, values: list[float]) -> None:
        self._values = list(values)
        self._last = values[-1]

    def __call__(self) -> float:
        if self._values:
            self._last = self._values.pop(0)
        return self._last


class FakeHttpClient:
    """只回應 HEAD 的假 client，記錄呼叫參數。"""

    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def head(self, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append((url, kwargs))
        return self.response


class ExplodingHttpClient:
    def __init__(self) -> None:
        self.called = False

    async def head(self, url: str, **kwargs: Any) -> httpx.Response:
        self.called = True
        raise AssertionError("network probe must not be attempted for disallowed host")


class FakeNtpResponse:
    def __init__(self, offset: float, delay: float, stratum: int = 2) -> None:
        self.offset = offset
        self.delay = delay
        self.stratum = stratum


class FakeNtpClient:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.hosts: list[str] = []

    def request(self, host: str, timeout: float = 3.0) -> Any:
        self.hosts.append(host)
        if self.error is not None:
            raise self.error
        return self.response


def head_response(status_code: int = 200, date: str | None = HEADER_DATE,
                  extra_headers: dict[str, str] | None = None) -> httpx.Response:
    headers = dict(extra_headers or {})
    if date is not None:
        headers["Date"] = date
    return httpx.Response(status_code, headers=headers, request=httpx.Request("HEAD", PROBE_URL))


# ------------------------------------------------------------ TimeReference
def test_time_reference_round_trip_conversion() -> None:
    ref = TimeReference(offset_ms=250.0)
    true_time = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
    local = ref.to_local(true_time)
    assert local == true_time - timedelta(milliseconds=250)
    assert ref.to_true(local) == true_time


def test_time_reference_with_offset_returns_new_instance() -> None:
    ref = TimeReference(offset_ms=0.0)
    updated = ref.with_offset(-40.0, ClockSource.SERVER_HEADER)
    assert ref.offset_ms == 0.0
    assert updated.offset_ms == -40.0
    assert updated.primary_source is ClockSource.SERVER_HEADER


# ------------------------------------------------------------ NTP
async def test_ntp_sample_converts_seconds_to_milliseconds() -> None:
    client = FakeNtpClient(FakeNtpResponse(offset=0.125, delay=0.05, stratum=3))
    sample = await NtpClockSync(ntp_client=client).sample("ntp.example.com")
    assert sample.source is ClockSource.NTP
    assert sample.offset_ms == pytest.approx(125.0)
    assert sample.rtt_ms == pytest.approx(50.0)
    assert sample.stratum == 3
    assert client.hosts == ["ntp.example.com"]


async def test_ntp_sample_wraps_errors_in_clock_sync_error() -> None:
    client = FakeNtpClient(error=OSError("unreachable"))
    with pytest.raises(ClockSyncError):
        await NtpClockSync(ntp_client=client).sample("ntp.example.com")


# ------------------------------------------------------------ Server header
async def test_server_header_uses_perf_counter_and_half_rtt_compensation() -> None:
    """rtt 取自 perf_counter，offset 為半 RTT 補償後之差。"""
    perf = FakePerf([100.0, 100.4])
    end_wall = HEADER_DATE_UTC + timedelta(milliseconds=500)
    probe = ServerHeaderClockSync(
        http_client=FakeHttpClient(head_response()),
        perf_counter=perf,
        wall_clock=lambda: end_wall,
    )
    sample = await probe.sample(PROBE_URL)
    assert sample.source is ClockSource.SERVER_HEADER
    assert sample.rtt_ms == pytest.approx(400.0, abs=1e-6)
    # server_estimated = 12:00:00.200；本機牆鐘 12:00:00.500 -> offset = -300ms
    assert sample.offset_ms == pytest.approx(-300.0, abs=1e-6)
    assert sample.polled_at == end_wall


async def test_server_header_rejects_error_status_and_missing_date() -> None:
    bad_status = ServerHeaderClockSync(http_client=FakeHttpClient(head_response(503)))
    with pytest.raises(ClockSyncError):
        await bad_status.sample(PROBE_URL)

    no_date = ServerHeaderClockSync(http_client=FakeHttpClient(head_response(date=None)))
    with pytest.raises(ClockSyncError):
        await no_date.sample(PROBE_URL)


# ------------------------------------------------------------ FROZEN-2
def test_default_allowlist_is_frozen_to_kktix() -> None:
    assert DEFAULT_KKTIX_ALLOWED_HOSTS == ("kktix.com", ".kktix.cc")
    assert ServerHeaderClockSync().allowed_hosts == ("kktix.com", ".kktix.cc")


@pytest.mark.parametrize(
    "url",
    [
        "https://kktix.com/events/x",
        "https://KKTIX.CoM/events/x",
        "http://kktix.com/x",
        "https://kktix.cc/x",
        "https://a.kktix.cc/x",
        "https://deep.sub.kktix.cc/x",
    ],
)
def test_allowlist_accepts_official_kktix_hosts(url: str) -> None:
    assert ServerHeaderClockSync().assert_url_allowed(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://evil.com/x",
        "https://kktix.com.evil.com/x",
        "https://notkktix.cc/x",
        "file:///etc/passwd",
        "http://169.254.169.254/latest/meta-data",
        "https:///no-host",
        "ftp://kktix.com/x",
    ],
)
def test_allowlist_blocks_everything_else(url: str) -> None:
    with pytest.raises(ClockSyncError):
        ServerHeaderClockSync().assert_url_allowed(url)


async def test_allowlist_gate_runs_before_any_request() -> None:
    """白名單閘門必須早於任何網路請求：注入的 client 不得被呼叫。"""
    client = ExplodingHttpClient()
    probe = ServerHeaderClockSync(http_client=client)  # type: ignore[arg-type]
    with pytest.raises(ClockSyncError):
        await probe.sample("http://169.254.169.254/latest/meta-data")
    assert client.called is False


def test_custom_allowlist_does_not_inherit_kktix() -> None:
    custom = ServerHeaderClockSync(allowed_hosts=(".tixcraft.com",))
    assert custom.assert_url_allowed("https://tixcraft.com/x")
    assert custom.assert_url_allowed("https://ticket.tixcraft.com/x")
    with pytest.raises(ClockSyncError):
        custom.assert_url_allowed("https://kktix.com/x")


async def test_probe_never_follows_redirects() -> None:
    """轉址目標不會再過白名單，跟隨轉址等同開後門。"""
    redirect = head_response(302, extra_headers={"Location": "http://evil.invalid/"})
    client = FakeHttpClient(redirect)
    probe = ServerHeaderClockSync(http_client=client)  # type: ignore[arg-type]
    sample = await probe.sample(PROBE_URL)
    assert len(client.calls) == 1, "轉址不得觸發第二次請求"
    assert client.calls[0][0] == PROBE_URL
    assert client.calls[0][1]["follow_redirects"] is False
    assert sample.source is ClockSource.SERVER_HEADER


# ------------------------------------------------------------ ClockSynchronizer
async def test_synchronizer_collects_dual_track_samples() -> None:
    ntp_client = FakeNtpClient(FakeNtpResponse(offset=0.01, delay=0.02))
    perf = FakePerf([100.0, 100.4])
    sync = ClockSynchronizer(
        ntp_sync=NtpClockSync(ntp_client=ntp_client),
        server_sync=ServerHeaderClockSync(
            http_client=FakeHttpClient(head_response()),
            perf_counter=perf,
            wall_clock=lambda: HEADER_DATE_UTC,
        ),
        server_url=PROBE_URL,
    )
    ref = await sync.refresh()
    assert {s.source for s in ref.samples} == {ClockSource.NTP, ClockSource.SERVER_HEADER}
    assert ref.primary_source is ClockSource.NTP
    assert ref.offset_ms == pytest.approx(10.0)
    assert ntp_client.hosts == [DEFAULT_NTP_HOST]
    assert sync.server_url == PROBE_URL


async def test_synchronizer_tolerates_server_failure_and_keeps_ntp() -> None:
    ntp_client = FakeNtpClient(FakeNtpResponse(offset=-0.5, delay=0.01))
    sync = ClockSynchronizer(
        ntp_sync=NtpClockSync(ntp_client=ntp_client),
        server_sync=ServerHeaderClockSync(http_client=ExplodingHttpClient()),  # type: ignore[arg-type]
        server_url="http://evil.com/x",
    )
    ref = await sync.refresh()
    assert [s.source for s in ref.samples] == [ClockSource.NTP]
    assert ref.primary_source is ClockSource.NTP
    assert ref.offset_ms == pytest.approx(-500.0)


async def test_synchronizer_falls_back_to_server_when_ntp_fails() -> None:
    perf = FakePerf([100.0, 100.2])
    sync = ClockSynchronizer(
        ntp_sync=NtpClockSync(ntp_client=FakeNtpClient(error=OSError("down"))),
        server_sync=ServerHeaderClockSync(
            http_client=FakeHttpClient(head_response()),
            perf_counter=perf,
            wall_clock=lambda: HEADER_DATE_UTC,
        ),
        server_url=PROBE_URL,
    )
    ref = await sync.refresh()
    assert [s.source for s in ref.samples] == [ClockSource.SERVER_HEADER]
    assert ref.primary_source is ClockSource.SERVER_HEADER


async def test_synchronizer_without_sources_returns_empty_reference() -> None:
    ref = await ClockSynchronizer(ntp_host=None, server_url=None).refresh()
    assert ref.samples == ()
    assert ref.primary_source is None
    assert ref.offset_ms == 0.0


# ------------------------------------------------------------ ClockOffsetUpdate
def test_clock_offset_update_is_frozen_with_four_fields() -> None:
    names = {f.name for f in dataclasses.fields(ClockOffsetUpdate)}
    assert names == {"offset_ms", "source", "samples", "applied_at"}
    update = ClockOffsetUpdate(
        offset_ms=1.0,
        source="NTP",
        samples=(ClockSample(source=ClockSource.NTP, offset_ms=1.0, rtt_ms=2.0),),
        applied_at=HEADER_DATE_UTC,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        update.offset_ms = 2.0  # type: ignore[misc]


def test_clock_offset_update_is_exported_from_package_root() -> None:
    from scheduler import ClockOffsetUpdate as Exported

    assert Exported is ClockOffsetUpdate
