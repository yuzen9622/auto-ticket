from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx


class ClockSyncError(Exception):
    pass


# 預設僅信任 KKTIX 官方網域。
# 無前導點 = host 必須完全相等；有前導點 = 該網域本身或其任意子網域。
# 擴充其他售票平台（拓元、ibon）時於建構時傳入 allowed_hosts，嚴禁放寬此預設值。
DEFAULT_KKTIX_ALLOWED_HOSTS: tuple[str, ...] = ("kktix.com", ".kktix.cc")
ALLOWED_URL_SCHEMES: frozenset[str] = frozenset({"http", "https"})


class ClockSource(str, Enum):
    NTP = "NTP"
    SERVER_HEADER = "SERVER_HEADER"
    MANUAL = "MANUAL"


@dataclass(frozen=True, slots=True)
class ClockSample:
    source: ClockSource
    offset_ms: float
    rtt_ms: float
    stratum: int | None = None
    polled_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TimeReference:
    offset_ms: float
    samples: tuple[ClockSample, ...] = ()
    primary_source: ClockSource | None = None
    monotonic_anchor_perf: float = 0.0
    monotonic_anchor_wall: datetime | None = None

    def to_local(self, true_time: datetime) -> datetime:
        """local = true - offset"""
        return true_time - timedelta(milliseconds=self.offset_ms)

    def to_true(self, local_time: datetime) -> datetime:
        """true = local + offset"""
        return local_time + timedelta(milliseconds=self.offset_ms)

    def with_offset(self, offset_ms: float, source: ClockSource = ClockSource.MANUAL) -> TimeReference:
        return replace(self, offset_ms=offset_ms, primary_source=source)


@dataclass(frozen=True, slots=True)
class ClockOffsetUpdate:
    """WarmupScheduler.update_clock_offset() 的回傳契約。

    offset_ms  : 本次呼叫結束後實際生效的偏移（未套用時等於呼叫前的舊值）。
    source     : 偏移來源 ID（ClockSource.value 或呼叫端傳入的自訂字串）。
    samples    : 生效的雙軌樣本快照（NTP 與 SERVER_HEADER 可同時存在）。
    applied_at : 本次套用判定的本機牆鐘時刻。
    """

    offset_ms: float
    source: str
    samples: tuple[ClockSample, ...]
    applied_at: datetime


class ClockSynchronizerLike(Protocol):
    async def refresh(self) -> TimeReference: ...


#: NTP 成功時實測 15~30ms；3 秒不是「成功要多久」而是「失敗要賠多久」。
#: 這個探測跑在預熱階段上，賠掉的每一秒都直接壓縮開賣前的準備時間。
DEFAULT_NTP_TIMEOUT_S = 0.8
#: 連續失敗幾次之後停用 NTP 軌。
NTP_FAILURE_LIMIT = 2


class NtpClockSync:
    """NTP 取樣。連續失敗就停用本軌，不再每個階段重賠一次逾時。"""

    def __init__(self, *, ntp_client: Any = None) -> None:
        self._client = ntp_client
        self._consecutive_failures = 0

    @property
    def disabled(self) -> bool:
        """連錯這麼多次就別再試了：同一個 task 的網路狀況不會在幾秒內變好。"""
        return self._consecutive_failures >= NTP_FAILURE_LIMIT

    async def sample(
        self, host: str, timeout: float = DEFAULT_NTP_TIMEOUT_S
    ) -> ClockSample:
        def _call() -> ClockSample:
            try:
                if self._client is not None:
                    res = self._client.request(host, timeout=timeout)
                else:
                    import ntplib
                    client = ntplib.NTPClient()
                    res = client.request(host, timeout=timeout)
                return ClockSample(
                    source=ClockSource.NTP,
                    offset_ms=float(res.offset) * 1000.0,
                    rtt_ms=float(res.delay) * 1000.0,
                    stratum=getattr(res, "stratum", None),
                    polled_at=datetime.now(timezone.utc),
                )
            except Exception as exc:
                if type(exc).__name__ == "NetworkAccessError":
                    raise
                raise ClockSyncError(f"NTP sample failed for {host}: {exc}") from exc

        if self.disabled:
            raise ClockSyncError(f"NTP disabled after repeated failures ({host})")
        try:
            sample = await asyncio.to_thread(_call)
        except Exception:
            self._consecutive_failures += 1
            raise
        self._consecutive_failures = 0
        return sample


