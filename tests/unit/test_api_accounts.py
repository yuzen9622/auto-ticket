from __future__ import annotations

import re

import httpx
import pytest
from cryptography.fernet import Fernet

from accounts.service import AccountService
from accounts.vault import EncryptedFileVault
from api.main import create_app
from broker.broker import SqliteTaskBroker
from broker.jobs import JobKind
from broker.schema import create_broker_schema
from storage.database import Database
from tests.netguard import netguard_autouse  # noqa: F401


@pytest.fixture
async def accounts_app(db: Database, tmp_path):
    await create_broker_schema(db.engine)
    broker = SqliteTaskBroker(db)
    key = Fernet.generate_key()
    vault = EncryptedFileVault(tmp_path, Fernet(key))
    service = AccountService(broker=broker, vault=vault)
    app = create_app(db=db, broker=broker, accounts=service)
    return app, broker, vault


async def test_accounts_status_and_credentials_vault(accounts_app) -> None:
    app, broker, vault = accounts_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        # 1. 預設未設定狀態
        resp = await client.get("/api/v1/accounts/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["configured"] is False
        assert data["source"] == "none"

        # 2. 存入憑證 (write-only)
        test_user = "test_user_kktix"
        put_resp = await client.put(
            "/api/v1/accounts/kktix/credentials",
            json={"account": test_user, "access_key": "dummy_secret_123"},
        )
        assert put_resp.status_code == 204

        # 3. 再次查詢狀態，確認已設定且帳號已遮罩，且回傳絕不包含密碼明文
        status_resp = await client.get("/api/v1/accounts/kktix/status")
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        assert status_data["configured"] is True
        assert status_data["source"] == "vault"
        assert "dummy_secret_123" not in status_resp.text
        assert "dummy_secret_123" not in str(status_data)

        # 4. 觸發 session check 派工
        check_resp = await client.post(
            "/api/v1/accounts/kktix/session/check",
            json={"profile": "live"},
        )
        assert check_resp.status_code == 202
        check_data = check_resp.json()
        job_id = check_data["job_id"]
        assert re.fullmatch(r"[0-9a-f]{16}", job_id)

        # 驗證 broker 中有 SESSION_CHECK 任務
        job = await broker.get(job_id)
        assert job is not None
        assert job.kind == JobKind.SESSION_CHECK

        # 5. 觸發 login 派工
        login_resp = await client.post(
            "/api/v1/accounts/kktix/login",
            json={"mode": "auto", "profile": "live"},
        )
        assert login_resp.status_code == 202
        login_job_id = login_resp.json()["job_id"]
        login_job = await broker.get(login_job_id)
        assert login_job is not None
        assert login_job.kind == JobKind.AUTO_LOGIN

        # 6. 查詢 job 狀態
        job_status_resp = await client.get(f"/api/v1/accounts/jobs/{login_job_id}")
        assert job_status_resp.status_code == 200
        assert job_status_resp.json()["job_id"] == login_job_id


async def test_store_platform_cookie_api_204_and_no_echo(accounts_app) -> None:
    app, _, _ = accounts_app
    sentinel_cookie = "superSecretSentinelCookie999"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        # 1. 儲存 Cookie：204 No Content，無回聲
        cookie_resp = await client.post(
            "/api/v1/accounts/tixcraft/cookie",
            json={"cookies": {"TIXUISID": sentinel_cookie}},
        )
        assert cookie_resp.status_code == 204
        assert cookie_resp.text == ""

        # 2. 查詢狀態：只顯示 credential_kind=cookie 與固定 label，絕不洩漏 sentinel
        status_resp = await client.get("/api/v1/accounts/tixcraft/status")
        assert status_resp.status_code == 200
        data = status_resp.json()
        assert data["configured"] is True
        assert data["credential_kind"] == "cookie"
        assert data["masked_account"] == "TIXUISID"
        assert sentinel_cookie not in status_resp.text

        # 3. 拒絕不合法 Cookie
        bad_resp = await client.post(
            "/api/v1/accounts/tixcraft/cookie",
            json={"cookies": {"TIXUISID": "invalid!@#$"}},
        )
        assert bad_resp.status_code == 400

