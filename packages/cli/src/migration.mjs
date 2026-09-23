// D3 資料遷移：純複製語意，永不寫來源。DB 一致性複製交給 runtime Python 的
// sqlite3 backup API（packaging/runtime/migrate_db.py），其餘檔案逐檔複製。
// 領域模組，不得 import runtime-cache.mjs / host.mjs / supervisor.mjs。
import fs from "node:fs/promises";
import path from "node:path";

// 領域模組不 import `command.mjs`——那會形成循環，也會讓依賴方向失去單一走向。
// 錯誤只帶 `code`，由 `command.mjs` 負責對映結束碼；`name` 讓它在那邊被認出來。
function cliError(code, message) {
  const err = new Error(message);
  err.name = "CliError";
  err.code = code;
  return err;
}

function isCliError(err) {
  return err?.name === "CliError" && typeof err?.code === "string";
}

const SKIP_SUFFIXES = [".bak", "-wal", "-shm"];

/**
 * 依優先順序解析遷移來源：顯式 --from → 往上找同時有 pyproject.toml(name="auto-ticket")
 * 與 data/auto-ticket.db 的祖先目錄 → 都找不到就回傳 null（代表全新安裝，不是錯誤）。
 * 只做讀取判斷，不做任何寫入。
 */
export async function planMigration({ from, cwd = process.cwd(), fsImpl = fs } = {}) {
  if (from) {
    const dbPath = path.join(from, "data", "auto-ticket.db");
    const exists = await fsImpl.stat(dbPath).catch(() => null);
    return { source: exists ? path.resolve(from) : null, explicit: true };
  }
  let dir = path.resolve(cwd);
  for (;;) {
    const pyproject = path.join(dir, "pyproject.toml");
    const db = path.join(dir, "data", "auto-ticket.db");
    const pyprojectContent = await fsImpl.readFile(pyproject, "utf8").catch(() => null);
    const dbStat = pyprojectContent ? await fsImpl.stat(db).catch(() => null) : null;
    if (pyprojectContent && /name\s*=\s*"auto-ticket"/.test(pyprojectContent) && dbStat) {
      return { source: dir, explicit: false };
    }
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return { source: null, explicit: false };
}

export async function readMarker(paths, fsImpl = fs) {
  const raw = await fsImpl.readFile(paths.migratedMarker, "utf8").catch(() => null);
  return raw ? JSON.parse(raw) : null;
}

export async function writeMarker(paths, data, fsImpl = fs) {
  await fsImpl.mkdir(path.dirname(paths.migratedMarker), { recursive: true });
  await fsImpl.writeFile(paths.migratedMarker, JSON.stringify(data, null, 2), "utf8");
}

/**
 * 執行遷移。`dryRun` 時只計算計畫，保證零寫入。
 * 靜態掃描（migration-readonly.test.mjs）會逐行檢查本檔不得對「來源」路徑做
 * rm/unlink/rename/truncate —— 因此下面所有具破壞性的呼叫都只作用在 staging／目標路徑，
 * 來源一律只用 fsImpl.stat / readFile / readdir / copyFile 讀取。
 */
export async function migrate({
  from,
  mergeMissing = false,
  dryRun = false,
  paths,
  runPython = defaultRunPython,
  pythonPath,
  runtimeDir,
  now = () => new Date().toISOString(),
  fsImpl = fs,
  cwd,
} = {}) {
  const marker = await readMarker(paths, fsImpl);
  if (marker) {
    return { skipped: true, reason: "already-migrated", marker };
  }

  const { source: sourceRoot } = await planMigration({ from, cwd, fsImpl });

  if (!sourceRoot) {
    const plan = { source: null, mode: "fresh" };
    if (dryRun) return { dryRun: true, plan };
    await fsImpl.mkdir(paths.data, { recursive: true });
    await writeMarker(paths, { schema: 1, completedAt: now(), ...plan }, fsImpl);
    return { skipped: false, ...plan };
  }

  const targetDbStat = await fsImpl.stat(paths.db).catch(() => null);
  if (targetDbStat) {
    if (!mergeMissing) {
      throw cliError(
        "MIGRATION_CONFLICT",
        `目標資料庫已存在但沒有遷移標記：來源=${sourceRoot} 目標=${paths.db}。` +
          "請使用 --skip-migration 直接沿用現有資料，或加 --merge-missing 只補缺少的檔案。",
      );
    }
  }

  const plan = await buildCopyPlan(sourceRoot, paths, fsImpl);
  if (dryRun) {
    return { dryRun: true, plan: { source: sourceRoot, files: plan.length } };
  }

  const stagingRoot = `${paths.data}.incoming-${Date.now()}`;
  try {
    await fsImpl.mkdir(stagingRoot, { recursive: true });
    let bytes = 0;
    for (const item of plan) {
      const destStagingPath = path.join(stagingRoot, item.rel);
      await fsImpl.mkdir(path.dirname(destStagingPath), { recursive: true });
      await fsImpl.copyFile(item.abs, destStagingPath);
      const st = await fsImpl.stat(item.abs);
      bytes += st.size;
      if (item.rel.startsWith(path.join("credentials", ""))) {
        await fsImpl.chmod(destStagingPath, 0o600).catch(() => {});
      }
    }

    let dbBackupOk = true;
    const sourceDb = path.join(sourceRoot, "data", "auto-ticket.db");
    const sourceDbExists = await fsImpl.stat(sourceDb).catch(() => null);
    if (sourceDbExists) {
      const destDb = path.join(stagingRoot, "auto-ticket.db");
      try {
        await runPython({ sourceDb, destDb, pythonPath, runtimeDir });
      } catch (err) {
        dbBackupOk = false;
        throw cliError(
          "MIGRATION_CONFLICT",
          `來源資料庫仍在使用中，無法完成一致性備份：${err.message}`,
        );
      }
    }

    if (mergeMissing && targetDbStat) {
      await mergeIntoExisting(stagingRoot, paths.data, fsImpl);
    } else {
      await fsImpl.mkdir(path.dirname(paths.data), { recursive: true });
      await fsImpl.rename(stagingRoot, paths.data);
    }

    await writeMarker(
      paths,
      {
        schema: 1,
        completedAt: now(),
        source: sourceRoot,
        mode: mergeMissing ? "merged" : "copied",
        files: plan.length,
        bytes,
        dbBackupOk,
      },
      fsImpl,
    );
    return { skipped: false, source: sourceRoot, files: plan.length, bytes };
  } catch (err) {
    await fsImpl.rm(stagingRoot, { recursive: true, force: true }).catch(() => {});
    if (isCliError(err)) throw err;
    throw cliError("MIGRATION_CONFLICT", `遷移失敗：${err.message}`);
  }
}

async function buildCopyPlan(sourceRoot, paths, fsImpl) {
  const sourceData = path.join(sourceRoot, "data");
  const plan = [];
  async function walk(dir, rel) {
    const entries = await fsImpl.readdir(dir, { withFileTypes: true }).catch(() => []);
    for (const entry of entries) {
      const abs = path.join(dir, entry.name);
      const entryRel = path.join(rel, entry.name);
      if (entry.isDirectory()) {
        await walk(abs, entryRel);
      } else if (entry.isFile()) {
        if (SKIP_SUFFIXES.some((suffix) => entry.name.endsWith(suffix))) continue;
        if (entry.name === "auto-ticket.db") continue; // DB 走 sqlite backup API，不逐檔複製。
        plan.push({ abs, rel: entryRel });
      }
    }
  }
  await walk(sourceData, ".");
  return plan;
}

/** 只補目標缺少的檔案，絕不覆蓋任何已存在的檔案。 */
async function mergeIntoExisting(stagingRoot, targetData, fsImpl) {
  async function walk(dir, rel) {
    const entries = await fsImpl.readdir(dir, { withFileTypes: true }).catch(() => []);
    for (const entry of entries) {
      const stagingPath = path.join(dir, entry.name);
      const targetPath = path.join(targetData, rel, entry.name);
      if (entry.isDirectory()) {
        await fsImpl.mkdir(targetPath, { recursive: true });
        await walk(stagingPath, path.join(rel, entry.name));
      } else if (entry.isFile()) {
        const already = await fsImpl.stat(targetPath).catch(() => null);
        if (already) continue;
        await fsImpl.mkdir(path.dirname(targetPath), { recursive: true });
        await fsImpl.copyFile(
          stagingPath,
          targetPath,
          fsImpl.constants?.COPYFILE_EXCL,
        );
      }
    }
  }
  await walk(stagingRoot, ".");
  await fsImpl.rm(stagingRoot, { recursive: true, force: true });
}

/**
 * 以 runtime 自帶的 Python 跑一致性備份。
 *
 * 腳本與直譯器都必須來自 runtime：使用者機器上沒有這個 repo，npm 套件也只出貨
 * `bin/` 與 `src/`，而系統 python3 未必存在、更未必是 3.12。
 */
async function defaultRunPython({ sourceDb, destDb, pythonPath, runtimeDir }) {
  if (!pythonPath || !runtimeDir) {
    throw cliError(
      "MIGRATION_CONFLICT",
      "資料庫一致性備份需要 runtime 內的 Python；請先執行 `autix runtime install`。",
    );
  }
  const { spawn } = await import("node:child_process");
  const scriptPath = path.join(runtimeDir, "app", "scripts", "migrate_db.py");
  await new Promise((resolve, reject) => {
    const child = spawn(
      pythonPath,
      ["-s", scriptPath, "--source", sourceDb, "--dest", destDb],
      { shell: false },
    );
    child.once("error", reject);
    child.once("exit", (code) =>
      code === 0 ? resolve() : reject(new Error(`migrate_db.py exit ${code}`)),
    );
  });
}
