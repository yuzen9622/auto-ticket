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
ENV_CHALLENGE_GRACE_S = "AUTO_TICKET_CHALLENGE_GRACE_S"
ENV_OCR_ENABLED = "AUTO_TICKET_OCR_ENABLED"


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
    # Cloudflare 的自動挑戰多半數秒內自己會過。先安靜等一段有上限的寬限期，
    # 期滿仍在才喊人——一偵測到就發通知會把「系統自己能處理」的情況也丟給使用者。
    challenge_grace_s: float = 45.0
    challenge_poll_s: float = 2.0
    # OCR 總開關。模型載不起來、平台不支援這類環境層問題，不該要使用者逐一改任務設定。
    ocr_enabled: bool = True
    # 借用使用者自己的 Chrome（`http://127.0.0.1:9222`）。KKTIX 的 Cloudflare 擋
    # Playwright 自帶的瀏覽器，開視窗也沒用，只有借用模式過得去。
    cdp_endpoint: str | None = None
    # 沒給 cdp_endpoint 時，自己去把使用者本機的 Chrome 開起來並接上——
    # 不該要求使用者手動下 --remote-debugging-port 再把端點貼回來。
    auto_launch_browser: bool = True
    browser_debug_port: int = 9222

    def __post_init__(self) -> None:
        object.__setattr__(self, "db_path", Path(self.db_path))
        object.__setattr__(self, "screenshot_dir", Path(self.screenshot_dir))
        object.__setattr__(self, "vault_root", Path(self.vault_root))
        object.__setattr__(self, "timeline_dir", Path(self.timeline_dir))

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> WorkerSettings:
        source = os.environ if environ is None else environ
        grace_raw = source.get(ENV_CHALLENGE_GRACE_S)
        grace_s = 45.0
        if grace_raw is not None:
            try:
                grace_s = float(grace_raw)
            except ValueError:
                grace_s = 45.0

        ocr_raw = source.get(ENV_OCR_ENABLED)
        ocr_on = True
        if ocr_raw is not None:
            ocr_on = ocr_raw.strip().lower() not in {"0", "false", "no", "off"}

        return cls(
            db_path=Path(source.get(ENV_DB_PATH, str(DEFAULT_DB_PATH))),
            screenshot_dir=Path(
                source.get(ENV_SCREENSHOT_DIR, str(DEFAULT_SCREENSHOT_DIR))
            ),
            vault_root=Path(source.get(ENV_VAULT_ROOT, str(DEFAULT_VAULT_ROOT))),
            cdp_endpoint=source.get(ENV_CDP_ENDPOINT) or None,
            challenge_grace_s=grace_s,
            ocr_enabled=ocr_on,
        )
