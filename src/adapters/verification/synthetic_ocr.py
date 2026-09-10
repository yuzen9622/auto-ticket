"""合成資料 OCR provider——**只有介面，沒有實作**。

自動解題被 PRD 列為 P1，且需要先由實站 dry-run 收集到真實題型樣本才談得上訓練
或評測。此處只保留 Port 的形狀，呼叫即拋 `NotImplementedError`，
不留一個看似可用其實會靜默失敗的假實作。
"""

from __future__ import annotations

from adapters.verification.base import VerificationChallenge, VerificationResult

PROVIDER_NAME = "synthetic_ocr"


class SyntheticOcrProvider:
    name = PROVIDER_NAME

    async def solve(self, challenge: VerificationChallenge) -> VerificationResult:
        raise NotImplementedError(
            "合成 OCR 解題尚未實作：需先由實站 dry-run 收集真實題型樣本後再評估"
        )
