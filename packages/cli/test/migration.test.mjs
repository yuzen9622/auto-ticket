import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CliError, ExitCode } from "../src/command.mjs";
import { migrate, readMarker } from "../src/migration.mjs";

let tmpRoot;
let sourceRoot;
let homeRoot;

function fakePaths(root) {
  const data = path.join(root, "data");
  return {
    root,
    data,
    db: path.join(data, "auto-ticket.db"),
    credentials: path.join(data, "credentials"),
    migratedMarker: path.join(data, ".migrated.json"),
  };
}

async function writeFileDeep(p, content) {
  await fs.mkdir(path.dirname(p), { recursive: true });
  await fs.writeFile(p, content);
}

async function makeSource(root) {
  await writeFileDeep(path.join(root, "pyproject.toml"), 'name = "auto-ticket"\n');
  await writeFileDeep(path.join(root, "data", "auto-ticket.db"), "sqlite-bytes");
  await writeFileDeep(path.join(root, "data", "credentials", ".vault_key"), "super-secret-key-content");
  await writeFileDeep(path.join(root, "data", "screenshots", "shot.png"), "png-bytes");
  await writeFileDeep(path.join(root, "data", "timelines", "t1.json"), "{}");
  await writeFileDeep(path.join(root, "data", "auto-ticket.db-wal"), "wal-bytes");
}

const fakeRunPython = async ({ sourceDb, destDb }) => {
  await fs.mkdir(path.dirname(destDb), { recursive: true });
  await fs.copyFile(sourceDb, destDb);
};

beforeEach(async () => {
  tmpRoot = await fs.mkdtemp(path.join(os.tmpdir(), "at-migration-"));
  sourceRoot = path.join(tmpRoot, "source");
  homeRoot = path.join(tmpRoot, "home", ".auto-ticket");
  await makeSource(sourceRoot);
});

afterEach(async () => {
  await fs.rm(tmpRoot, { recursive: true, force: true });
});

