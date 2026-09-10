"""測試用零網路存取保證（context manager ＋ autouse fixture）。

攔截 TCP（`connect` / `connect_ex` / `create_connection`）、UDP（`sendto`）與
DNS（`getaddrinfo` / `gethostbyname` / `gethostbyname_ex`）。針對 AF_INET / AF_INET6
拋出 NetworkAccessError；放行 AF_UNIX 等本機通信與 loopback 名稱解析。

**適用範圍（不得誇大宣稱）**：掛載方式是每個測試檔自行 import `netguard_autouse`
fixture（由 G8 機械化驗證涵蓋完整）。`tests/conftest.py` 屬凍結檔案、不得為了全域註冊
而修改，因此本模組**不宣稱**涵蓋未掛載的既有測試（那些測試本來就不連外）。
"""
from __future__ import annotations

import contextlib
import socket
from collections.abc import Iterator
from typing import Any

import pytest


class NetworkAccessError(RuntimeError):
    pass


IP_FAMILIES = (socket.AF_INET, socket.AF_INET6)
# DNS 放行清單：loopback 名稱解析不會產生對外封包，且部分 asyncio 內部路徑會用到。
LOOPBACK_HOSTS = frozenset({"", "localhost", "localhost.", "127.0.0.1", "::1", "ip6-localhost"})

_patch_depth: int = 0
_orig_connect: Any = None
_orig_connect_ex: Any = None
_orig_sendto: Any = None
_orig_create: Any = None
_orig_getaddrinfo: Any = None
_orig_gethostbyname: Any = None
_orig_gethostbyname_ex: Any = None


def _blocked_connect(self: socket.socket, address: tuple[str, int] | str) -> None:
    if self.family in IP_FAMILIES:
        raise NetworkAccessError(f"Real network connect attempt blocked: {address}")
    return _orig_connect(self, address)


def _blocked_connect_ex(self: socket.socket, address: tuple[str, int] | str) -> int:
    if self.family in IP_FAMILIES:
        raise NetworkAccessError(f"Real network connect_ex attempt blocked: {address}")
    return _orig_connect_ex(self, address)


def _blocked_sendto(self: socket.socket, data: bytes, *args: Any) -> int:
    if self.family in IP_FAMILIES:
        dest = args[0] if args else "unknown"
        raise NetworkAccessError(f"Real UDP network access blocked: sendto to {dest}")
    return _orig_sendto(self, data, *args)


def _blocked_create_connection(address: tuple[str, int], *args: Any, **kwargs: Any) -> socket.socket:
    raise NetworkAccessError(f"Real network create_connection blocked: {address}")


def _is_loopback(host: Any) -> bool:
    return host is None or str(host).lower() in LOOPBACK_HOSTS


def _blocked_getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
    # DNS 查詢本身就是對外封包（且是 NTP/HTTP 連線的第一步），必須一併攔截，
    # 否則「零網路」只擋得住 connect，卻仍會對真實 resolver 送出 UDP 53。
    if _is_loopback(host):
        return _orig_getaddrinfo(host, port, *args, **kwargs)
    raise NetworkAccessError(f"Real DNS lookup blocked: getaddrinfo({host!r}, {port!r})")


def _blocked_gethostbyname(host: str) -> str:
    if _is_loopback(host):
        return _orig_gethostbyname(host)
    raise NetworkAccessError(f"Real DNS lookup blocked: gethostbyname({host!r})")


def _blocked_gethostbyname_ex(host: str) -> Any:
    if _is_loopback(host):
        return _orig_gethostbyname_ex(host)
    raise NetworkAccessError(f"Real DNS lookup blocked: gethostbyname_ex({host!r})")


@contextlib.contextmanager
def no_network() -> Iterator[None]:
    global _patch_depth
    global _orig_connect, _orig_connect_ex, _orig_sendto, _orig_create
    global _orig_getaddrinfo, _orig_gethostbyname, _orig_gethostbyname_ex
    if _patch_depth == 0:
        _orig_connect = socket.socket.connect
        _orig_connect_ex = socket.socket.connect_ex
        _orig_sendto = socket.socket.sendto
        _orig_create = socket.create_connection
        _orig_getaddrinfo = socket.getaddrinfo
        _orig_gethostbyname = socket.gethostbyname
        _orig_gethostbyname_ex = socket.gethostbyname_ex

        socket.socket.connect = _blocked_connect        # type: ignore[assignment]
        socket.socket.connect_ex = _blocked_connect_ex  # type: ignore[assignment]
        socket.socket.sendto = _blocked_sendto          # type: ignore[assignment]
        socket.create_connection = _blocked_create_connection  # type: ignore[assignment]
        socket.getaddrinfo = _blocked_getaddrinfo              # type: ignore[assignment]
        socket.gethostbyname = _blocked_gethostbyname          # type: ignore[assignment]
        socket.gethostbyname_ex = _blocked_gethostbyname_ex    # type: ignore[assignment]
    _patch_depth += 1
    try:
        yield
    finally:
        _patch_depth -= 1
        if _patch_depth == 0:
            # [NETGUARD-RESTORE-BY-ASSIGN] 一律以保存的原始方法引用重新賦值還原。
            # 嚴禁改用「刪除屬性」的還原方式（見 G10）：那依賴「patch 前該屬性
            # 不在 socket.socket.__dict__、而是繼承自 _socket.socket」這個 CPython
            # 實作細節；一旦 socket.py 改為自行定義該方法，屬性刪除會把真正的
            # 實作永久移除，整個測試行程的 socket 都壞掉，且錯誤現場離事發點
            # 極遠、幾乎無法追查。本檔受 G10 靜態掃描，註解亦不得出現該字面值。
            socket.socket.connect = _orig_connect        # type: ignore[method-assign]
            socket.socket.connect_ex = _orig_connect_ex  # type: ignore[method-assign]
            socket.socket.sendto = _orig_sendto          # type: ignore[method-assign]
            socket.create_connection = _orig_create
            socket.getaddrinfo = _orig_getaddrinfo
            socket.gethostbyname = _orig_gethostbyname
            socket.gethostbyname_ex = _orig_gethostbyname_ex
            # [NETGUARD-RESTORE-BY-ASSIGN] **保留**原始引用，嚴禁在此設回 None：
            # 8.0 的 identity 斷言（`socket.socket.connect is netguard._orig_connect`）
            # 需要它，且下次進入時會以同一份原始實作重新保存（冪等）。G10 靜態鎖定。


@pytest.fixture(autouse=True)
def netguard_autouse() -> Iterator[None]:
    """每個測試檔以 `from tests.netguard import netguard_autouse  # noqa: F401` 掛載。

    `tests/` 有 `__init__.py`，故 `tests.netguard` 為合法可 import 的套件路徑，
    pytest 與 `python -c` 兩種入口皆可解析。
    """
    with no_network():
        yield