class ServerHeaderClockSync:
    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        perf_counter: Callable[[], float] = time.perf_counter,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        allowed_hosts: tuple[str, ...] = DEFAULT_KKTIX_ALLOWED_HOSTS,
    ) -> None:
        self._client = http_client
        self._perf = perf_counter
        self._wall = wall_clock
        self._allowed_hosts = tuple(h.lower() for h in allowed_hosts)

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        return self._allowed_hosts

    def assert_url_allowed(self, url: str) -> str:
        """SSRF 防護閘門。

        必須在發出任何網路請求「之前」呼叫。不命中白名單一律拋 ClockSyncError，
        絕不發包（因此也不會觸發 netguard，測試可直接斷言例外型別）。
        """
        parts = urlsplit(url)
        scheme = (parts.scheme or "").lower()
        if scheme not in ALLOWED_URL_SCHEMES:
            raise ClockSyncError(f"Disallowed URL scheme for clock probe: {scheme!r}")
        host = (parts.hostname or "").lower()
        if not host:
            raise ClockSyncError(f"Clock probe URL has no host: {url!r}")
        for entry in self._allowed_hosts:
            if entry.startswith("."):
                if host == entry[1:] or host.endswith(entry):
                    return host
            elif host == entry:
                return host
        raise ClockSyncError(
            f"Host {host!r} is not in the clock-probe allowlist {self._allowed_hosts!r}"
        )

    async def sample(self, url: str) -> ClockSample:
        # SSRF 閘門置於 try 之外：白名單違規是設定錯誤，必須原樣拋出，
        # 不可被下方的通用 except 重新包裝成「取樣失敗」而被容錯路徑吞掉。
        self.assert_url_allowed(url)
        try:
            t_start_perf = self._perf()
            if self._client is not None:
                # 注入的 client 亦不得跟隨轉址：轉址目標不會再過白名單，
                # 會成為繞過 allowlist 的側門。呼叫端注入時必須自行帶 follow_redirects=False；
                # 此處明確再指定一次，httpx 允許逐次覆寫。
                resp = await self._client.head(url, follow_redirects=False)
            else:
                async with httpx.AsyncClient(timeout=3.0, follow_redirects=False) as client:
                    resp = await client.head(url)
            t_end_perf = self._perf()
            t_end_wall = self._wall()

            if resp.status_code >= 400:
                raise ClockSyncError(f"HTTP HEAD returned status {resp.status_code}")

            date_header = resp.headers.get("Date")
            if not date_header:
                raise ClockSyncError("HTTP response missing Date header")

            server_date = parsedate_to_datetime(date_header)
            if server_date.tzinfo is None:
                server_date = server_date.replace(tzinfo=timezone.utc)
            else:
                server_date = server_date.astimezone(timezone.utc)

            # RTT 一律取自 perf_counter（單調鐘），
            # 嚴禁以牆鐘相減；半個 RTT 補償後與 perf_end 對應的牆鐘比較，
            # 符合 §A 不變式 offset_ms = true_utc_ms - local_utc_ms。
            rtt_sec = t_end_perf - t_start_perf
            rtt_ms = rtt_sec * 1000.0
            server_estimated_utc = server_date + timedelta(seconds=rtt_sec / 2)
            offset_ms = (server_estimated_utc - t_end_wall).total_seconds() * 1000.0

            return ClockSample(
                source=ClockSource.SERVER_HEADER,
                offset_ms=offset_ms,
                rtt_ms=rtt_ms,
                stratum=None,
                polled_at=t_end_wall,
            )
        except Exception as exc:
            if type(exc).__name__ == "NetworkAccessError":
                raise
            if isinstance(exc, ClockSyncError):
                raise
            raise ClockSyncError(f"Server header sample failed for {url}: {exc}") from exc


DEFAULT_NTP_HOST = "pool.ntp.org"


class ClockSynchronizer:
    """雙軌並行時鐘同步聚合器。

    預設 ntp_host = 'pool.ntp.org'，可傳入 server_url 於 T-5m / T-1m 同時收集
    NTP 與 Server HTTP Date 樣本。
    """

    def __init__(
        self,
        *,
        ntp_sync: NtpClockSync | None = None,
        server_sync: ServerHeaderClockSync | None = None,
        ntp_host: str | None = DEFAULT_NTP_HOST,
        server_url: str | None = None,
        allowed_hosts: tuple[str, ...] = DEFAULT_KKTIX_ALLOWED_HOSTS,
        perf_counter: Callable[[], float] = time.perf_counter,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._ntp_sync = ntp_sync or NtpClockSync()
        # allowed_hosts 透明轉傳；若呼叫端已注入 server_sync，
        # 則以該實例自身的白名單為準（不覆寫、不放寬）。
        self._server_sync = server_sync or ServerHeaderClockSync(allowed_hosts=allowed_hosts)
        self._ntp_host = ntp_host
        self._server_url = server_url
        self._perf = perf_counter
        self._wall = wall_clock

    @property
    def server_url(self) -> str | None:
        """本同步器綁定的 Server Date 探針 URL（唯讀）。"""
        return self._server_url

    async def refresh(self) -> TimeReference:
        samples: list[ClockSample] = []
        tasks: list[asyncio.Task[ClockSample]] = []

        if self._ntp_host:
            tasks.append(asyncio.create_task(self._ntp_sync.sample(self._ntp_host)))
        if self._server_url:
            tasks.append(asyncio.create_task(self._server_sync.sample(self._server_url)))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, ClockSample):
                    samples.append(res)

        primary_source: ClockSource | None = None
        selected_offset = 0.0

        ntp_sample = next((s for s in samples if s.source == ClockSource.NTP), None)
        server_sample = next((s for s in samples if s.source == ClockSource.SERVER_HEADER), None)

        if ntp_sample is not None:
            primary_source = ClockSource.NTP
            selected_offset = ntp_sample.offset_ms
        elif server_sample is not None:
            primary_source = ClockSource.SERVER_HEADER
            selected_offset = server_sample.offset_ms

        return TimeReference(
            offset_ms=selected_offset,
            samples=tuple(samples),
            primary_source=primary_source,
            monotonic_anchor_perf=self._perf(),
            monotonic_anchor_wall=self._wall(),
        )
