# 既有資料遷移

## 一句話

**只複製，永遠不刪、不搬、不改寫來源。**

第一次執行 `autix start`（或任何時候顯式跑 `autix migrate`）時，
既有的 repo `data/` 會被複製到 `~/.auto-ticket/data/`。原本的目錄一個位元組都不會動。

代價是磁碟翻倍——截圖通常佔絕大部分。這是刻意的：搶票資料是不可重建的營運紀錄，
省磁碟不值得拿它冒險。`--dry-run` 可以先看會複製多少。

## 來源怎麼找

依序：

1. `--from <path>` 顯式指定
2. 從目前工作目錄往上找，直到同時看到 `pyproject.toml`（`name = "auto-ticket"`）
   與 `data/auto-ticket.db` 的那一層
3. 都找不到——**這不是錯誤**。建立空的 `~/.auto-ticket/data/`，寫入
   `{"source": null, "mode": "fresh"}`，正常啟動

## 資料庫為什麼不是直接複製檔案

SQLite 在 WAL 模式下，最近的交易還躺在 `-wal` 裡沒有 checkpoint。直接複製 `.db`
會拿到一個少了最近幾筆寫入、甚至頁面不一致的資料庫，而且它**開得起來**——你要到
很久以後才會發現少了東西。

改用 `sqlite3.Connection.backup()` 線上備份 API（`packaging/runtime/migrate_db.py`，
由 CLI 以 runtime 內的 Python 呼叫）。它在複製過程持鎖，把 WAL 內容併進去，產出一份
單檔自洽的 `auto-ticket.db`，之後再跑一次 `integrity_check`。

`-wal`、`-shm`、`.bak*` 這些衍生檔**不複製**——它們的內容已經在備份裡了。

## 前置檢查

來源 DB 若正被使用（8000 埠還活著，或 `backup()` 拿不到鎖），遷移會**中止且完全
不寫入**，並提示先把正在跑的 auto-ticket 關掉。

## 其他檔案

`credentials/`（含 `.vault_key`，POSIX 權限保持 `0600`）、`screenshots/`、`timelines/`
逐檔複製並保留 mtime。

日誌**絕不記錄 `.vault_key` 的內容**，只記檔名與位元組數。

## 冪等

完成後寫入 `~/.auto-ticket/data/.migrated.json`：

```json
{
  "schema": 1,
  "completedAt": "2026-09-22T10:00:00Z",
  "source": "/abs/path/to/repo",
  "mode": "copied",
  "cliVersion": "1.0.0",
  "files": 1234,
  "bytes": 195000000,
  "dbBackupOk": true
}
```

這個檔存在就直接 skip。再跑一萬次也不會動到資料。

舊版 CLI 遇到未知的 `schema` 值時視為「已遷移」並略過，不會重跑——降級不會毀資料。

## 目標已經有資料了怎麼辦

標記檔不存在、但 `~/.auto-ticket/data/auto-ticket.db` 已經在了：

**預設中止**，結束碼 `6`，訊息列出兩邊的路徑、大小與 mtime，並給三個選項：

| 選項 | 行為 |
| --- | --- |
| `--skip-migration` | 直接用現有的目標資料，不複製 |
| `--from <path> --merge-missing` | 只補目標**缺少**的檔案，絕不覆蓋任何已存在的檔 |
| 手動處理 | 自己看過兩邊再決定 |

**任何模式下都不覆蓋目標既有檔案。**

## 回滾

整批先寫進 `~/.auto-ticket/data.incoming-<timestamp>/`，全部成功之後才原子
`rename` 成 `data/`。`--merge-missing` 模式則逐檔 `copy → rename`，且帶
`COPYFILE_EXCL`（目標存在就失敗，不會靜默覆蓋）。

任一步失敗 → 刪掉 `data.incoming-*`，目標維持原狀，**來源從頭到尾沒被碰過**。

要重來，刪掉整個 `~/.auto-ticket/data/` 再跑一次 `autix migrate` 就好。

## 先看看會發生什麼

```bash
autix migrate --dry-run
```

只列印計畫：來源、目標、檔案數、位元組數、會略過哪些衍生檔。零寫入。
