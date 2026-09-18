from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

DEFAULT_DB_PATH = Path("data/auto-ticket.db")
DEFAULT_SCREENSHOT_DIR = Path("data/screenshots")
DEFAULT_VAULT_ROOT = Path("data/credentials")
DEFAULT_CORS_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://localhost:3000",
)

# 預熱最早階段 PREPARE_BROWSER 落在 T-10min；多留 1 分鐘給 claim 與組裝。
DEFAULT_WARMUP_LEAD = timedelta(minutes=11)

ENV_DB_PATH = "AUTO_TICKET_DB_PATH"
ENV_SCREENSHOT_DIR = "AUTO_TICKET_SCREENSHOT_DIR"
ENV_VAULT_ROOT = "AUTO_TICKET_VAULT_ROOT"
ENV_CORS_ORIGINS = "AUTO_TICKET_CORS_ORIGINS"


@dataclass(frozen=True, slots=True)
class ApiSettings:
    """API Server 的設定。

    **不含任何憑證欄位**：憑證只從環境變數或加密 vault 取得，永不經過設定物件，
    否則 `repr()` 一進 log 就全裸（G29）。
    """

    db_path: Path = DEFAULT_DB_PATH
    screenshot_dir: Path = DEFAULT_SCREENSHOT_DIR
    vault_root: Path = DEFAULT_VAULT_ROOT
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS
    default_profile: str = "live"
    warmup_lead: timedelta = DEFAULT_WARMUP_LEAD
    pump_poll_ms: int = 100
    prune_interval_s: float = 60.0
    lease_ttl_s: float = 30.0
    version: str = "0.1.0"
    resolver_orgs: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "db_path", Path(self.db_path))
        object.__setattr__(self, "screenshot_dir", Path(self.screenshot_dir))
        object.__setattr__(self, "vault_root", Path(self.vault_root))

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ApiSettings:
        source = os.environ if environ is None else environ
        origins = source.get(ENV_CORS_ORIGINS, "").strip()
        return cls(
            db_path=Path(source.get(ENV_DB_PATH, str(DEFAULT_DB_PATH))),
            screenshot_dir=Path(
                source.get(ENV_SCREENSHOT_DIR, str(DEFAULT_SCREENSHOT_DIR))
            ),
            vault_root=Path(source.get(ENV_VAULT_ROOT, str(DEFAULT_VAULT_ROOT))),
            cors_origins=(
                tuple(o.strip() for o in origins.split(",") if o.strip())
                if origins
                else DEFAULT_CORS_ORIGINS
            ),
        )
