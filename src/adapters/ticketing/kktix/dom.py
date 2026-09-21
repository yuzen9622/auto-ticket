"""KKTIX 頁面的 AngularJS 相容 DOM 操作（向後相容 re-export shim）。

實際實作已上移至 `adapters.ticketing.dom`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from adapters.ticketing.dom import (
    DEFAULT_DISPATCH_TIMEOUT_MS,
    DEFAULT_OPTIONAL_PROBE_MS,
    DEFAULT_PROBE_TIMEOUT_MS,
    DISPATCH_SKIPPED_MARK,
    SELECTOR_FALLBACK_MARK,
    candidate_selectors,
    first_visible,
    ng_click,
    ng_dispatch,
    ng_fill,
    page_text,
    read_input_value,
)
from adapters.ticketing.dom import (
    contains_cloudflare_challenge as _contains_cloudflare_challenge,
)
from adapters.ticketing.kktix.selectors import KKTIXSelectors

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page
else:
    Locator = Any
    Page = Any

__all__ = [
    "DEFAULT_DISPATCH_TIMEOUT_MS",
    "DEFAULT_OPTIONAL_PROBE_MS",
    "DEFAULT_PROBE_TIMEOUT_MS",
    "DISPATCH_SKIPPED_MARK",
    "SELECTOR_FALLBACK_MARK",
    "candidate_selectors",
    "contains_cloudflare_challenge",
    "first_visible",
    "ng_click",
    "ng_dispatch",
    "ng_fill",
    "page_text",
    "read_input_value",
]


def contains_cloudflare_challenge(text: str) -> str | None:
    """命中回傳該挑戰字串，否則 None。"""
    return _contains_cloudflare_challenge(text, KKTIXSelectors.CLOUDFLARE_CHALLENGE_TEXTS)
