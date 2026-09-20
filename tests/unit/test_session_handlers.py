from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from accounts.vault import VaultDecryptError
from adapters.ticketing.kktix.adapter import KKTIXPageKind
from broker.jobs import JobKind, JobRecord, JobState
from tests.netguard import netguard_autouse  # noqa: F401
from worker.handlers.session import (
    KKTIX_AUTH_COOKIE_NAMES,
    LoginState,
    execute_auto_login,
    has_kktix_auth_cookies,
    probe_login_state,
    summarize_cookies,
)


def _make_job_record(
    job_id: str,
    kind: JobKind = JobKind.AUTO_LOGIN,
    payload: dict | None = None,
    profile: str = "live",
) -> JobRecord:
    now = datetime.now(timezone.utc)
    return JobRecord(
        id=job_id,
        kind=kind,
        task_id=None,
        profile=profile,
        payload=MappingProxyType(payload or {}),
        state=JobState.PENDING,
        available_at=now,
        worker_id=None,
        lease_expires_at=None,
        heartbeat_at=None,
        attempt=0,
        max_attempts=3,
        result=None,
        error=None,
        created_at=now,
        updated_at=now,
    )


def test_has_kktix_auth_cookies() -> None:
    # 訪客只有 session 或 locale，未登入
    guest_cookies = [
        {"domain": ".kktix.com", "name": "_kktix_session", "value": "abc123xyz"},
        {"domain": ".kktix.com", "name": "locale", "value": "zh-TW"},
    ]
    assert has_kktix_auth_cookies(guest_cookies) is False
    assert has_kktix_auth_cookies([]) is False
    assert has_kktix_auth_cookies(None) is False

    # 登入後擁有 user_id_v2 等欄位
    for cookie_name in KKTIX_AUTH_COOKIE_NAMES:
        user_cookies = [
            {"domain": "kktix.com", "name": cookie_name, "value": "5507559"},
        ]
        assert has_kktix_auth_cookies(user_cookies) is True

    # 空值不計為有效登入
    empty_val_cookies = [
        {"domain": "kktix.com", "name": "user_id_v2", "value": "  "},
    ]
    assert has_kktix_auth_cookies(empty_val_cookies) is False


def test_summarize_cookies_kktix_distinguishes_guest_from_logged_in() -> None:
    # 訪客 session 在 KKTIX 不該被當成已登入
    guest_cookies = [
        {"domain": ".kktix.com", "name": "_kktix_session", "value": "session123"},
    ]
    count, has_session = summarize_cookies(guest_cookies, host_fragment="kktix")
    assert count == 1
    assert has_session is False

    # 登入後擁有 user_id_v2
    logged_in_cookies = [
        {"domain": ".kktix.com", "name": "_kktix_session", "value": "session123"},
        {"domain": "kktix.com", "name": "user_id_v2", "value": "5507559"},
        {"domain": "kktix.com", "name": "user_display_name_v2", "value": "oscar689"},
    ]
    count, has_session = summarize_cookies(logged_in_cookies, host_fragment="kktix")
    assert count == 3
    assert has_session is True


def test_summarize_cookies_other_platforms() -> None:
    # 其他平台仍以 session / token 判定
    cookies = [
        {"domain": ".tixcraft.com", "name": "SID", "value": "123"},
        {"domain": ".tixcraft.com", "name": "session_id", "value": "456"},
    ]
    count, has_session = summarize_cookies(cookies, host_fragment="tixcraft")
    assert count == 2
    assert has_session is True


