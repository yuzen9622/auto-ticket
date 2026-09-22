from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROFILE_NAME_RE = re.compile(r"\A[a-zA-Z0-9_-]{1,64}\Z")
SAFE_FILENAME_RE = re.compile(r"\A[a-zA-Z0-9_.-]+\Z")
DEFAULT_SCREENSHOT_DIR = Path("data/screenshots")
ENV_BROWSER_PROFILE_ROOT = "AUTO_TICKET_BROWSER_PROFILE_ROOT"


def default_profile_root() -> Path:
    """persistent context 的落地根目錄。

    未設環境變數時維持 CWD 相對的 `.browser_profiles`——開發流程照舊。
    打包安裝的情境沒有「專案目錄」可相對，launcher 會把它指到 `~/.auto-ticket`。
    """
    return Path(os.environ.get(ENV_BROWSER_PROFILE_ROOT) or ".browser_profiles")

DEFAULT_MACOS_CHROME_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)

DEFAULT_CHROMIUM_ARGS = (
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--no-first-run",
    "--no-default-browser-check",
)


@dataclass(frozen=True, slots=True)
class BrowserProfile:
    name: str
    user_data_dir: Path | None = None
    viewport: dict[str, int] | None = None
    locale: str = "zh-TW"
    timezone_id: str = "Asia/Taipei"
    user_agent: str = DEFAULT_MACOS_CHROME_USER_AGENT
    headless: bool = True
    """有頭模式是使用者自行登入該 profile 的唯一途徑，因此必須可由呼叫端覆寫。"""

    def __post_init__(self) -> None:
        if not PROFILE_NAME_RE.fullmatch(self.name):
            raise ValueError(f"Invalid profile name: {self.name!r}")
        if self.user_data_dir is None:
            object.__setattr__(self, "user_data_dir", default_profile_root() / self.name)
        if self.viewport is None:
            object.__setattr__(self, "viewport", {"width": 1920, "height": 1080})


def build_persistent_context_options(
    profile: BrowserProfile,
    extra_args: Sequence[str] = (),
) -> dict[str, Any]:
    args = list(DEFAULT_CHROMIUM_ARGS)
    for a in extra_args:
        if a not in args:
            args.append(a)
    return {
        "headless": profile.headless,
        "args": args,
        "viewport": profile.viewport,
        "locale": profile.locale,
        "timezone_id": profile.timezone_id,
        "user_agent": profile.user_agent,
        "ignore_default_args": ["--enable-automation"],
    }


def sanitize_path_component(value: str, fallback: str = "unknown") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("._")
    return cleaned[:64] or fallback


def screenshot_filename(experiment_id: str, sequence: int, state: str) -> str:
    if not (type(sequence) is int and not isinstance(sequence, bool) and sequence >= 0):
        raise ValueError("sequence must be non-negative int")
    safe_exp = sanitize_path_component(experiment_id, "default")
    safe_state = sanitize_path_component(state, "STATE")
    return f"{safe_exp}_{sequence:04d}_{safe_state}.png"


def resolve_screenshot_path(base_dir: Path, filename: str) -> Path:
    dest = (base_dir / filename).resolve()
    base = base_dir.resolve()
    if not dest.is_relative_to(base):
        raise ValueError(f"Path traversal detected: {filename}")
    return dest
