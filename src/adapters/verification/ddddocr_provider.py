# ruff: noqa: BLE001
"""本機 ddddocr 圖片驗證碼辨識器。

採用 Lazy Singleton 延遲載入模型與 onnxruntime，避免在 API 行程或不使用 OCR
的任務中產生昂貴的載入開銷。推論呼叫由 asyncio.to_thread 移至工作執行緒，
避免阻塞事件迴圈。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from adapters.verification.base import (
    ChallengeKind,
    VerificationChallenge,
    VerificationResult,
)
from telemetry.timeline import TimelineEventType, TimelineRecorder

PROVIDER_NAME = "ddddocr"

_ENGINES: dict[tuple[str | None, bool], Any] = {}
_ENGINE_LOCK = asyncio.Lock()


def _build_ocr(model_path: str | None, *, beta: bool = False) -> Any:
    """載入並初始化 ddddocr 引擎（在 worker 執行緒中執行）。"""
    import ddddocr

    if model_path is None:
        return ddddocr.DdddOcr(show_ad=False, beta=beta)

    onnx_file = Path(model_path)
    if not onnx_file.is_file():
        raise FileNotFoundError(f"Custom OCR model not found: {model_path}")
    json_file = onnx_file.with_suffix(".json")
    charsets = str(json_file) if json_file.is_file() else ""
    return ddddocr.DdddOcr(
        show_ad=False,
        beta=beta,
        import_onnx_path=str(onnx_file),
        charsets_path=charsets,
    )


async def _get_ocr(model_path: str | None, *, beta: bool = False) -> Any:
    """依模型路徑取得快取的 OCR 引擎單例。"""
    cache_key = (model_path, beta)
    engine = _ENGINES.get(cache_key)
    if engine is not None:
        return engine
    async with _ENGINE_LOCK:
        if cache_key not in _ENGINES:
            if beta:
                engine = await asyncio.to_thread(_build_ocr, model_path, beta=True)
            else:
                engine = await asyncio.to_thread(_build_ocr, model_path)
            _ENGINES[cache_key] = engine
    return _ENGINES[cache_key]


class DdddOcrProvider:
    """基於 ddddocr 的圖片驗證碼辨識器。"""

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        telemetry: TimelineRecorder | None = None,
        model_path: str | None = None,
        min_length: int = 1,
        beta: bool = False,
        color_filter_colors: Sequence[str] | None = None,
    ) -> None:
        self.telemetry = telemetry
        self.model_path = model_path
        self.min_length = min_length
        self.beta = beta
        self.color_filter_colors = tuple(color_filter_colors or ())

    async def _load_engine(self, model_path: str | None) -> Any:
        if self.beta:
            return await _get_ocr(model_path, beta=True)
        return await _get_ocr(model_path)

    async def solve(self, challenge: VerificationChallenge) -> VerificationResult:
        if challenge.kind is not ChallengeKind.IMAGE_CAPTCHA:
            return VerificationResult(
                solved=False,
                answer=None,
                provider=PROVIDER_NAME,
                detail={"reason": "kind_not_supported"},
            )

        if not challenge.image_bytes:
            return VerificationResult(
                solved=False,
                answer=None,
                provider=PROVIDER_NAME,
                detail={"reason": "no_image_bytes"},
            )

        ocr = None
        if self.model_path is not None:
            try:
                ocr = await self._load_engine(self.model_path)
            except Exception as exc:
                if self.telemetry is not None:
                    self.telemetry.record(
                        TimelineEventType.MARK,
                        "ocr_custom_model_unavailable",
                        reason=str(exc),
                        model_path=self.model_path,
                    )
                ocr = None

        if ocr is None:
            try:
                ocr = await self._load_engine(None)
            except (ImportError, OSError) as exc:
                if self.telemetry is not None:
                    self.telemetry.record(
                        TimelineEventType.MARK,
                        "ocr_unsolved",
                        reason="ddddocr_unavailable",
                        error_type=type(exc).__name__,
                    )
                return VerificationResult(
                    solved=False,
                    answer=None,
                    provider=PROVIDER_NAME,
                    detail={
                        "reason": "ddddocr_unavailable",
                        "error_type": type(exc).__name__,
                    },
                )
            except Exception as exc:
                if self.telemetry is not None:
                    self.telemetry.record(
                        TimelineEventType.MARK,
                        "ocr_unsolved",
                        reason="ocr_failed",
                        error_type=type(exc).__name__,
                    )
                return VerificationResult(
                    solved=False,
                    answer=None,
                    provider=PROVIDER_NAME,
                    detail={
                        "reason": "ocr_failed",
                        "error_type": type(exc).__name__,
                    },
                )

        try:
            classification_kwargs: dict[str, Any] = {}
            if self.color_filter_colors:
                classification_kwargs["color_filter_colors"] = list(
                    self.color_filter_colors
                )
            raw_answer = await asyncio.to_thread(
                ocr.classification,
                challenge.image_bytes,
                **classification_kwargs,
            )
        except Exception as exc:
            if self.telemetry is not None:
                self.telemetry.record(
                    TimelineEventType.MARK,
                    "ocr_unsolved",
                    reason="ocr_failed",
                    error_type=type(exc).__name__,
                )
            return VerificationResult(
                solved=False,
                answer=None,
                provider=PROVIDER_NAME,
                detail={"reason": "ocr_failed", "error_type": type(exc).__name__},
            )

        answer = (raw_answer or "").strip()
        if len(answer) < self.min_length:
            if self.telemetry is not None:
                self.telemetry.record(
                    TimelineEventType.MARK,
                    "ocr_unsolved",
                    reason="empty_ocr_result",
                    length=len(answer),
                )
            return VerificationResult(
                solved=False,
                answer=None,
                provider=PROVIDER_NAME,
                detail={"reason": "empty_ocr_result"},
            )

        if self.telemetry is not None:
            self.telemetry.record(
                TimelineEventType.MARK,
                "ocr_solved",
                provider=PROVIDER_NAME,
                length=len(answer),
            )
        return VerificationResult(
            solved=True,
            answer=answer,
            provider=PROVIDER_NAME,
            detail={"length": len(answer)},
        )
