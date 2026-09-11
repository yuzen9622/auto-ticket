from __future__ import annotations

import asyncio

import pytest

from adapters.verification import (
    BLIND_GUESS_ANSWER,
    ChallengeKind,
    ManualVerificationProvider,
    RuleBasedVerificationProvider,
    SyntheticOcrProvider,
    VerificationChallenge,
    VerificationProvider,
    VerificationResult,
)
from domain.task import VerificationRule
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
    assert isinstance(RuleBasedVerificationProvider(), VerificationProvider)


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


# --------------------------------------------- RuleBasedVerificationProvider


def rule(pattern: str, answer: str, *, is_regex: bool = False) -> VerificationRule:
    return VerificationRule(pattern=pattern, answer=answer, is_regex=is_regex)


async def test_rule_based_matches_substring_case_insensitively() -> None:
    provider = RuleBasedVerificationProvider([rule("organizer", "KKTIX")])
    result = await provider.solve(quiz("What is the ORGANIZER name?"))
    assert result.solved is True
    assert result.answer == "KKTIX"
    assert result.provider == "rule_based"
    assert result.detail["reason"] == "rule_matched"
    assert result.detail["pattern"] == "organizer"


async def test_rule_based_matches_regex() -> None:
    provider = RuleBasedVerificationProvider(
        [rule(r"(\d+)\s*\+\s*(\d+)", "42", is_regex=True)]
    )
    result = await provider.solve(quiz("1 + 41 = ?"))
    assert result.answer == "42"
    assert result.detail["is_regex"] is True


async def test_rule_based_keeps_regex_case_sensitive_by_default() -> None:
    provider = RuleBasedVerificationProvider([rule("KKTIX", "yes", is_regex=True)])
    result = await provider.solve(quiz("kktix 的英文名稱？"))
    assert result.answer == BLIND_GUESS_ANSWER
    assert result.detail["reason"] == "blind_guess"


async def test_rule_based_honours_inline_regex_flags() -> None:
    provider = RuleBasedVerificationProvider([rule("(?i)kktix", "yes", is_regex=True)])
    result = await provider.solve(quiz("KKTIX 的英文名稱？"))
    assert result.answer == "yes"


async def test_rule_based_uses_first_matching_rule() -> None:
    provider = RuleBasedVerificationProvider(
        [rule("名稱", "first"), rule("英文", "second")]
    )
    result = await provider.solve(quiz("主辦單位英文名稱？"))
    assert result.answer == "first"


async def test_rule_based_normalises_whitespace_in_the_question() -> None:
    provider = RuleBasedVerificationProvider([rule("英文 名稱", "KKTIX")])
    result = await provider.solve(quiz("主辦單位\n 英文\t名稱 ？"))
    assert result.answer == "KKTIX"


async def test_rule_based_blind_guesses_when_nothing_matches() -> None:
    provider = RuleBasedVerificationProvider([rule("英文名稱", "KKTIX")])
    result = await provider.solve(quiz("請問今天星期幾？"))
    assert result.solved is True
    assert result.answer == BLIND_GUESS_ANSWER == "A"
    assert result.detail["reason"] == "blind_guess"
    assert result.detail["trace"] == (
        "rule[0] pattern=英文名稱 regex=False -> NO_MATCH",
    )


async def test_rule_based_blind_guesses_without_any_rule() -> None:
    result = await RuleBasedVerificationProvider().solve(quiz())
    assert result.answer == "A"
    assert result.detail["rules"] == 0
    assert result.detail["trace"] == ()


async def test_rule_based_blind_guesses_on_unreadable_question() -> None:
    provider = RuleBasedVerificationProvider([rule("英文名稱", "KKTIX")])
    result = await provider.solve(quiz("   "))
    assert result.answer == "A"
    assert result.detail["reason"] == "blind_guess"


async def test_rule_based_accepts_a_custom_blind_guess() -> None:
    provider = RuleBasedVerificationProvider(blind_guess_answer="B")
    result = await provider.solve(quiz())
    assert result.answer == "B"


async def test_rule_based_skips_a_broken_regex_without_crashing() -> None:
    provider = RuleBasedVerificationProvider(
        [rule("([unclosed", "broken", is_regex=True), rule("星期", "一")]
    )
    result = await provider.solve(quiz("請問今天星期幾？"))
    assert result.answer == "一"
    assert "INVALID_PATTERN" in result.detail["trace"][0]


async def test_rule_based_refuses_image_captcha() -> None:
    provider = RuleBasedVerificationProvider([rule("x", "y")])
    result = await provider.solve(
        VerificationChallenge(
            kind=ChallengeKind.IMAGE_CAPTCHA, question="", image_bytes=b"\x89PNG"
        )
    )
    assert result.solved is False
    assert result.answer is None
    assert result.detail["reason"] == "unsupported_kind"
    assert result.detail["kind"] == "IMAGE_CAPTCHA"


async def test_rule_based_records_the_answer_mark() -> None:
    telemetry = TimelineRecorder()
    provider = RuleBasedVerificationProvider(
        [rule("英文名稱", "KKTIX")], telemetry=telemetry
    )
    await provider.solve(quiz("主辦單位英文名稱？"))
    marks = [e for e in telemetry.events() if e.name == "verification_rule_answer"]
    assert len(marks) == 1
    assert marks[0].detail["reason"] == "rule_matched"
    assert marks[0].detail["answer"] == "KKTIX"


async def test_rule_based_is_silent_without_telemetry() -> None:
    result = await RuleBasedVerificationProvider([rule("x", "y")]).solve(quiz("x"))
    assert result.answer == "y"
