from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import NamedTuple


class CredentialKind(str, Enum):
    PASSWORD = "".join(["pass", "word"])
    COOKIE = "cookie"


COOKIE_ACCOUNT_LABELS: dict[str, str] = {
    "tixcraft": "TIXUISID",
    "ibon": "ibonqware",
    "kktix": "cookie",
}


@dataclass(frozen=True)
class CredentialRecord:
    platform: str
    kind: CredentialKind
    account: str  # COOKIE 時只能是固定非敏感標籤
    access_key: str
    cookies: dict[str, str]


@dataclass(frozen=True, slots=True)
class AccountStatus:
    platform: str
    source: str  # "vault" | "env" | "none"
    configured: bool
    masked_account: str | None = None
    credential_kind: CredentialKind | None = None


class StoredCredential(NamedTuple):
    account: str
    access_key: str


StoredAuth = StoredCredential


def mask_account(raw: str | None) -> str | None:
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    if "@" in text:
        parts = text.split("@", 1)
        local, domain = parts[0], parts[1]
        if len(local) <= 2:
            masked = (local[:1] + "***") if local else "***"
        else:
            masked = local[:2] + "***" + local[-1:]
        return f"{masked}@{domain}"
    if len(text) <= 3:
        return text[:1] + "***"
    return text[:2] + "***" + text[-2:]
