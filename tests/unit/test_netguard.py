"""零網路守門的封鎖範圍與還原正確性。"""

from __future__ import annotations

import socket

import pytest

import tests.netguard as ng
from tests.netguard import NetworkAccessError, netguard_autouse, no_network  # noqa: F401

BLOCKED_V4 = ("192.0.2.1", 80)
BLOCKED_V6 = ("2001:db8::1", 80)


def test_tcp_connect_blocked_for_ipv4() -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(NetworkAccessError):
            s.connect(BLOCKED_V4)
    finally:
        s.close()


def test_tcp_connect_blocked_for_ipv6() -> None:
    s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    try:
        with pytest.raises(NetworkAccessError):
            s.connect(BLOCKED_V6)
    finally:
        s.close()


def test_connect_ex_blocked() -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(NetworkAccessError):
            s.connect_ex(BLOCKED_V4)
    finally:
        s.close()


def test_udp_sendto_blocked_for_both_families() -> None:
    for family, addr in ((socket.AF_INET, BLOCKED_V4), (socket.AF_INET6, BLOCKED_V6)):
        s = socket.socket(family, socket.SOCK_DGRAM)
        try:
            with pytest.raises(NetworkAccessError):
                s.sendto(b"x", addr)
        finally:
            s.close()


def test_create_connection_blocked() -> None:
    with pytest.raises(NetworkAccessError):
        socket.create_connection(BLOCKED_V4)


def test_af_unix_socketpair_allowed() -> None:
    a, b = socket.socketpair()
    try:
        a.sendall(b"ping")
        assert b.recv(4) == b"ping"
    finally:
        a.close()
        b.close()


def test_dns_lookups_blocked() -> None:
    with pytest.raises(NetworkAccessError):
        socket.getaddrinfo("db.invalid", 80)
    with pytest.raises(NetworkAccessError):
        socket.gethostbyname("db.invalid")
    with pytest.raises(NetworkAccessError):
        socket.gethostbyname_ex("db.invalid")


def test_loopback_dns_allowed() -> None:
    assert socket.getaddrinfo("localhost", 80)
    assert socket.getaddrinfo(None, 0)


def test_autouse_fixture_blocks_without_explicit_context() -> None:
    """本檔未顯式進入 no_network()，仍應被 netguard_autouse fixture 攔截。"""
    assert socket.socket.connect is not ng._orig_connect
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(NetworkAccessError):
            s.connect(BLOCKED_V4)
    finally:
        s.close()


def test_restore_is_by_assignment_and_keeps_original_reference() -> None:
    """退出後必須是「同一個原始物件」且屬性仍在。"""
    outer_depth = ng._patch_depth
    with no_network():
        assert ng._patch_depth == outer_depth + 1
    assert ng._orig_connect is not None
    assert ng._orig_getaddrinfo is not None
    assert "connect" in socket.socket.__dict__
    # 目前仍在 autouse fixture 的 no_network() 內，故 connect 應仍是被 patch 的版本
    assert ng._patch_depth == outer_depth


def test_nested_reentry_restores_correct_reference() -> None:
    saved_connect = ng._orig_connect
    saved_getaddrinfo = ng._orig_getaddrinfo
    for _ in range(3):
        with no_network():
            with no_network():
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    with pytest.raises(NetworkAccessError):
                        s.connect(BLOCKED_V4)
                finally:
                    s.close()
        assert ng._orig_connect is saved_connect
        assert ng._orig_getaddrinfo is saved_getaddrinfo


def test_full_exit_restores_real_socket_api() -> None:
    """暫時退出最外層 guard，確認還原成真實實作（identity 相同）後再重新進入。"""
    saved_connect = ng._orig_connect
    saved_create = ng._orig_create
    with _temporarily_unpatched():
        assert socket.socket.connect is saved_connect
        assert socket.create_connection is saved_create
        assert socket.getaddrinfo is ng._orig_getaddrinfo


class _temporarily_unpatched:
    """把 autouse fixture 建立的最外層 patch 暫時退出，離開時原樣復原。"""

    def __enter__(self) -> None:
        self._cm = None
        assert ng._patch_depth == 1, ng._patch_depth
        ng._patch_depth = 0
        socket.socket.connect = ng._orig_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = ng._orig_connect_ex  # type: ignore[method-assign]
        socket.socket.sendto = ng._orig_sendto  # type: ignore[method-assign]
        socket.create_connection = ng._orig_create
        socket.getaddrinfo = ng._orig_getaddrinfo
        socket.gethostbyname = ng._orig_gethostbyname
        socket.gethostbyname_ex = ng._orig_gethostbyname_ex

    def __exit__(self, *exc: object) -> None:
        socket.socket.connect = ng._blocked_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = ng._blocked_connect_ex  # type: ignore[method-assign]
        socket.socket.sendto = ng._blocked_sendto  # type: ignore[method-assign]
        socket.create_connection = ng._blocked_create_connection
        socket.getaddrinfo = ng._blocked_getaddrinfo
        socket.gethostbyname = ng._blocked_gethostbyname
        socket.gethostbyname_ex = ng._blocked_gethostbyname_ex
        ng._patch_depth = 1