@pytest.mark.asyncio
async def test_probe_login_state_kktix() -> None:
    mock_adapter = MagicMock()
    mock_page = MagicMock()
    mock_page.url = "https://kktix.test/"

    # 1. 人機驗證時一律 UNKNOWN
    mock_adapter.probe_page = AsyncMock(return_value=KKTIXPageKind.CHALLENGE)
    state = await probe_login_state(mock_adapter, mock_page, cookies=[])
    assert state is LoginState.UNKNOWN

    # 2. 在登入頁時一律 LOGGED_OUT
    mock_adapter.probe_page = AsyncMock(return_value=KKTIXPageKind.LOGIN)
    state = await probe_login_state(mock_adapter, mock_page, cookies=[])
    assert state is LoginState.LOGGED_OUT

    # 3. 在非登入頁（如 UNKNOWN 或 EVENT），但無登入 cookie 時應判定為 LOGGED_OUT
    mock_adapter.probe_page = AsyncMock(return_value=KKTIXPageKind.UNKNOWN)
    guest_cookies = [{"domain": ".kktix.com", "name": "_kktix_session", "value": "guest"}]
    state = await probe_login_state(mock_adapter, mock_page, cookies=guest_cookies, platform="kktix")
    assert state is LoginState.LOGGED_OUT

    # 4. 在非登入頁，且有 user_id_v2 時判定為 LOGGED_IN
    logged_in_cookies = [{"domain": "kktix.com", "name": "user_id_v2", "value": "5507559"}]
    state = await probe_login_state(mock_adapter, mock_page, cookies=logged_in_cookies, platform="kktix")
    assert state is LoginState.LOGGED_IN


@pytest.mark.asyncio
async def test_execute_auto_login_vault_decrypt_error_handling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # 模擬 Vault 拋出 VaultDecryptError，且無環境變數憑證
    mock_vault = MagicMock()
    mock_vault.available.return_value = True
    mock_vault.load.side_effect = VaultDecryptError("Failed to decrypt vault content")

    monkeypatch.setattr("worker.handlers.session.EncryptedFileVault.from_env", lambda root: mock_vault)
    monkeypatch.setattr("worker.handlers.session.EnvCredentialSource.load", lambda self, plat: None)

    mock_broker = MagicMock()
    mock_broker.mark_running = AsyncMock()
    mock_broker.fail = AsyncMock()

    job = _make_job_record("test_job_123", payload={"platform": "kktix"})
    mock_settings = MagicMock()
    mock_settings.vault_root = tmp_path / "vault"

    await execute_auto_login(
        job,
        worker_id="worker_test",
        db=MagicMock(),
        broker=mock_broker,
        outbox=MagicMock(),
        settings=mock_settings,
    )

    mock_broker.fail.assert_awaited_once_with(
        "test_job_123",
        worker_id="worker_test",
        error="vault_decrypt_failed",
        retry=False,
    )


