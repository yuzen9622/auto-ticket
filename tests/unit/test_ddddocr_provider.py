# ruff: noqa: I001
from tests.netguard import netguard_autouse  # noqa: F401

import threading
from unittest.mock import AsyncMock, MagicMock

import pytest

from adapters.verification.base import (
    ChallengeKind,
    VerificationChallenge,
    VerificationResult,
)
from adapters.verification.ddddocr_provider import (
    _ENGINES,
    DdddOcrProvider,
)
from adapters.verification.routing import RoutingVerificationProvider
from telemetry.timeline import TimelineEventType, TimelineRecorder


@pytest.fixture(autouse=True)
def _clear_ocr_engines():
    _ENGINES.clear()
    yield
    _ENGINES.clear()


@pytest.mark.asyncio
async def test_rejects_text_quiz():
    provider = DdddOcrProvider()
    challenge = VerificationChallenge(kind=ChallengeKind.TEXT_QUIZ, question="What is 1+1?")
    result = await provider.solve(challenge)
    assert not result.solved
    assert result.detail.get("reason") == "kind_not_supported"


@pytest.mark.asyncio
async def test_rejects_missing_image_bytes():
    provider = DdddOcrProvider()
    challenge = VerificationChallenge(
        kind=ChallengeKind.IMAGE_CAPTCHA, question="", image_bytes=None
    )
    result = await provider.solve(challenge)
    assert not result.solved
    assert result.detail.get("reason") == "no_image_bytes"


@pytest.mark.asyncio
async def test_solves_with_stubbed_engine(monkeypatch):
    stub_engine = MagicMock()
    stub_engine.classification.return_value = " AB12 "

    async def _stub_get_ocr(model_path: str | None):
        return stub_engine

    monkeypatch.setattr("adapters.verification.ddddocr_provider._get_ocr", _stub_get_ocr)

    provider = DdddOcrProvider()
    challenge = VerificationChallenge(
        kind=ChallengeKind.IMAGE_CAPTCHA, question="", image_bytes=b"fake-bytes"
    )
    result = await provider.solve(challenge)
    assert result.solved
    assert result.answer == "AB12"
    assert result.detail.get("length") == 4


@pytest.mark.asyncio
async def test_reports_unavailable_engine(monkeypatch):
    def _fail_build(model_path: str | None):
        raise ImportError("No module named ddddocr")

    monkeypatch.setattr("adapters.verification.ddddocr_provider._build_ocr", _fail_build)

    provider = DdddOcrProvider()
    challenge = VerificationChallenge(
        kind=ChallengeKind.IMAGE_CAPTCHA, question="", image_bytes=b"fake-bytes"
    )
    result = await provider.solve(challenge)
    assert not result.solved
    assert result.detail.get("reason") == "ddddocr_unavailable"
    assert result.detail.get("error_type") == "ImportError"


@pytest.mark.asyncio
async def test_reports_empty_result(monkeypatch):
    stub_engine = MagicMock()
    stub_engine.classification.return_value = "   "

    async def _stub_get_ocr(model_path: str | None):
        return stub_engine

    monkeypatch.setattr("adapters.verification.ddddocr_provider._get_ocr", _stub_get_ocr)

    provider = DdddOcrProvider()
    challenge = VerificationChallenge(
        kind=ChallengeKind.IMAGE_CAPTCHA, question="", image_bytes=b"fake-bytes"
    )
    result = await provider.solve(challenge)
    assert not result.solved
    assert result.detail.get("reason") == "empty_ocr_result"


@pytest.mark.asyncio
async def test_engine_is_built_once_per_model_path(monkeypatch, tmp_path):
    built_paths: list[str | None] = []

    def _track_build(model_path: str | None):
        built_paths.append(model_path)
        stub = MagicMock()
        stub.classification.return_value = "ABCD"
        return stub

    monkeypatch.setattr("adapters.verification.ddddocr_provider._build_ocr", _track_build)

    provider1 = DdddOcrProvider()
    challenge = VerificationChallenge(
        kind=ChallengeKind.IMAGE_CAPTCHA, question="", image_bytes=b"fake-bytes"
    )
    await provider1.solve(challenge)
    await provider1.solve(challenge)
    assert built_paths == [None]

    other_model = str(tmp_path / "other.onnx")
    provider2 = DdddOcrProvider(model_path=other_model)
    await provider2.solve(challenge)
    assert built_paths == [None, other_model]


