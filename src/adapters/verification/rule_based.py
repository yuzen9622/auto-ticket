"""規則式文字問答作答器。

只處理主辦自訂的**文字**問答題（`ChallengeKind.TEXT_QUIZ`）：依任務檔預先寫好的
規則比對題幹並回填答案。圖形驗證碼一律不受理——本專案不實作任何圖形破密。

規則全數落空時以 `BLIND_GUESS_ANSWER` 盲猜送出：KKTIX 的問答題答錯只會退回同一張
表單重填，而卡在這裡不作答則等於整場放棄。盲猜與命中在 `detail` 與 timeline 上
嚴格區分，事後看得出這一單的答案是規則給的還是猜的。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from adapters.verification.base import (
    ChallengeKind,
    VerificationChallenge,
    VerificationResult,
)
from domain.task import VerificationRule
from telemetry.timeline import TimelineEventType, TimelineRecorder

PROVIDER_NAME = "rule_based"
BLIND_GUESS_ANSWER = "A"

REASON_RULE_MATCHED = "rule_matched"
REASON_BLIND_GUESS = "blind_guess"
REASON_UNSUPPORTED_KIND = "unsupported_kind"

ANSWER_MARK = "verification_rule_answer"

NO_MATCH = "NO_MATCH"
MATCHED = "MATCHED"
INVALID_PATTERN = "INVALID_PATTERN"


class RuleBasedVerificationProvider:
    """依序比對規則；`is_regex=False` 走不分大小寫子字串，True 走正則。

    正則刻意**不**強加 `IGNORECASE`：大小寫敏感與否由規則作者以 `(?i)` 自行宣告，
    這裡替他決定會讓寫得精準的 pattern 失效。壞掉的 pattern 只跳過該條並留下
    軌跡，不讓一條打錯字的規則炸掉整場搶票。
    """

    name = PROVIDER_NAME

    def __init__(
        self,
        rules: Sequence[VerificationRule] = (),
        *,
        blind_guess_answer: str = BLIND_GUESS_ANSWER,
        telemetry: TimelineRecorder | None = None,
    ) -> None:
        self.rules = tuple(rules)
        self.blind_guess_answer = blind_guess_answer
        self.telemetry = telemetry

    async def solve(self, challenge: VerificationChallenge) -> VerificationResult:
        if challenge.kind is not ChallengeKind.TEXT_QUIZ:
            return VerificationResult(
                solved=False,
                answer=None,
                provider=PROVIDER_NAME,
                detail={
                    "reason": REASON_UNSUPPORTED_KIND,
                    "kind": challenge.kind.value,
                },
            )

        question = " ".join((challenge.question or "").split())
        matched, trace = self._match(question)
        if matched is not None:
            answer, reason = matched.answer, REASON_RULE_MATCHED
        else:
            answer, reason = self.blind_guess_answer, REASON_BLIND_GUESS

        detail: dict[str, object] = {
            "reason": reason,
            "rules": len(self.rules),
            "trace": tuple(trace),
        }
        if matched is not None:
            detail["pattern"] = matched.pattern
            detail["is_regex"] = matched.is_regex

        if self.telemetry is not None:
            self.telemetry.record(
                TimelineEventType.MARK,
                ANSWER_MARK,
                provider=PROVIDER_NAME,
                reason=reason,
                rules=len(self.rules),
                question=question,
                answer=answer,
            )
        return VerificationResult(
            solved=True, answer=answer, provider=PROVIDER_NAME, detail=detail
        )

    def _match(self, question: str) -> tuple[VerificationRule | None, list[str]]:
        trace: list[str] = []
        lowered = question.lower()
        for index, rule in enumerate(self.rules):
            label = f"rule[{index}] pattern={rule.pattern} regex={rule.is_regex}"
            if rule.is_regex:
                try:
                    hit = re.search(rule.pattern, question) is not None
                except re.error as exc:
                    trace.append(f"{label} -> {INVALID_PATTERN} ({exc})")
                    continue
            else:
                hit = rule.pattern.lower() in lowered
            if hit:
                trace.append(f"{label} -> {MATCHED}")
                return rule, trace
            trace.append(f"{label} -> {NO_MATCH}")
        return None, trace
