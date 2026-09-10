from __future__ import annotations

import asyncio

import pytest

from adapters.verification import (
    ChallengeKind,
    ManualVerificationProvider,
    SyntheticOcrProvider,
    VerificationChallenge,
    VerificationProvider,
    VerificationResult,
)
from telemetry.timeline import TimelineRecorder
from tests.netguard import netguard_autouse  # noqa: F401


def quiz(question: str = "1 + 41 = ?") -> VerificationChallenge:
    return VerificationChallenge(kind=ChallengeKind.TEXT_QUIZ, question=question)


async def test_manual_returns_trimmed_answer() -> None:
    async def source(challenge: VerificationChallenge) -> str:
        return "  42  "

    result = await ManualVerificationProvider(source).solve(quiz())
    assert result.solved is True
    assert result.answer == "42"
    assert result.provider == "manual"


async def test_manual_receives_the_challenge() -> None:
    seen: list[VerificationChallenge] = []

    async def source(challenge: VerificationChallenge) -> str:
        seen.append(challenge)
        return "ok"

    await ManualVerificationProvider(source).solve(quiz("主辦單位英文名稱？"))
    assert seen[0].question == "主辦單位英文名稱？"
    assert seen[0].kind is ChallengeKind.TEXT_QUIZ


async def test_manual_times_out_without_inventing_an_answer() -> None:
    async def slow(challenge: VerificationChallenge) -> str:
        await asyncio.sleep(1)
        return "too late"

    result = await ManualVerificationProvider(slow, timeout_s=0.01).solve(quiz())
    assert result.solved is False
    assert result.answer is None
    assert result.detail["reason"] == "timeout"


async def test_manual_rejects_empty_answer() -> None:
    async def source(challenge: VerificationChallenge) -> str:
        return "   "

    result = await ManualVerificationProvider(source).solve(quiz())
    assert result.solved is False
    assert result.detail["reason"] == "empty_answer"


async def test_manual_reports_answer_source_failure() -> None:
    async def broken(challenge: VerificationChallenge) -> str:
        raise ValueError("stdin closed")

    result = await ManualVerificationProvider(broken).solve(quiz())
    assert result.solved is False
    assert result.detail["reason"] == "answer_source_failed"
    assert result.detail["error_type"] == "ValueError"


async def test_manual_records_prompt_mark() -> None:
    telemetry = TimelineRecorder()

    async def source(challenge: VerificationChallenge) -> str:
        return "42"

    await ManualVerificationProvider(source, telemetry=telemetry).solve(quiz())
    marks = [e for e in telemetry.events() if e.name == "verification_prompted"]
    assert len(marks) == 1
    assert marks[0].detail["kind"] == ChallengeKind.TEXT_QUIZ.value


async def test_synthetic_ocr_is_an_honest_stub() -> None:
    with pytest.raises(NotImplementedError):
        await SyntheticOcrProvider().solve(quiz())


def test_providers_satisfy_the_port() -> None:
    async def source(challenge: VerificationChallenge) -> str:
        return "42"

    assert isinstance(ManualVerificationProvider(source), VerificationProvider)
    assert isinstance(SyntheticOcrProvider(), VerificationProvider)


def test_challenge_and_result_are_immutable() -> None:
    challenge = quiz()
    with pytest.raises(Exception):
        challenge.question = "changed"  # type: ignore[misc]
    result = VerificationResult(True, "42", "manual")
    with pytest.raises(Exception):
        result.solved = False  # type: ignore[misc]


def test_image_captcha_carries_bytes() -> None:
    challenge = VerificationChallenge(
        kind=ChallengeKind.IMAGE_CAPTCHA, question="", image_bytes=b"\x89PNG"
    )
    assert challenge.image_bytes == b"\x89PNG"
    assert challenge.kind.value == "IMAGE_CAPTCHA"
