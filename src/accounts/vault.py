from __future__ import annotations

import base64
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from cryptography.fernet import Fernet, InvalidToken

from .models import AccountStatus, mask_account

ENV_VAULT_KEY = "AUTO_TICKET_VAULT_KEY"
ENV_KKTIX_ACCOUNT = "AUTO_TICKET_KKTIX_USERNAME"
ENV_KKTIX_KEY = "AUTO_TICKET_KKTIX_PASSWORD"


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
        plat = _normalize_platform(platform)
        if plat != "kktix":
            return None
        acc = self._environ.get(ENV_KKTIX_ACCOUNT, "").strip()
        key = self._environ.get(ENV_KKTIX_KEY, "").strip()
        if not acc or not key:
            return None
        return acc, key


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
    ) -> EncryptedFileVault | None:
        env = os.environ if environ is None else environ
        raw_key = env.get(ENV_VAULT_KEY, "").strip()
        if not raw_key:
            return None
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

    def store(self, platform: str, account: str, access_key: str) -> None:
        path = self._platform_file(platform)
        path.parent.mkdir(parents=True, exist_ok=True)
        blob = json.dumps({"account": account, "access_key": access_key}).encode(
            "utf-8"
        )
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

    def erase(self, platform: str) -> None:
        path = self._platform_file(platform)
        if path.exists():
            path.unlink(missing_ok=True)

    def load(self, platform: str) -> tuple[str, str] | None:
        path = self._platform_file(platform)
        if not path.exists():
            return None
        try:
            with open(path, "rb") as f:
                sealed = f.read()
            blob = self._fernet.decrypt(sealed)
            data = json.loads(blob.decode("utf-8"))
            return str(data["account"]), str(data["access_key"])
        except (InvalidToken, ValueError, KeyError) as exc:
            raise VaultDecryptError("Failed to decrypt vault content") from exc

    def describe(self, platform: str) -> AccountStatus:
        plat = _normalize_platform(platform)
        try:
            pair = self.load(plat)
            if pair is not None:
                return AccountStatus(
                    platform=plat,
                    source="vault",
                    configured=True,
                    masked_account=mask_account(pair[0]),
                )
        except VaultDecryptError:
            return AccountStatus(
                platform=plat,
                source="vault",
                configured=False,
                masked_account=None,
            )
        return AccountStatus(
            platform=plat,
            source="vault",
            configured=False,
            masked_account=None,
        )