@pytest.mark.asyncio
async def test_execute_auto_login_vault_decrypt_error_fallback_to_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # 模擬 Vault 拋出 VaultDecryptError，但環境變數有憑證，成功 fallback
    mock_vault = MagicMock()
    mock_vault.available.return_value = True
    mock_vault.load.side_effect = VaultDecryptError("Failed to decrypt vault content")

    monkeypatch.setattr("worker.handlers.session.EncryptedFileVault.from_env", lambda root: mock_vault)
    monkeypatch.setattr("worker.handlers.session.EnvCredentialSource.load", lambda self, plat: ("user@test.com", "pass123"))

    # 模擬後續 Playwright 與 Adapter 流程
    mock_browser = MagicMock()
    mock_browser.start = AsyncMock()
    mock_browser.stop = AsyncMock()
    mock_page = MagicMock()
    mock_page.url = "https://kktix.test/users/sign_in"
    mock_page.context.cookies = AsyncMock(side_effect=[
        [],  # 登入前無登入 cookie
        [{"domain": "kktix.com", "name": "user_id_v2", "value": "5507559"}],  # 登入後取得 user_id_v2
    ])
    mock_browser.new_page = AsyncMock(return_value=mock_page)

    monkeypatch.setattr("worker.handlers.session.PlaywrightManager", lambda *args, **kwargs: mock_browser)
    monkeypatch.setattr("worker.handlers.session._ensure_browser_endpoint", AsyncMock(return_value=None))

    mock_adapter = MagicMock()
    # probe_page 呼叫順序：
    # 1. probe_before (LOGIN)
    # 2. probe_login_state before (LOGIN)
    # 3. probe after login (UNKNOWN)
    # 4. probe_login_state after login (UNKNOWN)
    mock_adapter.probe_page = AsyncMock(side_effect=[
        KKTIXPageKind.LOGIN,
        KKTIXPageKind.LOGIN,
        KKTIXPageKind.UNKNOWN,
        KKTIXPageKind.UNKNOWN,
    ])
    mock_adapter.login = AsyncMock(return_value=True)
    monkeypatch.setattr("worker.handlers.session.KKTIXAdapter", lambda *args, **kwargs: mock_adapter)

    mock_broker = MagicMock()
    mock_broker.mark_running = AsyncMock()
    mock_broker.complete = AsyncMock()
    mock_broker.fail = AsyncMock()

    job = _make_job_record("test_job_fallback", payload={"platform": "kktix"})
    mock_settings = MagicMock()
    mock_settings.vault_root = tmp_path / "vault"
    mock_settings.headless = True
    mock_settings.screenshot_dir = tmp_path / "screenshots"

    await execute_auto_login(
        job,
        worker_id="worker_test",
        db=MagicMock(),
        broker=mock_broker,
        outbox=MagicMock(),
        settings=mock_settings,
    )

    # 驗證 adapter.login 被成功呼叫並帶入環境變數帳密
    mock_adapter.login.assert_awaited_once_with(mock_page, "user@test.com", "pass123")
    mock_broker.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_wait_for_turnstile_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    from adapters.payment.mock import MockPaymentProvider
    from adapters.ticketing.kktix.adapter import KKTIXAdapter
    from telemetry.timeline import TimelineRecorder

    telemetry = TimelineRecorder()
    adapter = KKTIXAdapter(telemetry=telemetry, payment=MockPaymentProvider())

    mock_page = MagicMock()
    mock_container = MagicMock()
    mock_input = MagicMock()

    # 模擬 Turnstile 正在驗證，第 1 次 input_value=""，第 2 次填入 token
    mock_input.input_value = AsyncMock(side_effect=["", "cf-token-12345"])

    # 模擬 _first_present
    async def fake_first_present(root: Any, selectors: Any) -> Any:
        if "cf-turnstile-response" in str(selectors):
            return mock_input
        if "cf-turnstile" in str(selectors):
            return mock_container
        return None

    monkeypatch.setattr(adapter, "_first_present", fake_first_present)

    ok = await adapter._wait_for_turnstile(mock_page, timeout_ms=2000)
    assert ok is True
    assert any(e.name == "turnstile_verified" for e in telemetry.events())


@pytest.mark.asyncio
async def test_wait_for_turnstile_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from adapters.payment.mock import MockPaymentProvider
    from adapters.ticketing.kktix.adapter import KKTIXAdapter
    from telemetry.timeline import TimelineRecorder

    telemetry = TimelineRecorder()
    adapter = KKTIXAdapter(telemetry=telemetry, payment=MockPaymentProvider())

    mock_page = MagicMock()
    mock_container = MagicMock()
    mock_input = MagicMock()
    mock_input.input_value = AsyncMock(return_value="")  # 一直為空

    async def fake_first_present(root: Any, selectors: Any) -> Any:
        if "cf-turnstile-response" in str(selectors):
            return mock_input
        if "cf-turnstile" in str(selectors):
            return mock_container
        return None

    monkeypatch.setattr(adapter, "_first_present", fake_first_present)

    ok = await adapter._wait_for_turnstile(mock_page, timeout_ms=300)
    assert ok is False
    assert any(e.name == "turnstile_timeout" for e in telemetry.events())


@pytest.mark.asyncio
async def test_wait_for_turnstile_not_present() -> None:
    from adapters.payment.mock import MockPaymentProvider
    from adapters.ticketing.kktix.adapter import KKTIXAdapter
    from telemetry.timeline import TimelineRecorder

    telemetry = TimelineRecorder()
    adapter = KKTIXAdapter(telemetry=telemetry, payment=MockPaymentProvider())

    mock_page = MagicMock()
    # 頁面上無 Turnstile
    mock_page.locator.return_value.first.count = AsyncMock(return_value=0)

    ok = await adapter._wait_for_turnstile(mock_page, timeout_ms=1000)
    assert ok is True
