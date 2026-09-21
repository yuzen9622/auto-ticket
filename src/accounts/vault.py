from __future__ import annotations

import base64
import json
import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from cryptography.fernet import Fernet, InvalidToken

from .models import (
    COOKIE_ACCOUNT_LABELS,
    AccountStatus,
    CredentialKind,
    CredentialRecord,
    mask_account,
)

logger = logging.getLogger(__name__)

ENV_VAULT_KEY = "AUTO_TICKET_VAULT_KEY"
ENV_KKTIX_ACCOUNT = "AUTO_TICKET_KKTIX_USERNAME"
ENV_KKTIX_KEY = "AUTO_TICKET_KKTIX_PASSWORD"
ENV_TIXCRAFT_ACCOUNT = "AUTO_TICKET_TIXCRAFT_USERNAME"
ENV_TIXCRAFT_KEY = "AUTO_TICKET_TIXCRAFT_PASSWORD"
ENV_IBON_ACCOUNT = "AUTO_TICKET_IBON_USERNAME"
ENV_IBON_KEY = "AUTO_TICKET_IBON_PASSWORD"
KEY_FILENAME = ".vault_key"

ENV_CREDENTIAL_MAP: dict[str, tuple[str, str]] = {
    "kktix": (ENV_KKTIX_ACCOUNT, ENV_KKTIX_KEY),
    "tixcraft": (ENV_TIXCRAFT_ACCOUNT, ENV_TIXCRAFT_KEY),
    "ibon": (ENV_IBON_ACCOUNT, ENV_IBON_KEY),
}


def get_env_credentials(
    platform: Any, environ: Mapping[str, str] | None = None
) -> tuple[str, str] | None:
    plat = _normalize_platform(platform)
    env_keys = ENV_CREDENTIAL_MAP.get(plat)
    if not env_keys:
        return None
    env = os.environ if environ is None else environ
    acc = env.get(env_keys[0], "").strip()
    key = env.get(env_keys[1], "").strip()
    if not acc or not key:
        return None
    return acc, key


class VaultKeyError(ValueError):
    """Vault key 格式不符。訊息嚴禁帶入金鑰內容。"""


class VaultDecryptError(RuntimeError):
    """解密失敗。訊息嚴禁帶入任何內容。"""


class CredentialSource(Protocol):
    name: str

    def available(self, platform: str) -> bool: ...

    def load(self, platform: str) -> tuple[str, str] | None: ...


AuthSource = CredentialSource


def _normalize_platform(platform: Any) -> str:
    val = getattr(platform, "value", platform)
    return str(val).lower()


class EnvCredentialSource:
    name: str = "env"

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._environ = os.environ if environ is None else environ

    def available(self, platform: str) -> bool:
        return self.load(platform) is not None

    def load(self, platform: str) -> tuple[str, str] | None:
        return get_env_credentials(platform, self._environ)


EnvAuthSource = EnvCredentialSource


