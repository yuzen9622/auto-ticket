"""依題型分派至對應 VerificationProvider 的路由層。"""

from __future__ import annotations

from adapters.verification.base import (
    ChallengeKind,
    VerificationChallenge,
    VerificationProvider,
    VerificationResult,
)

PROVIDER_NAME = "routing"


class RoutingVerificationProvider:
    """依 challenge.kind 路由至文字問答題或圖片驗證碼 provider。"""

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        text: VerificationProvider | None,
        image: VerificationProvider | None,
    ) -> None:
        self.text = text
        self.image = image

    async def solve(self, challenge: VerificationChallenge) -> VerificationResult:
        target = self.image if challenge.kind is ChallengeKind.IMAGE_CAPTCHA else self.text
        if target is None:
            return VerificationResult(
                solved=False,
                answer=None,
                provider=PROVIDER_NAME,
                detail={
                    "reason": "no_provider_for_kind",
                    "kind": challenge.kind.value,
                },
            )
        return await target.solve(challenge)