@pytest.mark.asyncio
async def test_classification_runs_off_the_event_loop(monkeypatch):
    main_thread_id = threading.get_ident()
    ocr_thread_id = None

    stub_engine = MagicMock()

    def _sync_classify(data: bytes) -> str:
        nonlocal ocr_thread_id
        ocr_thread_id = threading.get_ident()
        return "PASS"

    stub_engine.classification = _sync_classify

    async def _stub_get_ocr(model_path: str | None):
        return stub_engine

    monkeypatch.setattr("adapters.verification.ddddocr_provider._get_ocr", _stub_get_ocr)

    provider = DdddOcrProvider()
    challenge = VerificationChallenge(
        kind=ChallengeKind.IMAGE_CAPTCHA, question="", image_bytes=b"fake-bytes"
    )
    result = await provider.solve(challenge)
    assert result.solved
    assert ocr_thread_id is not None
    assert ocr_thread_id != main_thread_id


@pytest.mark.asyncio
async def test_custom_model_failure_falls_back_to_builtin(monkeypatch, tmp_path):
    recorder = TimelineRecorder()

    def _mock_build(model_path: str | None):
        if model_path is not None:
            raise FileNotFoundError(f"Model not found: {model_path}")
        stub = MagicMock()
        stub.classification.return_value = "BUILTIN"
        return stub

    monkeypatch.setattr("adapters.verification.ddddocr_provider._build_ocr", _mock_build)

    nonexistent = str(tmp_path / "nonexistent.onnx")
    provider = DdddOcrProvider(
        telemetry=recorder, model_path=nonexistent
    )
    challenge = VerificationChallenge(
        kind=ChallengeKind.IMAGE_CAPTCHA, question="", image_bytes=b"fake-bytes"
    )
    result = await provider.solve(challenge)
    assert result.solved
    assert result.answer == "BUILTIN"

    events = [
        e for e in recorder.events()
        if e.event_type == TimelineEventType.MARK and e.name == "ocr_custom_model_unavailable"
    ]
    assert len(events) == 1
    assert nonexistent in str(events[0].detail.get("model_path"))


@pytest.mark.asyncio
async def test_routing_dispatches_by_kind():
    text_provider = AsyncMock()
    text_provider.solve.return_value = VerificationResult(
        solved=True, answer="TEXT_ANS", provider="text_mock"
    )
    image_provider = AsyncMock()
    image_provider.solve.return_value = VerificationResult(
        solved=True, answer="IMAGE_ANS", provider="image_mock"
    )

    router = RoutingVerificationProvider(text=text_provider, image=image_provider)

    text_challenge = VerificationChallenge(kind=ChallengeKind.TEXT_QUIZ, question="q")
    res_text = await router.solve(text_challenge)
    assert res_text.solved
    assert res_text.answer == "TEXT_ANS"
    assert res_text.provider == "text_mock"
    text_provider.solve.assert_called_once_with(text_challenge)

    image_challenge = VerificationChallenge(
        kind=ChallengeKind.IMAGE_CAPTCHA, question="", image_bytes=b"img"
    )
    res_img = await router.solve(image_challenge)
    assert res_img.solved
    assert res_img.answer == "IMAGE_ANS"
    assert res_img.provider == "image_mock"
    image_provider.solve.assert_called_once_with(image_challenge)


@pytest.mark.asyncio
async def test_routing_reports_missing_provider():
    router = RoutingVerificationProvider(text=None, image=None)
    challenge = VerificationChallenge(kind=ChallengeKind.IMAGE_CAPTCHA, question="")
    result = await router.solve(challenge)
    assert not result.solved
    assert result.detail.get("reason") == "no_provider_for_kind"
    assert result.detail.get("kind") == ChallengeKind.IMAGE_CAPTCHA.value


@pytest.mark.asyncio
async def test_telemetry_never_records_image_bytes(monkeypatch):
    recorder = TimelineRecorder()
    stub_engine = MagicMock()
    stub_engine.classification.return_value = "SECRET123"

    async def _stub_get_ocr(model_path: str | None):
        return stub_engine

    monkeypatch.setattr("adapters.verification.ddddocr_provider._get_ocr", _stub_get_ocr)

    raw_bytes = b"TOP_SECRET_IMAGE_BYTES_XYZ"
    provider = DdddOcrProvider(telemetry=recorder)
    challenge = VerificationChallenge(
        kind=ChallengeKind.IMAGE_CAPTCHA, question="", image_bytes=raw_bytes
    )
    result = await provider.solve(challenge)
    assert result.solved

    for event in recorder.events():
        data_str = str(event.detail)
        assert "TOP_SECRET_IMAGE_BYTES_XYZ" not in data_str
        assert "SECRET123" not in data_str  # 亦不記錄原始答案文字
