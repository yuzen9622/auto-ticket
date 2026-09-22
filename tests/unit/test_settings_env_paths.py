"""路徑類設定的來源優先序：CLI 旗標 > 環境變數 > dataclass 預設。

打包安裝的執行路徑完全靠環境變數把三個行程指到 `~/.auto-ticket/data`；只要有人
在 argparse 上抄了第二份預設值，環境變數就永遠被蓋掉，而且症狀是「資料寫到別的
地方」這種不會當場報錯的錯。這組測試就是守住那條優先序。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from api.settings import ApiSettings
from browser.context_factory import (
    ENV_BROWSER_PROFILE_ROOT,
    BrowserProfile,
    default_profile_root,
)
from worker.__main__ import parse_args, settings_from_args
from worker.settings import WorkerSettings

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_serve_api():
    """`scripts/serve_api.py` 不是套件模組，只能按路徑載入。"""
    spec = importlib.util.spec_from_file_location(
        "serve_api_under_test", REPO_ROOT / "scripts" / "serve_api.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ENV_KEYS = (
    "AUTO_TICKET_DB_PATH",
    "AUTO_TICKET_SCREENSHOT_DIR",
    "AUTO_TICKET_VAULT_ROOT",
    "AUTO_TICKET_TIMELINE_DIR",
    "AUTO_TICKET_BROWSER_PROFILE_ROOT",
    "AUTO_TICKET_CDP_ENDPOINT",
    "AUTO_TICKET_CHALLENGE_GRACE_S",
    "AUTO_TICKET_OCR_ENABLED",
    "AUTO_TICKET_CORS_ORIGINS",
    "AUTO_TICKET_RESOLVER_ORGS",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


class TestWorkerSettingsFromEnv:
    def test_timeline_dir_reads_its_own_env_var(self) -> None:
        settings = WorkerSettings.from_env({"AUTO_TICKET_TIMELINE_DIR": "/tmp/tl"})
        assert settings.timeline_dir == Path("/tmp/tl")

    def test_timeline_dir_falls_back_to_repo_relative_default(self) -> None:
        assert WorkerSettings.from_env({}).timeline_dir == Path("data/timelines")


class TestWorkerCliPrecedence:
    def test_no_flags_and_no_env_matches_current_defaults(self) -> None:
        settings = settings_from_args(parse_args([]))
        baseline = WorkerSettings()
        assert settings.db_path == baseline.db_path
        assert settings.screenshot_dir == baseline.screenshot_dir
        assert settings.timeline_dir == baseline.timeline_dir
        assert settings.vault_root == baseline.vault_root
        assert settings.profile == baseline.profile
        assert settings.headless == baseline.headless
        assert settings.poll_ms == baseline.poll_ms
        assert settings.ocr_enabled == baseline.ocr_enabled
        assert settings.challenge_grace_s == baseline.challenge_grace_s
        assert settings.cdp_endpoint is None

    def test_env_applies_when_no_flag_given(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AUTO_TICKET_DB_PATH", "/data/a.db")
        monkeypatch.setenv("AUTO_TICKET_SCREENSHOT_DIR", "/data/shots")
        monkeypatch.setenv("AUTO_TICKET_TIMELINE_DIR", "/data/tl")
        monkeypatch.setenv("AUTO_TICKET_VAULT_ROOT", "/data/creds")
        monkeypatch.setenv("AUTO_TICKET_OCR_ENABLED", "0")

        settings = settings_from_args(parse_args([]))

        assert settings.db_path == Path("/data/a.db")
        assert settings.screenshot_dir == Path("/data/shots")
        assert settings.timeline_dir == Path("/data/tl")
        assert settings.vault_root == Path("/data/creds")
        assert settings.ocr_enabled is False

    def test_explicit_flag_beats_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTO_TICKET_DB_PATH", "/from/env.db")
        monkeypatch.setenv("AUTO_TICKET_TIMELINE_DIR", "/from/env/tl")

        settings = settings_from_args(
            parse_args(["--db", "/from/cli.db", "--timeline-dir", "/from/cli/tl"])
        )

        assert settings.db_path == Path("/from/cli.db")
        assert settings.timeline_dir == Path("/from/cli/tl")

    def test_vault_root_flag_is_available(self) -> None:
        settings = settings_from_args(parse_args(["--vault-root", "/v"]))
        assert settings.vault_root == Path("/v")

    def test_no_headless_flag_still_overrides(self) -> None:
        assert settings_from_args(parse_args(["--no-headless"])).headless is False


class TestApiCliPrecedence:
    def test_no_flags_and_no_env_matches_current_defaults(self) -> None:
        module = load_serve_api()
        settings = module.settings_from_args(module.parse_args([]))
        baseline = ApiSettings()
        assert settings.db_path == baseline.db_path
        assert settings.screenshot_dir == baseline.screenshot_dir
        assert settings.vault_root == baseline.vault_root
        assert settings.cors_origins == baseline.cors_origins

    def test_env_applies_when_no_flag_given(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AUTO_TICKET_DB_PATH", "/data/a.db")
        monkeypatch.setenv("AUTO_TICKET_VAULT_ROOT", "/data/creds")
        monkeypatch.setenv("AUTO_TICKET_CORS_ORIGINS", "http://127.0.0.1:3000")

        module = load_serve_api()
        settings = module.settings_from_args(module.parse_args([]))

        assert settings.db_path == Path("/data/a.db")
        assert settings.vault_root == Path("/data/creds")
        assert settings.cors_origins == ("http://127.0.0.1:3000",)

    def test_explicit_flag_beats_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTO_TICKET_DB_PATH", "/from/env.db")

        module = load_serve_api()
        settings = module.settings_from_args(module.parse_args(["--db", "/from/cli.db"]))

        assert settings.db_path == Path("/from/cli.db")


class TestBrowserProfileRoot:
    def test_default_is_unchanged_without_env(self) -> None:
        assert default_profile_root() == Path(".browser_profiles")
        assert BrowserProfile(name="live").user_data_dir == Path(
            ".browser_profiles/live"
        )

    def test_env_relocates_profile_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_BROWSER_PROFILE_ROOT, "/home/u/.auto-ticket/browser-profiles")

        assert default_profile_root() == Path("/home/u/.auto-ticket/browser-profiles")
        assert BrowserProfile(name="live").user_data_dir == Path(
            "/home/u/.auto-ticket/browser-profiles/live"
        )

    def test_explicit_user_data_dir_still_wins(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_BROWSER_PROFILE_ROOT, "/ignored")
        profile = BrowserProfile(name="live", user_data_dir=Path("/explicit"))
        assert profile.user_data_dir == Path("/explicit")
