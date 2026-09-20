from __future__ import annotations

import stat
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from accounts.models import mask_account
from accounts.service import AccountService, VaultNotConfiguredError
from accounts.vault import (
    EncryptedFileVault,
    EnvCredentialSource,
    VaultDecryptError,
    VaultKeyError,
)
from tests.netguard import netguard_autouse  # noqa: F401


def test_mask_account() -> None:
    assert mask_account("test@example.com") == "te***t@example.com"
    assert mask_account("user1234") == "us***34"
    assert mask_account("ab") == "a***"
    assert mask_account("") is None


def test_env_credential_source() -> None:
    # 兩個都沒填
    src = EnvCredentialSource({})
    assert not src.available("kktix")
    assert src.load("kktix") is None

    # 只填一個
    src_half1 = EnvCredentialSource({"AUTO_TICKET_KKTIX_USERNAME": "foo"})
    assert not src_half1.available("kktix")
    assert src_half1.load("kktix") is None

    src_half2 = EnvCredentialSource({"AUTO_TICKET_KKTIX_PASSWORD": "bar"})
    assert not src_half2.available("kktix")
    assert src_half2.load("kktix") is None

    # 兩個都填
    src_full = EnvCredentialSource(
        {
            "AUTO_TICKET_KKTIX_USERNAME": "alice",
            "AUTO_TICKET_KKTIX_PASSWORD": "secret123",
        }
    )
    assert src_full.available("kktix")
    assert src_full.load("kktix") == ("alice", "secret123")

    # 非 kktix 平台
    assert src_full.load("other") is None


def test_encrypted_file_vault_roundtrip(tmp_path: Path) -> None:
    key = Fernet.generate_key()
    fernet = Fernet(key)
    vault = EncryptedFileVault(tmp_path, fernet)

    assert not vault.available("kktix")
    assert vault.load("kktix") is None

    acc = "myuser@example.com"
    key_val = "mypassword"
    vault.store("kktix", acc, key_val)

    assert vault.available("kktix")
    bin_file = tmp_path / "kktix.bin"
    assert bin_file.exists()

    # 驗證落地檔為密文，絕不含明文
    raw_bytes = bin_file.read_bytes()
    assert acc.encode() not in raw_bytes
    assert key_val.encode() not in raw_bytes

    # 驗證檔案權限 0600
    file_stat = bin_file.stat()
    assert stat.S_IMODE(file_stat.st_mode) == 0o600

    # 驗證讀回資料
    loaded = vault.load("kktix")
    assert loaded == (acc, key_val)

    # 驗證 describe
    status = vault.describe("kktix")
    assert status.configured
    assert status.source == "vault"
    assert status.masked_account == mask_account(acc)

    # 驗證 erase
    vault.erase("kktix")
    assert not vault.available("kktix")
    assert vault.load("kktix") is None


def test_encrypted_file_vault_decrypt_error(tmp_path: Path) -> None:
    key1 = Fernet.generate_key()
    key2 = Fernet.generate_key()
    vault1 = EncryptedFileVault(tmp_path, Fernet(key1))
    vault2 = EncryptedFileVault(tmp_path, Fernet(key2))

    vault1.store("kktix", "user", "secret")
    with pytest.raises(VaultDecryptError) as exc_info:
        vault2.load("kktix")
    assert "user" not in str(exc_info.value)
    assert "secret" not in str(exc_info.value)


def test_encrypted_file_vault_from_env(tmp_path: Path) -> None:
    # 1. auto_generate=False 時，沒 key 回 None
    assert EncryptedFileVault.from_env(tmp_path, {}, auto_generate=False) is None

    # 2. 預設 auto_generate=True：沒 key 時自動生成並保存 .vault_key
    vault_auto = EncryptedFileVault.from_env(tmp_path, {})
    assert vault_auto is not None
    key_file = tmp_path / ".vault_key"
    assert key_file.exists()
    saved_key = key_file.read_text(encoding="ascii").strip()
    assert len(saved_key) > 0

    # 3. 再次載入時讀取同一個 .vault_key
    vault_auto.store("kktix", "user_auto", "pass_auto")
    vault_reloaded = EncryptedFileVault.from_env(tmp_path, {})
    assert vault_reloaded is not None
    pair = vault_reloaded.load("kktix")
    assert pair == ("user_auto", "pass_auto")

    # 4. key 格式錯誤抛 VaultKeyError
    with pytest.raises(VaultKeyError):
        EncryptedFileVault.from_env(tmp_path, {"AUTO_TICKET_VAULT_KEY": "invalid_key"})

    # 5. 合法 key 優先使用
    valid_key = Fernet.generate_key().decode("ascii")
    tmp_path_2 = tmp_path / "another_vault"
    vault = EncryptedFileVault.from_env(tmp_path_2, {"AUTO_TICKET_VAULT_KEY": valid_key})
    assert vault is not None
    assert (tmp_path_2 / ".vault_key").exists()


def test_account_service_priority(tmp_path: Path) -> None:
    class DummyBroker:
        pass

    broker = DummyBroker()

    # 1. 均未設定
    service = AccountService(broker, vault=None, env_source=EnvCredentialSource({}))
    st = service.status("kktix")
    assert not st.configured
    assert st.source == "none"

    # 2. 只有 env 設定
    env_source = EnvCredentialSource(
        {
            "AUTO_TICKET_KKTIX_USERNAME": "bob@example.com",
            "AUTO_TICKET_KKTIX_PASSWORD": "p",
        }
    )
    service2 = AccountService(broker, vault=None, env_source=env_source)
    st2 = service2.status("kktix")
    assert st2.configured
    assert st2.source == "env"
    assert st2.masked_account == "bo***b@example.com"

    # 3. vault 優先於 env
    key = Fernet.generate_key()
    vault = EncryptedFileVault(tmp_path, Fernet(key))
    vault.store("kktix", "alice@example.com", "secret")

    service3 = AccountService(broker, vault=vault, env_source=env_source)
    st3 = service3.status("kktix")
    assert st3.configured
    assert st3.source == "vault"
    assert st3.masked_account == "al***e@example.com"

    # 測試 vault 未配置時 store 拋錯
    service_no_vault = AccountService(broker, vault=None)
    with pytest.raises(VaultNotConfiguredError):
        service_no_vault.store("kktix", "a", "b")
