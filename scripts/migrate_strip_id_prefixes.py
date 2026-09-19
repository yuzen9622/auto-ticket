#!/usr/bin/env python
"""一次性遷移：剝除 SQLite 與 timeline 檔案裡的 ID 前綴（task_ / exp_ / job_ / ev_ / tt_）。

預設為 dry-run（純唯讀，不產生任何副作用）；`--apply` 才進入寫入流程。
寫入流程在動 DB 之前先 checkpoint、備份、預檢碰撞與 timeline 更名衝突，
再於單一 `BEGIN IMMEDIATE` 交易內改寫全部白名單欄位。

用法：
    uv run --offline python scripts/migrate_strip_id_prefixes.py
    uv run --offline python scripts/migrate_strip_id_prefixes.py --apply
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit_db_id_prefixes import (  # pyright: ignore[reportMissingImports]
    ID_PREFIX_RE,
    quote_ident,
    strip_prefixes,
    user_tables,
)

DEFAULT_DB = "data/auto-ticket.db"
DEFAULT_TIMELINES = "data/timelines"

# 檔名專用：共用的 ID_PREFIX_RE 要求前綴右界非識別字元（那是它不會誤殺 `task_cancelled`
# 的原因），而 `exp_<hex>_timeline.json` 的 hex 後面緊接的就是 `_`，因此套不上。
# 檔名是封閉形態，可以整個錨定後再拿掉前綴，不需要也不得放寬共用正則。
TIMELINE_NAME_RE = re.compile(
    r"^(?:task_|exp_|job_|ev_|tt_)([0-9a-f]{8,}_timeline\.json)$"
)

# 純量 ID 欄位：逐值剝除。不假設一個欄位只存一種前綴
# （broker_outbox.task_id 有數列存 job_*，experiment_id 有數十列存 task_*）。
SCALAR_ID_COLUMNS = (
    ("events", "id"),
    ("ticket_types", "id"),
    ("ticket_types", "event_id"),
    ("purchase_tasks", "id"),
    ("purchase_tasks", "event_id"),
    ("experiments", "id"),
    ("experiments", "task_id"),
    ("experiment_events", "experiment_id"),
    ("experiment_metrics", "experiment_id"),
    ("broker_jobs", "id"),
    ("broker_jobs", "task_id"),
    ("control_signals", "task_id"),
    ("broker_outbox", "task_id"),
    ("broker_outbox", "experiment_id"),
)

# JSON/TEXT 欄位：整段字串套 ID_PREFIX_RE.sub。
JSON_COLUMNS = (
    ("events", "raw_metadata"),
    ("purchase_tasks", "spec"),
    ("experiments", "result_summary"),
    ("experiment_events", "details"),
    ("broker_jobs", "payload"),
    ("broker_jobs", "result"),
    ("broker_jobs", "error"),
    ("control_signals", "payload"),
    ("broker_outbox", "payload"),
)

# 明確排除（同形字串陷阱）：experiment_events.action 的 task_cancelled、
# broker_outbox.type 的 TASK_LOG、ticket_types.raw_id 的平台原始 ID。
EXCLUDED_COLUMNS = (
    ("experiment_events", "action"),
    ("broker_outbox", "type"),
    ("ticket_types", "raw_id"),
)

# 剝除後必須仍然唯一的主鍵欄位。
PK_COLUMNS = (
    ("events", "id"),
    ("ticket_types", "id"),
    ("purchase_tasks", "id"),
    ("experiments", "id"),
    ("broker_jobs", "id"),
)


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in user_tables(conn):
        ident = quote_ident(table)
        counts[table] = conn.execute(f"select count(*) from {ident}").fetchone()[0]
    return counts


def fk_check(conn: sqlite3.Connection) -> list[tuple]:
    return sorted(tuple(r) for r in conn.execute("pragma foreign_key_check"))


def scalar_value_map(
    conn: sqlite3.Connection, table: str, col: str
) -> tuple[dict[str, str], int]:
    """回傳（需改寫的 舊值 -> 新值對照，受影響列數）。"""
    t, c = quote_ident(table), quote_ident(col)
    mapping: dict[str, str] = {}
    affected = 0
    for (value,) in conn.execute(f"select {c} from {t} where {c} is not null"):
        if not isinstance(value, str):
            continue
        new = strip_prefixes(value)
        if new != value:
            mapping[value] = new
            affected += 1
    return mapping, affected


def json_row_map(conn: sqlite3.Connection, table: str, col: str) -> dict[int, str]:
    """回傳需要改寫的 rowid -> 新字串 對照。"""
    t, c = quote_ident(table), quote_ident(col)
    mapping: dict[int, str] = {}
    for rowid, value in conn.execute(
        f"select rowid, {c} from {t} where {c} is not null"
    ):
        if not isinstance(value, str):
            continue
        new = ID_PREFIX_RE.sub("", value)
        if new != value:
            mapping[rowid] = new
    return mapping


def collision_check(conn: sqlite3.Connection, table: str, col: str) -> str | None:
    t, c = quote_ident(table), quote_ident(col)
    values = [
        r[0] for r in conn.execute(f"select {c} from {t} where {c} is not null")
    ]
    stripped = [strip_prefixes(v) if isinstance(v, str) else v for v in values]
    if len(set(stripped)) != len(stripped):
        return (
            f"{table}.{col}: 剝除後 {len(stripped)} 列僅剩 "
            f"{len(set(stripped))} 個相異值（PK 會碰撞）"
        )
    return None


def build_timeline_plan(timelines_dir: Path) -> list[tuple[Path, Path]]:
    """更名預檢：任何衝突都必須在動 DB 之前就中止。"""
    if not timelines_dir.is_dir():
        print(f"INFO: 找不到 timeline 目錄，略過更名：{timelines_dir}")
        return []
    plan: list[tuple[Path, Path]] = []
    targets: dict[Path, Path] = {}
    for src in sorted(timelines_dir.glob("*.json")):
        renamed = TIMELINE_NAME_RE.sub(r"\1", src.name)
        dst = src.with_name(renamed)
        if dst == src:
            continue
        if dst.exists():
            raise SystemExit(f"ABORT: 目標檔已存在，更名會覆蓋資料：{dst}")
        if dst in targets:
            raise SystemExit(
                f"ABORT: 兩個來源映射到同一目標：{targets[dst]} 與 {src} -> {dst}"
            )
        targets[dst] = src
        plan.append((src, dst))
    if plan:
        try:
            can_write_dir = os.access(timelines_dir, os.W_OK)
        except OSError:
            can_write_dir = False
        if not can_write_dir:
            raise SystemExit(f"ABORT: 無寫入權限：{timelines_dir}")
    for src, _ in plan:
        try:
            can_write_src = os.access(src, os.W_OK)
        except OSError:
            can_write_src = False
        if not can_write_src:
            raise SystemExit(f"ABORT: 無寫入權限：{src}")
    return plan


def timeline_content_targets(timelines_dir: Path) -> list[Path]:
    out: list[Path] = []
    for path in sorted(timelines_dir.glob("*.json")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if ID_PREFIX_RE.sub("", text) != text:
            out.append(path)
    return out


def run_dry_run(db_path: Path, timelines_dir: Path) -> int:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        print(f"DB: {db_path}")
        print("--- 純量 ID 欄位（預期改寫列數）")
        for table, col in SCALAR_ID_COLUMNS:
            mapping, rows = scalar_value_map(conn, table, col)
            print(f"  {table}.{col}: {rows} 列 / {len(mapping)} 個相異值")
        print("--- JSON/TEXT 欄位（預期改寫列數）")
        for table, col in JSON_COLUMNS:
            print(f"  {table}.{col}: {len(json_row_map(conn, table, col))} 列")
        print("--- 明確排除的欄位")
        for table, col in EXCLUDED_COLUMNS:
            print(f"  {table}.{col}")
        print("--- PK 碰撞預檢")
        problems = [
            msg
            for table, col in PK_COLUMNS
            if (msg := collision_check(conn, table, col)) is not None
        ]
        for msg in problems:
            print(f"  COLLISION: {msg}")
        if not problems:
            print(f"  OK: {len(PK_COLUMNS)} 個主鍵欄位剝除後仍唯一")
        baseline = fk_check(conn)
        print(f"--- foreign_key_check 基準：{len(baseline)} 筆既有違規")
        print(f"--- 各表筆數：{table_counts(conn)}")
    finally:
        conn.close()

    plan = build_timeline_plan(timelines_dir)
    print(f"--- timeline 更名（{len(plan)} 檔）")
    for src, dst in plan[:10]:
        print(f"  {src.name} -> {dst.name}")
    if len(plan) > 10:
        print(f"  ...（其餘 {len(plan) - 10} 檔略）")
    print(f"--- timeline 內容需改寫：{len(timeline_content_targets(timelines_dir))} 檔")

    if problems:
        print("ABORT: PK 碰撞預檢未通過，--apply 會被拒絕", file=sys.stderr)
        return 1
    print("DRY-RUN: no changes written. rerun with --apply to execute.")
    return 0


def backup_database(conn: sqlite3.Connection, bak_path: Path) -> None:
    if bak_path.exists():
        bak_path.unlink()
    dest = sqlite3.connect(bak_path)
    try:
        conn.backup(dest)
    finally:
        dest.close()


def rewrite_tables(session: sqlite3.Connection) -> dict[str, int]:
    changed: dict[str, int] = {}
    for table, col in SCALAR_ID_COLUMNS:
        mapping, _affected = scalar_value_map(session, table, col)
        t, c = quote_ident(table), quote_ident(col)
        rows = 0
        for old, new in mapping.items():
            cur = session.execute(
                f"update {t} set {c} = ? where {c} = ?",  # noqa: S608
                (new, old),
            )
            rows += cur.rowcount
        changed[f"{table}.{col}"] = rows
    for table, col in JSON_COLUMNS:
        mapping_json = json_row_map(session, table, col)
        t, c = quote_ident(table), quote_ident(col)
        for rowid, new in mapping_json.items():
            session.execute(
                f"update {t} set {c} = ? where rowid = ?",  # noqa: S608
                (new, rowid),
            )
        changed[f"{table}.{col}"] = len(mapping_json)
    return changed


def run_apply(db_path: Path, timelines_dir: Path, overwrite_backup: bool) -> int:
    bak_path = db_path.with_name(db_path.name + ".bak")
    timelines_bak = timelines_dir.with_name(timelines_dir.name + ".bak")

    # autocommit：PRAGMA foreign_keys 在交易中是 no-op 且不報錯，交易邊界必須由本腳本掌控。
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        busy, _log_frames, _checkpointed = conn.execute(
            "pragma wal_checkpoint(truncate)"
        ).fetchone()
        if busy != 0:
            raise SystemExit(
                "ABORT: wal_checkpoint busy=1，仍有其他連線持有 DB。"
                "請先停止 API / worker 後重試，切勿在服務運行中遷移。"
            )

        if bak_path.exists() and not overwrite_backup:
            raise SystemExit(
                f"ABORT: 備份 {bak_path} 已存在。"
                "若確定要覆蓋（將永久丟失上一次的遷移前快照），請加 --overwrite-backup。"
            )
        if timelines_bak.exists() and not overwrite_backup:
            raise SystemExit(
                f"ABORT: 備份目錄 {timelines_bak} 已存在。加 --overwrite-backup 以覆蓋。"
            )

        backup_database(conn, bak_path)
        if timelines_bak.exists():
            shutil.rmtree(timelines_bak)
        shutil.copytree(timelines_dir, timelines_bak)
        print(f"BACKUP: {bak_path} / {timelines_bak}")

        fk_baseline = fk_check(conn)
        counts_before = table_counts(conn)
        print(f"BASELINE: foreign_key_check {len(fk_baseline)} 筆；{counts_before}")

        problems = [
            msg
            for table, col in PK_COLUMNS
            if (msg := collision_check(conn, table, col)) is not None
        ]
        if problems:
            raise SystemExit("ABORT: PK 碰撞預檢失敗：" + "；".join(problems))

        plan = build_timeline_plan(timelines_dir)

        conn.execute("pragma foreign_keys = off")
        fk_state = conn.execute("pragma foreign_keys").fetchone()[0]
        assert fk_state == 0, (
            f"ABORT: PRAGMA foreign_keys 未生效（回傳 {fk_state}）。"
            "通常代表連線正處於交易中；請確認使用 isolation_level=None 建立連線。"
        )

        conn.execute("begin immediate")
        try:
            changed = rewrite_tables(conn)
            conn.execute("commit")
        except BaseException:
            conn.execute("rollback")
            raise
        finally:
            conn.execute("pragma foreign_keys = on")

        fk_after = fk_check(conn)
        if fk_after != fk_baseline:
            raise SystemExit(
                f"ABORT: foreign_key_check 與基準不符（{len(fk_baseline)} -> "
                f"{len(fk_after)}）。請依下方指引還原。"
            )
        print(f"FK OK: {len(fk_after)} 筆，與遷移前基準一致")

        counts_after = table_counts(conn)
        mismatched = [
            f"{t} {counts_before[t]} -> {counts_after.get(t)}"
            for t in counts_before
            if counts_before[t] != counts_after.get(t)
        ]
        if mismatched or set(counts_before) != set(counts_after):
            for line in mismatched:
                print(f"COUNT MISMATCH: {line}", file=sys.stderr)
            raise SystemExit("ABORT: 遷移前後筆數不一致")
        print(f"COUNTS OK: {len(counts_after)} tables unchanged")
    finally:
        conn.close()

    renamed = 0
    rewritten = 0
    for path in sorted(timelines_dir.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        new_text = ID_PREFIX_RE.sub("", text)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            rewritten += 1
    for src, dst in plan:
        src.rename(dst)
        renamed += 1

    for key, rows in changed.items():
        print(f"REWROTE {key}: {rows} 列")
    print(f"TIMELINES: {rewritten} 檔內容改寫、{renamed} 檔更名")
    print("MIGRATION DONE")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--overwrite-backup", action="store_true")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--timelines", default=DEFAULT_TIMELINES)
    args = parser.parse_args()

    db_path = Path(args.db)
    timelines_dir = Path(args.timelines)
    if not db_path.exists():
        print(f"ABORT: 找不到資料庫 {db_path}", file=sys.stderr)
        return 2

    try:
        if args.apply:
            return run_apply(db_path, timelines_dir, args.overwrite_backup)
        return run_dry_run(db_path, timelines_dir)
    except BaseException as exc:  # noqa: BLE001 - 失敗一律要印出回滾指引
        print(f"MIGRATION FAILED: {exc!r}", file=sys.stderr)
        print(
            "回滾步驟（請先確認 API / worker 已停止）：\n"
            f"  1. rm -f {db_path} {db_path}-wal {db_path}-shm\n"
            f"  2. cp {db_path}.bak {db_path}\n"
            f"  3. rm -rf {timelines_dir} && mv {timelines_dir}.bak {timelines_dir}\n"
            "  4. 以 audit 腳本確認還原結果：\n"
            f"     uv run --offline python scripts/audit_db_id_prefixes.py {db_path}\n"
            "     （還原後應 exit 1，因為備份保有舊前綴資料）",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
