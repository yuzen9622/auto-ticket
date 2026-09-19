#!/usr/bin/env python
"""稽核 SQLite 內是否殘留帶前綴的 ID（task_ / exp_ / job_ / ev_ / tt_）。

全表全欄位掃描而非白名單：白名單會隨 schema 演進而失準，稽核要的是「沒有角落漏掉」。
以 `mode=ro` 開啟，由 SQLite 自身保證唯讀。

用法：`uv run --offline python scripts/audit_db_id_prefixes.py [db_path]`
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = "data/auto-ticket.db"

# 前綴後必須緊接 >=8 個小寫 hex，且左右界皆非識別字元：
# 藉此放過 `task_cancelled`（含非 hex 字元）、`TASK_LOG`（大寫）與 `subtask_<hex>`（左界黏著）。
ID_PREFIX_RE = re.compile(
    r"(?<![0-9A-Za-z_])(?:task_|exp_|job_|ev_|tt_)(?=[0-9a-f]{8,}(?![0-9A-Za-z_]))"
)


def strip_prefixes(s: str) -> str:
    return ID_PREFIX_RE.sub("", s)


IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def quote_ident(name: str) -> str:
    """SQLite 不接受參數化的識別子，因此只放行白名單字元後再加雙引號。"""
    if IDENT_RE.fullmatch(name) is None:
        raise ValueError(f"refusing unsafe sqlite identifier: {name!r}")
    return f'"{name}"'


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def user_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "select name from sqlite_master where type='table' "
        "and name not like 'sqlite_%' order by name"
    ).fetchall()
    return [r[0] for r in rows]


def scan(session: sqlite3.Connection) -> list[tuple[str, str, int, str]]:
    """回傳 [(表, 欄位, 命中列數, 樣本)]。"""
    findings: list[tuple[str, str, int, str]] = []
    for table in user_tables(session):
        ident = quote_ident(table)
        columns = [
            r[1] for r in session.execute(f"pragma table_info({ident})", ())
        ]
        if not columns:
            continue
        counts: dict[str, int] = dict.fromkeys(columns, 0)
        samples: dict[str, str] = {}
        for row in session.execute(f"select * from {ident}", ()):
            for col, value in zip(columns, row, strict=True):
                if not isinstance(value, str) or ID_PREFIX_RE.search(value) is None:
                    continue
                counts[col] += 1
                samples.setdefault(col, value[:120])
        for col in columns:
            if counts[col]:
                findings.append((table, col, counts[col], samples[col]))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db", nargs="?", default=DEFAULT_DB)
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ABORT: 找不到資料庫 {db_path}", file=sys.stderr)
        return 2

    conn = connect_readonly(db_path)
    try:
        findings = scan(conn)
    finally:
        conn.close()

    if not findings:
        print(f"OK: no prefixed ids in {db_path}")
        return 0

    for table, col, count, sample in findings:
        print(f"{table}.{col}: {count} 列 命中 樣本={sample!r}")
    print(f"FAILED: {len(findings)} 個欄位仍有帶前綴的 ID", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
