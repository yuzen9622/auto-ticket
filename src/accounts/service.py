from __future__ import annotations

from typing import Any

from broker.jobs import JobKind

from .models import (
    COOKIE_ACCOUNT_LABELS,
    AccountStatus,
    CredentialKind,
    CredentialRecord,
    mask_account,
)
from .vault import EncryptedFileVault, EnvCredentialSource


class VaultNotConfiguredError(RuntimeError):
    """Vault 未啟用（未設定 AUTO_TICKET_VAULT_KEY）。"""


class AccountService:
    def __init__(
        self,
        broker: Any,
        vault: EncryptedFileVault | None = None,
        env_source: EnvCredentialSource | None = None,
    ) -> None:
        self._broker = broker
        self._vault = vault
        self._env_source = (
            env_source if env_source is not None else EnvCredentialSource()
        )

    @property
    def vault(self) -> EncryptedFileVault | None:
        return self._vault

    def status(self, platform: str) -> AccountStatus:
        plat = str(platform).lower()
        if self._vault is not None and self._vault.available(plat):
            return self._vault.describe(plat)
        if self._env_source is not None and self._env_source.available(plat):
            pair = self._env_source.load(plat)
            return AccountStatus(
                platform=plat,
                source="env",
                configured=True,
                masked_account=mask_account(pair[0]) if pair else None,
                credential_kind=CredentialKind.PASSWORD,
            )
        return AccountStatus(
            platform=plat,
            source="none",
            configured=False,
            masked_account=None,
            credential_kind=None,
        )

    def get_status(self, platform: str = "kktix") -> AccountStatus:
        return self.status(platform)

    def store(self, platform: str, account: str, access_key: str) -> None:
        if self._vault is None:
            raise VaultNotConfiguredError(
                "Vault is not configured; set AUTO_TICKET_VAULT_KEY to enable vault storage"
            )
        self._vault.store(platform, account, access_key)

    def store_cookie(self, platform: str, cookies: dict[str, str]) -> None:
        if self._vault is None:
            raise VaultNotConfiguredError(
                "Vault is not configured; set AUTO_TICKET_VAULT_KEY to enable vault storage"
            )
        plat = str(platform).lower()
        # 驗證拓元 TIXUISID / ibon ibonqware
        if plat == "tixcraft":
            val = cookies.get("TIXUISID", "").strip()
            if not val or not val.isalnum():
                raise ValueError("Invalid TIXUISID cookie value")
        elif plat == "ibon":
            val = cookies.get("ibonqware", "").strip()
            if not val:
                raise ValueError("Invalid ibonqware cookie value")

        label = COOKIE_ACCOUNT_LABELS.get(plat, "cookie")
        record = CredentialRecord(
            platform=plat,
            kind=CredentialKind.COOKIE,
            account=label,  # 固定非敏感標籤，絕不使用 cookie 值推導
            access_key="",
            cookies=cookies,
        )
        self._vault.store_record(record)

    def load_record(self, platform: str) -> CredentialRecord | None:
        plat = str(platform).lower()
        if self._vault is not None and self._vault.available(plat):
            return self._vault.load_record(plat)
        if self._env_source is not None and self._env_source.available(plat):
            pair = self._env_source.load(plat)
            if pair is not None:
                return CredentialRecord(
                    platform=plat,
                    kind=CredentialKind.PASSWORD,
                    account=pair[0],
                    access_key=pair[1],
                    cookies={},
                )
        return None

    def erase(self, platform: str) -> None:
        if self._vault is not None:
            self._vault.erase(platform)

    async def request_session_check(
        self,
        platform: str,
        profile: str = "live",
    ) -> str:
        plat = str(platform).lower()
        return await self._broker.enqueue(
            kind=JobKind.SESSION_CHECK,
            profile=profile,
            payload={"platform": plat},
        )

    async def request_login(
        self,
        platform: str,
        profile: str = "live",
        mode: str = "auto",
    ) -> str:
        plat = str(platform).lower()
        kind = JobKind.AUTO_LOGIN if mode == "auto" else JobKind.MANUAL_LOGIN
        return await self._broker.enqueue(
            kind=kind,
            profile=profile,
            payload={"platform": plat, "mode": mode},
        )
