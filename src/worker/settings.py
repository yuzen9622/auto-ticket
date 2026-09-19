from __future__ import annotations

import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB_PATH = Path("data/auto-ticket.db")
DEFAULT_SCREENSHOT_DIR = Path("data/screenshots")
DEFAULT_VAULT_ROOT = Path("data/credentials")
DEFAULT_TIMELINE_DIR = Path("data/timelines")

ENV_DB_PATH = "AUTO_TICKET_DB_PATH"
ENV_SCREENSHOT_DIR = "AUTO_TICKET_SCREENSHOT_DIR"
ENV_VAULT_ROOT = "AUTO_TICKET_VAULT_ROOT"
ENV_CDP_ENDPOINT = "AUTO_TICKET_CDP_ENDPOINT"


def default_worker_id() -> str:
    return f"worker_{socket.gethostname()}_{os.getpid()}"


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    """Worker 行程設定。**不含任何憑證欄位**（G29）。"""

    db_path: Path = DEFAULT_DB_PATH
    screenshot_dir: Path = DEFAULT_SCREENSHOT_DIR
    vault_root: Path = DEFAULT_VAULT_ROOT
    timeline_dir: Path = DEFAULT_TIMELINE_DIR
    profile: str = "live"
    headless: bool = True
    poll_ms: int = 500
    clock_tick_hz: float = 1.0
    control_poll_ms: int = 200
    outbox_flush_ms: int = 100
    lease_ttl_s: float = 30.0
    # 搶票任務重跑一次多半已經過了開賣時間，重試只會製造假資料。
    max_concurrent_jobs: int = 1
    manual_login_timeout_s: float = 600.0
    manual_login_notice_s: float = 5.0
    # 人機驗證要真人去點，240 秒常常不夠他發現通知再走到瀏覽器前面。
    session_gate_timeout_s: float = 600.0
    # 借用使用者自己的 Chrome（`http://127.0.0.1:9222`）。KKTIX 的 Cloudflare 擋
    # Playwright 自帶的瀏覽器，開視窗也沒用，只有借用模式過得去。
    cdp_endpoint: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "db_path", Path(self.db_path))
        object.__setattr__(self, "screenshot_dir", Path(self.screenshot_dir))
        object.__setattr__(self, "vault_root", Path(self.vault_root))
        object.__setattr__(self, "timeline_dir", Path(self.timeline_dir))

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> WorkerSettings:
        source = os.environ if environ is None else environ
        return cls(
            db_path=Path(source.get(ENV_DB_PATH, str(DEFAULT_DB_PATH))),
            screenshot_dir=Path(
                source.get(ENV_SCREENSHOT_DIR, str(DEFAULT_SCREENSHOT_DIR))
            ),
            vault_root=Path(source.get(ENV_VAULT_ROOT, str(DEFAULT_VAULT_ROOT))),
            cdp_endpoint=source.get(ENV_CDP_ENDPOINT) or None,
        )
