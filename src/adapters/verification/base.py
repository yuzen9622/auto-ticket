"""驗證（防機器人問答／圖形驗證碼）Port。

本專案是研究系統：驗證只做「偵測 -> 交由 provider 作答 -> 回填」，
**不實作任何繞過機制**。Cloudflare 之類的挑戰一律 fail-closed 中止。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class ChallengeKind(str, Enum):
    TEXT_QUIZ = "TEXT_QUIZ"
    IMAGE_CAPTCHA = "IMAGE_CAPTCHA"


@dataclass(frozen=True, slots=True)
class VerificationChallenge:
    kind: ChallengeKind
    question: str
    image_bytes: bytes | None = None


@dataclass(frozen=True, slots=True)
class VerificationResult:
    solved: bool
    answer: str | None
    provider: str
    detail: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class VerificationProvider(Protocol):
    name: str

    async def solve(
        self, challenge: VerificationChallenge
    ) -> VerificationResult: ...
