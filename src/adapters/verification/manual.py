"""人工作答 provider：把題目交給人，等一個有時限的答案。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from adapters.verification.base import (
    VerificationChallenge,
    VerificationResult,
)
from telemetry.timeline import TimelineEventType, TimelineRecorder

PROVIDER_NAME = "manual"
DEFAULT_TIMEOUT_S = 60.0
ASK_MARK = "verification_prompted"

AnswerSource = Callable[[VerificationChallenge], Awaitable[str]]


class ManualVerificationProvider:
    """等待外部（人）提供答案；逾時即誠實回報未解出，不臆造答案。"""

    name = PROVIDER_NAME

    def __init__(
        self,
        answer_source: AnswerSource,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        telemetry: TimelineRecorder | None = None,
    ) -> None:
        self.answer_source = answer_source
        self.timeout_s = timeout_s
        self.telemetry = telemetry

    async def solve(self, challenge: VerificationChallenge) -> VerificationResult:
        if self.telemetry is not None:
            self.telemetry.record(
                TimelineEventType.MARK,
                ASK_MARK,
                provider=PROVIDER_NAME,
                kind=challenge.kind.value,
                question=challenge.question,
            )
        try:
            answer = await asyncio.wait_for(
                self.answer_source(challenge), timeout=self.timeout_s
            )
        except asyncio.TimeoutError:
            return VerificationResult(
                solved=False,
                answer=None,
                provider=PROVIDER_NAME,
                detail={"reason": "timeout", "timeout_s": self.timeout_s},
            )
        except Exception as exc:
            return VerificationResult(
                solved=False,
                answer=None,
                provider=PROVIDER_NAME,
                detail={"reason": "answer_source_failed", "error_type": type(exc).__name__},
            )

        text = (answer or "").strip()
        if not text:
            return VerificationResult(
                solved=False,
                answer=None,
                provider=PROVIDER_NAME,
                detail={"reason": "empty_answer"},
            )
        return VerificationResult(
            solved=True, answer=text, provider=PROVIDER_NAME, detail={"reason": "answered"}
        )