class EncryptedFileVault:
    name: str = "vault"

    def __init__(self, root: Path, fernet: Fernet) -> None:
        self._root = Path(root)
        self._fernet = fernet

    @classmethod
    def from_env(
        cls,
        root: Path,
        environ: Mapping[str, str] | None = None,
        auto_generate: bool = True,
    ) -> EncryptedFileVault | None:
        env = os.environ if environ is None else environ
        key_path = Path(root) / KEY_FILENAME
        raw_key = env.get(ENV_VAULT_KEY, "").strip()

        # 1. 環境變數未提供時，讀取本地 .vault_key
        if not raw_key:
            if key_path.exists():
                try:
                    raw_key = key_path.read_text(encoding="ascii").strip()
                except Exception as exc:
                    raise VaultKeyError(
                        f"Failed to read vault key from {key_path}"
                    ) from exc
            elif auto_generate:
                try:
                    Path(root).mkdir(parents=True, exist_ok=True)
                    generated_bytes = Fernet.generate_key()
                    raw_key = generated_bytes.decode("ascii")
                    fd = os.open(
                        key_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600
                    )
                    try:
                        with os.fdopen(fd, "w", encoding="ascii") as f:
                            f.write(raw_key)
                        os.chmod(key_path, 0o600)
                    except Exception:
                        if key_path.exists():
                            key_path.unlink(missing_ok=True)
                        raise
                except Exception as exc:
                    raise VaultKeyError(
                        f"Failed to auto-generate vault key at {key_path}"
                    ) from exc
            else:
                return None
        else:
            # 2. 若環境變數提供合法 key 且本地尚未保存 key 檔，順便保存至本地
            if not key_path.exists() and auto_generate:
                try:
                    Path(root).mkdir(parents=True, exist_ok=True)
                    fd = os.open(
                        key_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600
                    )
                    try:
                        with os.fdopen(fd, "w", encoding="ascii") as f:
                            f.write(raw_key)
                        os.chmod(key_path, 0o600)
                    except Exception:
                        if key_path.exists():
                            key_path.unlink(missing_ok=True)
                except Exception as exc:
                    logger.debug("Failed to persist vault key to %s: %s", key_path, exc)

        try:
            key_bytes = raw_key.encode("ascii")
            decoded = base64.urlsafe_b64decode(key_bytes)
            if len(decoded) != 32:
                raise ValueError("Vault key must be 32 urlsafe base64 bytes")
            fernet = Fernet(key_bytes)
        except Exception as exc:
            raise VaultKeyError(
                "Invalid vault key format; expected urlsafe-base64 32 bytes"
            ) from exc
        return cls(root, fernet)

    def _platform_file(self, platform: str) -> Path:
        plat = _normalize_platform(platform)
        return self._root / f"{plat}.bin"

    def available(self, platform: str) -> bool:
        return self._platform_file(platform).exists()

    def store_record(self, record: CredentialRecord) -> None:
        path = self._platform_file(record.platform)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "account": record.account,
            "access_key": record.access_key,
            "kind": record.kind.value,
            "cookies": record.cookies,
        }
        blob = json.dumps(data).encode("utf-8")
        sealed = self._fernet.encrypt(blob)

        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(sealed)
            os.chmod(path, 0o600)
        except Exception:
            if path.exists():
                path.unlink(missing_ok=True)
            raise

    def store(self, platform: str, account: str, access_key: str) -> None:
        record = CredentialRecord(
            platform=platform,
            kind=CredentialKind.PASSWORD,
            account=account,
            access_key=access_key,
            cookies={},
        )
        self.store_record(record)

    def erase(self, platform: str) -> None:
        path = self._platform_file(platform)
        if path.exists():
            path.unlink(missing_ok=True)

    def load_record(self, platform: str) -> CredentialRecord | None:
        path = self._platform_file(platform)
        if not path.exists():
            return None
        try:
            with open(path, "rb") as f:
                sealed = f.read()
            blob = self._fernet.decrypt(sealed)
            data = json.loads(blob.decode("utf-8"))
            raw_kind = data.get("kind", CredentialKind.PASSWORD.value)
            kind = (
                CredentialKind(raw_kind)
                if raw_kind in ("password", "cookie")
                else CredentialKind.PASSWORD
            )
            account = str(data.get("account", ""))
            access_key = str(data.get("access_key", ""))
            cookies = dict(data.get("cookies", {}))
            return CredentialRecord(
                platform=_normalize_platform(platform),
                kind=kind,
                account=account,
                access_key=access_key,
                cookies=cookies,
            )
        except (InvalidToken, ValueError, KeyError) as exc:
            raise VaultDecryptError("Failed to decrypt vault content") from exc

    def load(self, platform: str) -> tuple[str, str] | None:
        record = self.load_record(platform)
        if record is None:
            return None
        return record.account, record.access_key

    def describe(self, platform: str) -> AccountStatus:
        plat = _normalize_platform(platform)
        try:
            record = self.load_record(plat)
            if record is not None:
                if record.kind == CredentialKind.COOKIE:
                    masked = record.account or COOKIE_ACCOUNT_LABELS.get(plat, "cookie")
                else:
                    masked = mask_account(record.account)
                return AccountStatus(
                    platform=plat,
                    source="vault",
                    configured=True,
                    masked_account=masked,
                    credential_kind=record.kind,
                )
        except VaultDecryptError:
            return AccountStatus(
                platform=plat,
                source="vault",
                configured=False,
                masked_account=None,
                credential_kind=None,
            )
        return AccountStatus(
            platform=plat,
            source="vault",
            configured=False,
            masked_account=None,
            credential_kind=None,
        )
