#!/usr/bin/env python
"""轉 public 前的唯讀稽核。

repo 一旦公開，工作區的檔案與**整段 git 歷史**都是公開內容，而歷史無法事後補救。
本腳本因此分成三層：目前索引有沒有不該追蹤的路徑、歷史上有沒有「加進來又刪掉」的
同類路徑、以及全歷史的 patch 內容裡有沒有憑證形態的字串。

任一命中即 exit 1；這支腳本綠燈是人工把 repo 切成 public 的前置條件。

用法：
    uv run python scripts/audit_public_release.py
    uv run python scripts/audit_public_release.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 這些路徑只要出現在索引或歷史裡就是事故：營運資料、金鑰、本機設定與工具產物。
FORBIDDEN_PATH_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"^data/", "營運資料目錄"),
    (r"(^|/)\.vault_key$", "Vault 主金鑰"),
    (r"(^|/)\.env(\.local)?$", "本機環境設定"),
    (r"^task\.json$", "本機任務暫存"),
    (r"^out/", "工具輸出目錄"),
    (r"^graphify-out/", "知識圖譜輸出"),
    (r"^\.pi/", "規劃工作區"),
    (r"(^|/)credentials/", "憑證目錄"),
)

# patch 內容的憑證形態。逐條都刻意收窄，寧可讓稽核者多看兩行，也不要靠寬鬆 regex
# 製造大量假紅而讓人養成略過報告的習慣。
CONTENT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bFernet\(\s*[\"'][A-Za-z0-9_-]{43}=[\"']", "疑似硬編碼的 Fernet 金鑰"),
    (r"[\"'][A-Za-z0-9_-]{43}=[\"']\s*(?:#|$)", "疑似 base64url 44 字元金鑰字面值"),
    (r"\bBearer\s+[A-Za-z0-9._-]{16,}", "Bearer token"),
    (r"\b(?:gh[pousr]|github_pat)_[A-Za-z0-9_]{20,}", "GitHub token"),
    (r"\bnpm_[A-Za-z0-9]{30,}", "npm token"),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS access key id"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "私鑰"),
    (
        r"\b(?:Cookie|Set-Cookie)\s*[:=]\s*[\"']?[A-Za-z0-9._-]+=[A-Za-z0-9%._-]{16,}",
        "cookie 值",
    ),
    (r"\b[A-Za-z0-9._%+-]+@(?!example\.|localhost)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "個人 email"),
    (r"/(?:Users|home)/(?!runner\b|user\b)[A-Za-z0-9._-]+/", "絕對家目錄路徑"),
)

# 通用佔位符與教學用字串不是洩漏；它們在 patch 裡出現的頻率遠高於真金鑰。
CONTENT_ALLOW = re.compile(
    r"noreply@|no-reply@|example\.(?:com|org)|@anthropic\.com|"
    r"users\.noreply\.github\.com|\byour[-_.]?email\b|xxx+|\.\.\.|<[a-z-]+>",
    re.IGNORECASE,
)

# 測試與樣板檔裡的假 email、假家目錄是刻意寫的資料，不是洩漏。把它們一律當雜訊擋掉，
# 否則報告會被幾十條假紅淹沒，真的那一條反而沒人看。
NOISE_PATH_RE = re.compile(
    r"(^|/)_{0,2}tests?_{0,2}/|(^|/)test_|\.(?:test|spec)\.[jt]sx?$|"
    r"(^|/)conftest\.py$|(^|/)fixtures?/|"
    r"\.(?:example|sample|template)(?:\.[a-z0-9]+)?$",
)


@dataclass
class Finding:
    gate: str
    detail: str
    where: str

    def as_dict(self) -> dict[str, str]:
        return {"gate": self.gate, "detail": self.detail, "where": self.where}


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    scanned_commits: int = 0
    tracked_files: int = 0

    def add(self, gate: str, detail: str, where: str) -> None:
        self.findings.append(Finding(gate, detail, where))


def git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失敗: {proc.stderr.strip()}")
    return proc.stdout


def match_forbidden(path: str) -> str | None:
    for pattern, label in FORBIDDEN_PATH_PATTERNS:
        if re.search(pattern, path):
            return label
    return None


def audit_tracked_files(report: Report) -> None:
    """目前索引：公開後第一眼看得到的內容。"""
    files = [line for line in git("ls-files").splitlines() if line]
    report.tracked_files = len(files)
    for path in files:
        label = match_forbidden(path)
        if label is not None:
            report.add("TRACKED", f"{label}被追蹤", path)


def audit_history_paths(report: Report) -> None:
    """歷史新增過的路徑：後來刪掉也還在歷史裡，公開等於一起公開。"""
    out = git("log", "--all", "--diff-filter=A", "--name-only", "--format=%x00%H")
    commit = "?"
    for line in out.splitlines():
        if line.startswith("\x00"):
            commit = line[1:].strip()
            continue
        path = line.strip()
        if not path:
            continue
        label = match_forbidden(path)
        if label is not None:
            report.add("HISTORY", f"{label}曾被加入版控", f"{commit[:12]} {path}")


def audit_patch_content(report: Report) -> None:
    """全歷史 patch 內容：只看實際被加入的行，commit metadata 不算內容。"""
    out = git(
        "log",
        "--all",
        "-p",
        "--no-color",
        "--no-textconv",
        "--format=%x00%H",
    )
    commit = "?"
    path = "?"
    noisy = False
    seen: set[tuple[str, str]] = set()
    for line in out.splitlines():
        if line.startswith("\x00"):
            commit = line[1:].strip()
            report.scanned_commits += 1
            continue
        if line.startswith("+++ "):
            path = line[4:].removeprefix("b/").strip()
            noisy = NOISE_PATH_RE.search(path) is not None
            continue
        if noisy or not line.startswith("+"):
            continue
        body = line[1:]
        if CONTENT_ALLOW.search(body):
            continue
        for pattern, label in CONTENT_PATTERNS:
            hit = re.search(pattern, body)
            if hit is None:
                continue
            key = (label, hit.group(0)[:64])
            if key in seen:
                continue
            seen.add(key)
            report.add("CONTENT", label, f"{commit[:12]} {path}: {hit.group(0)[:64]}")


def render_text(report: Report) -> str:
    lines = [
        "轉 public 稽核報告",
        f"  追蹤中檔案：{report.tracked_files}",
        f"  掃描 commit：{report.scanned_commits}",
        f"  findings：{len(report.findings)}",
    ]
    for finding in report.findings:
        lines.append(f"  [{finding.gate}] {finding.detail} — {finding.where}")
    if not report.findings:
        lines.append("  ✓ 0 finding，可進行人工可見性切換")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="以 JSON 輸出報告")
    args = parser.parse_args(argv)

    report = Report()
    audit_tracked_files(report)
    audit_history_paths(report)
    audit_patch_content(report)

    if args.json:
        payload = {
            "trackedFiles": report.tracked_files,
            "scannedCommits": report.scanned_commits,
            "findings": [f.as_dict() for f in report.findings],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_text(report))

    return 1 if report.findings else 0


if __name__ == "__main__":
    sys.exit(main())
