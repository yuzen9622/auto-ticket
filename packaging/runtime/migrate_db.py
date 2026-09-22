#!/usr/bin/env python3
"""用 SQLite 線上備份 API 把來源資料庫複製成單一自洽檔。

不能直接複製 `.db`：WAL 模式下未 checkpoint 的交易還躺在 `-wal` 裡，只搬主檔會
拿到一個少了最近幾筆寫入、甚至頁面不一致的資料庫。`Connection.backup()` 會在
複製過程持鎖並把 WAL 內容併進去，產出的單檔不需要再帶 `-wal`／`-shm`。

來源**只以唯讀方式開啟**，整支腳本不對來源做任何寫入。

用法（由 CLI 以 runtime 內的 Python 呼叫）：
    python migrate_db.py --source <src.db> --dest <dst.db>
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def _force_utf8_output() -> None:
    """Windows 主控台預設是 cp1252，編不了中文，一行 log 就能讓建置整個倒下。

    `PYTHONUTF8` 得在直譯器啟動前就設好才有用，在 `__main__` 裡設已經太遲，
    所以直接改串流本身。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


_force_utf8_output()


def backup(source: Path, dest: Path) -> dict[str, object]:
    if not source.is_file():
        raise FileNotFoundError(f"來源資料庫不存在: {source}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise FileExistsError(f"目的地已存在，拒絕覆寫: {dest}")

    # `mode=ro` 讓 SQLite 自己保證不會寫來源；immutable 不能用，因為來源可能有 WAL。
    src_uri = f"file:{source.as_posix()}?mode=ro"
    src = sqlite3.connect(src_uri, uri=True)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
            dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            dst.close()
    finally:
        src.close()

    if integrity != "ok":
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"備份後的完整性檢查失敗: {integrity}")

    return {
        "source": str(source),
        "dest": str(dest),
        "sourceBytes": source.stat().st_size,
        "destBytes": dest.stat().st_size,
        "integrity": integrity,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--dest", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        result = backup(args.source, args.dest)
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