describe("migrate", () => {
  it("① 完整複製後來源檔案數/大小/mtime 不變", async () => {
    const before = await snapshotDir(path.join(sourceRoot, "data"));
    const paths = fakePaths(homeRoot);
    const result = await migrate({ from: sourceRoot, paths, runPython: fakeRunPython });
    expect(result.skipped).toBe(false);
    const after = await snapshotDir(path.join(sourceRoot, "data"));
    expect(after).toEqual(before);
  });

  it("② 重跑 → skip 且零寫入", async () => {
    const paths = fakePaths(homeRoot);
    await migrate({ from: sourceRoot, paths, runPython: fakeRunPython });

    const writeSpy = vi.spyOn(fs, "writeFile");
    const copySpy = vi.spyOn(fs, "copyFile");
    const renameSpy = vi.spyOn(fs, "rename");
    const result = await migrate({ from: sourceRoot, paths, runPython: fakeRunPython });
    expect(result.skipped).toBe(true);
    expect(writeSpy).not.toHaveBeenCalled();
    expect(copySpy).not.toHaveBeenCalled();
    expect(renameSpy).not.toHaveBeenCalled();
    writeSpy.mockRestore();
    copySpy.mockRestore();
    renameSpy.mockRestore();
  });

  it("③ 目標已有 db 且無標記 → code=6 且零寫入", async () => {
    const paths = fakePaths(homeRoot);
    await writeFileDeep(paths.db, "already-here");

    const writeSpy = vi.spyOn(fs, "writeFile");
    const copySpy = vi.spyOn(fs, "copyFile");
    await expect(migrate({ from: sourceRoot, paths, runPython: fakeRunPython })).rejects.toMatchObject({
      code: "MIGRATION_CONFLICT",
    });
    expect(writeSpy).not.toHaveBeenCalled();
    expect(copySpy).not.toHaveBeenCalled();
    writeSpy.mockRestore();
    copySpy.mockRestore();
  });

  it("④ --merge-missing 不覆蓋任何既有檔", async () => {
    const paths = fakePaths(homeRoot);
    await writeFileDeep(paths.db, "target-db-untouched");
    await writeFileDeep(path.join(paths.data, "screenshots", "shot.png"), "target-version-of-shot");

    const result = await migrate({ from: sourceRoot, paths, mergeMissing: true, runPython: fakeRunPython });
    expect(result.skipped).toBe(false);

    const dbContent = await fs.readFile(paths.db, "utf8");
    expect(dbContent).toBe("target-db-untouched");
    const shotContent = await fs.readFile(path.join(paths.data, "screenshots", "shot.png"), "utf8");
    expect(shotContent).toBe("target-version-of-shot");
    // 目標缺少的檔案（timelines/t1.json）應該被補上。
    const timeline = await fs.readFile(path.join(paths.data, "timelines", "t1.json"), "utf8");
    expect(timeline).toBe("{}");
  });

  it("⑤ 中途失敗 → 目標原狀、data.incoming-* 已刪", async () => {
    const paths = fakePaths(homeRoot);
    const failingRunPython = async () => {
      throw new Error("來源資料庫仍被佔用");
    };
    await expect(migrate({ from: sourceRoot, paths, runPython: failingRunPython })).rejects.toMatchObject({ name: "CliError" });

    const dataExists = await fs.stat(paths.data).then(() => true, () => false);
    expect(dataExists).toBe(false);

    const homeEntries = await fs.readdir(homeRoot).catch(() => []);
    const leftover = homeEntries.filter((e) => e.startsWith("data.incoming-"));
    expect(leftover).toEqual([]);
  });

  it("⑥ .vault_key POSIX 權限為 0600", async () => {
    if (process.platform === "win32") return; // POSIX 專屬權限語意
    const paths = fakePaths(homeRoot);
    await migrate({ from: sourceRoot, paths, runPython: fakeRunPython });
    const stat = await fs.stat(path.join(paths.credentials, ".vault_key"));
    expect(stat.mode & 0o777).toBe(0o600);
  });

  it("⑦ 不把 vault key 內容印到任何輸出", async () => {
    const paths = fakePaths(homeRoot);
    const logs = [];
    const spy = vi.spyOn(console, "log").mockImplementation((...args) => logs.push(args.join(" ")));
    try {
      await migrate({ from: sourceRoot, paths, runPython: fakeRunPython });
    } finally {
      spy.mockRestore();
    }
    expect(logs.join("\n")).not.toMatch(/super-secret-key-content/);
  });

  it("⑧ --dry-run 零寫入", async () => {
    const paths = fakePaths(homeRoot);
    const writeSpy = vi.spyOn(fs, "writeFile");
    const copySpy = vi.spyOn(fs, "copyFile");
    const mkdirSpy = vi.spyOn(fs, "mkdir");
    const result = await migrate({ from: sourceRoot, paths, dryRun: true, runPython: fakeRunPython });
    expect(result.dryRun).toBe(true);
    expect(writeSpy).not.toHaveBeenCalled();
    expect(copySpy).not.toHaveBeenCalled();
    expect(mkdirSpy).not.toHaveBeenCalled();
    writeSpy.mockRestore();
    copySpy.mockRestore();
    mkdirSpy.mockRestore();
  });

  it("找不到來源時是全新安裝，不是錯誤", async () => {
    const paths = fakePaths(path.join(tmpRoot, "home-empty", ".auto-ticket"));
    const result = await migrate({ from: undefined, cwd: path.join(tmpRoot, "nowhere"), paths, runPython: fakeRunPython });
    expect(result.skipped).toBe(false);
    expect(result.source).toBe(null);
    const marker = await readMarker(paths);
    expect(marker.mode).toBe("fresh");
  });
});

async function snapshotDir(dir) {
  const out = {};
  async function walk(d, rel) {
    const entries = await fs.readdir(d, { withFileTypes: true });
    for (const entry of entries) {
      const abs = path.join(d, entry.name);
      const relPath = path.join(rel, entry.name);
      if (entry.isDirectory()) {
        await walk(abs, relPath);
      } else {
        const st = await fs.stat(abs);
        out[relPath] = { size: st.size, mtimeMs: st.mtimeMs };
      }
    }
  }
  await walk(dir, ".");
  return out;
}
