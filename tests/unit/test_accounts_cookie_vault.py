"""Vault Cookie 憑證與向後相容單元測試。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from accounts.models import (
    COOKIE_ACCOUNT_LABELS,
    CredentialKind,
    CredentialRecord,
)
from accounts.service import AccountService
from accounts.vault import EncryptedFileVault


@pytest.fixture
def vault(tmp_path: Path) -> EncryptedFileVault:
    key = Fernet.generate_key()
    fernet = Fernet(key)
    return EncryptedFileVault(tmp_path, fernet)


def test_vault_legacy_blob_compatibility(vault: EncryptedFileVault, tmp_path: Path) -> None:
    # 建立舊版 2 欄位 blob (只有 account 與 access_key)
    legacy_data = {"account": "legacy_user@example.com", "access_key": "legacy_secret"}
    blob = json.dumps(legacy_data).encode("utf-8")
    sealed = vault._fernet.encrypt(blob)
    kktix_path = tmp_path / "kktix.bin"
    kktix_path.write_bytes(sealed)

    # 1. 舊 load() 保持 tuple 契約
    pair = vault.load("kktix")
    assert pair == ("legacy_user@example.com", "legacy_secret")

    # 2. 新 load_record() 預設為 PASSWORD 且 cookies 為空字典
    record = vault.load_record("kktix")
    assert record is not None
    assert record.kind == CredentialKind.PASSWORD
    assert record.account == "legacy_user@example.com"
    assert record.access_key == "legacy_secret"
    assert record.cookies == {}

    # 3. describe() 顯示 masked_account
    status = vault.describe("kktix")
    assert status.configured is True
    assert status.credential_kind == CredentialKind.PASSWORD
    assert status.masked_account is not None
    assert "@example.com" in status.masked_account


def test_vault_store_and_load_cookie_record(vault: EncryptedFileVault) -> None:
    sentinel_cookie = "sentinel_secret_cookie_val_999"
    record = CredentialRecord(
        platform="tixcraft",
        kind=CredentialKind.COOKIE,
        account=COOKIE_ACCOUNT_LABELS["tixcraft"],
        access_key="",
        cookies={"TIXUISID": sentinel_cookie},
    )
    vault.store_record(record)

    # load_record()
    loaded = vault.load_record("tixcraft")
    assert loaded is not None
    assert loaded.kind == CredentialKind.COOKIE
    assert loaded.account == "TIXUISID"
    assert loaded.cookies == {"TIXUISID": sentinel_cookie}

    # describe() 絕不外洩 sentinel_cookie
    status = vault.describe("tixcraft")
    assert status.configured is True
    assert status.credential_kind == CredentialKind.COOKIE
    assert status.masked_account == "TIXUISID"
    # 敏感 sentinel 絕不出現在 status 任何欄位
    assert sentinel_cookie not in str(status)


def test_account_service_store_cookie_validation(vault: EncryptedFileVault) -> None:
    broker = None
    service = AccountService(broker=broker, vault=vault)

    # 正常儲存拓元 Cookie
    service.store_cookie("tixcraft", {"TIXUISID": "validAlphanumeric12345"})
    status = service.status("tixcraft")
    assert status.configured is True
    assert status.credential_kind == CredentialKind.COOKIE
    assert status.masked_account == "TIXUISID"

    # 拒絕不合法的 TIXUISID (含特殊符號或空值)
    with pytest.raises(ValueError, match="Invalid TIXUISID"):
        service.store_cookie("tixcraft", {"TIXUISID": "invalid!@#$"})

    with pytest.raises(ValueError, match="Invalid TIXUISID"):
        service.store_cookie("tixcraft", {"TIXUISID": ""})

    # 正常儲存 ibon Cookie
    service.store_cookie("ibon", {"ibonqware": "valid_ibon_token_123"})
    ibon_status = service.status("ibon")
    assert ibon_status.configured is True
    assert ibon_status.credential_kind == CredentialKind.COOKIE
    assert ibon_status.masked_account == "ibonqware"

    # 拒絕空值 ibonqware
    with pytest.raises(ValueError, match="Invalid ibonqware"):
        service.store_cookie("ibon", {"ibonqware": ""})
