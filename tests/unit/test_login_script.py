from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.netguard import netguard_autouse  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import login


def test_default_profile_is_live() -> None:
    args = login.build_parser().parse_args([])
    assert args.profile == "live"
    assert args.url.endswith("/users/sign_in")


def test_session_cookie_is_detected() -> None:
    count, has_session = login.summarize_cookies(
        [{"domain": ".kktix.com", "name": "_kktix_session"}, {"domain": ".other.test", "name": "x"}]
    )
    assert (count, has_session) == (1, True)


def test_token_cookie_also_counts_as_session() -> None:
    assert login.summarize_cookies([{"domain": "kktix.cc", "name": "auth_token"}]) == (1, True)


def test_cookies_without_session_are_reported_honestly() -> None:
    """只有偏好設定類 cookie 時不得回報成已登入。"""
    assert login.summarize_cookies([{"domain": ".kktix.com", "name": "locale"}]) == (1, False)


def test_no_cookies_at_all() -> None:
    assert login.summarize_cookies([]) == (0, False)


@pytest.mark.parametrize("field", ["value", "expires", "httpOnly"])
def test_summary_never_returns_cookie_contents(field: str) -> None:
    """回傳值只有數量與布林——cookie 內容不得外流到任何輸出。"""
    result = login.summarize_cookies(
        [{"domain": ".kktix.com", "name": "_kktix_session", field: "SECRET"}]
    )
    assert result == (1, True)
    assert "SECRET" not in repr(result)
