"""票種決策（PriorityFirst）。

**純函式層**：本模組只吃頁面快照（`TicketOption`），不得接觸 `Page`、不得做任何 IO。
DOM 讀取與決策分離是刻意的——決策是唯一能被大量、快速、決定性測試的部分。
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Literal

from domain.preference import TicketPreference, TicketPriority, TicketRule


@dataclass(frozen=True, slots=True)
class TicketOption:
    """頁面上單一票種的不可變快照。"""

    index: int
    """頁面上的第幾個票種單元（0 起算）；點擊加減號時要用。"""
    name: str
    price: int
    available: bool
    remaining: int | None
    """剩餘張數；頁面讀不到就是 None，**不得臆造**。"""
    status_text: str = ""
    """原始狀態字串，供事後追查。"""


@dataclass(frozen=True, slots=True)
class TicketDecision:
    status: Literal["SELECTED", "SOLD_OUT"]
    option: TicketOption | None
    quantity: int
    matched_priority: TicketPriority | None
    fallback_used: bool
    trace: tuple[str, ...]
    """每個 priority 的比對結果；研究資料，不得省略。"""


NO_PRICE_MATCH = "NO_PRICE_MATCH"
NO_NAME_MATCH = "NO_NAME_MATCH"
INVALID_PATTERN = "INVALID_PATTERN"
"""保留給既有紀錄判讀；名稱改為子字串比對後不再產生這個原因。"""
UNAVAILABLE = "UNAVAILABLE"
INSUFFICIENT_REMAINING = "INSUFFICIENT_REMAINING"
SELECTED = "SELECTED"
EXCLUDED = "EXCLUDED"
EXCLUDED_BY_NAME = "EXCLUDED_BY_NAME"
OUT_OF_PRICE_RANGE = "OUT_OF_PRICE_RANGE"
UNKNOWN_PRICE = "UNKNOWN_PRICE"


def is_selectable(option: TicketOption, quantity: int) -> bool:
    if not option.available:
        return False
    return option.remaining is None or option.remaining >= quantity


def _matches_any(name: str, patterns: Sequence[str]) -> bool:
    folded = name.casefold()
    return any(pattern.casefold() in folded for pattern in patterns if pattern)


def _name_rank(name: str, patterns: Sequence[str]) -> int:
    """命中第幾個偏好關鍵字；都沒命中排在所有命中的後面。"""
    folded = name.casefold()
    for idx, pattern in enumerate(patterns):
        if pattern and pattern.casefold() in folded:
            return idx
    return len(patterns)


def _price_key(price: int, order: str) -> tuple[int, int]:
    """價格取向的排序鍵。

    `price == 0` 是「這一列沒印價格」而不是免費。照字面排的話「便宜優先」會直接
    挑中一張根本不知道多少錢的票，所以價格未知的一律墊底。
    """
    if order == "page":
        return (0, 0)
    if price <= 0:
        return (1, 0)
    return (0, -price if order == "highest" else price)


def _apply_rule(
    options: Sequence[TicketOption],
    rule: TicketRule,
    quantity: int,
    trace: list[str],
) -> TicketOption | None:
    label = (
        f"rule price=[{rule.min_price or ''}..{rule.max_price or ''}] "
        f"order={rule.price_order} prefer={rule.prefer_name_patterns or '*'}"
    )
    candidates = [o for o in options if is_selectable(o, quantity)]
    if not candidates:
        trace.append(f"{label} -> {UNAVAILABLE}")
        return None

    if rule.has_price_window:
        priced = [o for o in candidates if o.price > 0]
        if len(priced) < len(candidates):
            # 設了預算卻拿未知價格的票來湊，等於自己把上限作廢。
            trace.append(
                f"{label} -> {UNKNOWN_PRICE} skipped={len(candidates) - len(priced)}"
            )
        candidates = [o for o in priced if rule.accepts_price(o.price)]
        if not candidates:
            trace.append(f"{label} -> {OUT_OF_PRICE_RANGE}")
            return None

    # sorted 是穩定排序：名次與價格都打平時保留頁面原本的順序。
    ranked = sorted(
        candidates,
        key=lambda o: (
            _name_rank(o.name, rule.prefer_name_patterns),
            _price_key(o.price, rule.price_order),
        ),
    )
    chosen = ranked[0]
    trace.append(f"{label} -> {SELECTED} index={chosen.index} name={chosen.name}")
    return chosen


def decide_ticket(
    options: Sequence[TicketOption],
    preference: TicketPreference,
    excluded_names: Collection[str] = (),
) -> TicketDecision:
    """依 `sorted_priorities` 逐一比對，回傳決策與完整比對軌跡。

    比對規則：價格相等；`ticket_name_pattern` 非 None 時同時要求名稱正則命中。
    命中且可選（`available` 且 `remaining` 足夠）即選取；全數落空時，
    `fallback_to_any=True` 才挑頁面順序上第一個可選票種；再落空回 `SOLD_OUT`。

    `preference.rule` 是開賣前唯一寫得出來的那種設定（預算上限、票種關鍵字、
    貴或便宜優先）。它排在精確價格之後、`fallback_to_any` 之前：精確指定最強，
    規則次之，「隨便挑一個」永遠是最後手段。規則裡的排除關鍵字則是例外，與
    `excluded_names` 同一關就先砍掉，連 `fallback_to_any` 也繞不過去。

    只想用規則的人仍然得填至少一筆 `priorities`（領域模型的既有限制）。要注意
    `price=0` 的那一筆不是「不指定」，而是「我要價格 0 的票」，它會比對上頁面
    沒印價格的票種並搶在規則前面成立——當成佔位用會讓規則整個失效。

    `excluded_names` 是「這一場已經搶輸過的票種」：被別人搶先一步的票種在頁面上
    仍然看起來可選，不排除就會無限重試同一張票。排除是完全性的，
    連 `fallback_to_any` 也不得繞過。
    """
    quantity = preference.quantity
    trace: list[str] = []

    excluded = frozenset(excluded_names)
    if excluded:
        candidates_before = len(options)
        options = [o for o in options if o.name not in excluded]
        trace.append(
            f"{EXCLUDED} names={sorted(excluded)} "
            f"remaining_options={len(options)}/{candidates_before}"
        )

    rule = preference.rule
    if rule is not None and rule.exclude_name_patterns:
        candidates_before = len(options)
        options = [
            o for o in options if not _matches_any(o.name, rule.exclude_name_patterns)
        ]
        trace.append(
            f"{EXCLUDED_BY_NAME} patterns={list(rule.exclude_name_patterns)} "
            f"remaining_options={len(options)}/{candidates_before}"
        )

    for idx, priority in enumerate(preference.sorted_priorities):
        label = f"priority[{idx}] price={priority.price} pattern={priority.ticket_name_pattern or '*'}"
        candidates = [o for o in options if o.price == priority.price]
        if not candidates:
            trace.append(f"{label} -> {NO_PRICE_MATCH}")
            continue

        if priority.ticket_name_pattern is not None:
            # 子字串比對，不是正規表示式。票種名稱本來就常含 $ + ( ) 這類字元
            # （例如「A＋$32 手續費」），當成 regex 編譯的話 `$` 會變成行尾錨點，
            # 名稱永遠配不到自己，最後被誤判成整場售罄。UI 也從未宣稱支援樣式。
            wanted = priority.ticket_name_pattern.casefold()
            named = [o for o in candidates if wanted in o.name.casefold()]
            if not named:
                trace.append(f"{label} -> {NO_NAME_MATCH}")
                continue
            candidates = named

        selectable = [o for o in candidates if is_selectable(o, quantity)]
        if not selectable:
            reason = (
                INSUFFICIENT_REMAINING
                if any(o.available for o in candidates)
                else UNAVAILABLE
            )
            trace.append(f"{label} -> {reason}")
            continue

        chosen = selectable[0]
        trace.append(f"{label} -> {SELECTED} index={chosen.index} name={chosen.name}")
        return TicketDecision(
            status="SELECTED",
            option=chosen,
            quantity=quantity,
            matched_priority=priority,
            fallback_used=False,
            trace=tuple(trace),
        )

    if rule is not None:
        chosen = _apply_rule(options, rule, quantity, trace)
        if chosen is not None:
            return TicketDecision(
                status="SELECTED",
                option=chosen,
                quantity=quantity,
                matched_priority=None,
                fallback_used=False,
                trace=tuple(trace),
            )

    if preference.fallback_to_any:
        selectable = [o for o in options if is_selectable(o, quantity)]
        if selectable:
            chosen = selectable[0]
            trace.append(f"fallback -> {SELECTED} index={chosen.index} name={chosen.name}")
            return TicketDecision(
                status="SELECTED",
                option=chosen,
                quantity=quantity,
                matched_priority=None,
                fallback_used=True,
                trace=tuple(trace),
            )
        trace.append(f"fallback -> {UNAVAILABLE}")
    else:
        trace.append("fallback -> DISABLED")

    return TicketDecision(
        status="SOLD_OUT",
        option=None,
        quantity=quantity,
        matched_priority=None,
        fallback_used=False,
        trace=tuple(trace),
    )
