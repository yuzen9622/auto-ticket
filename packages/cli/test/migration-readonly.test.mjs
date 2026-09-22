import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MIGRATION_FILE = path.resolve(__dirname, "..", "src", "migration.mjs");

// 破壞性呼叫：函式名稱 + 其後緊接的括號內容（含跨行）。
const DESTRUCTIVE_CALL = /\b(rm|unlink|rename|truncate)\w*\s*\(([\s\S]*?)\)/g;

// 任何看起來像「來源」的識別字，不得出現在破壞性呼叫的參數裡。
const SOURCE_LOOKING_IDENTIFIER = /\b(source\w*|sourceRoot|fromPath|item\.abs)\b/i;

describe("migration.mjs 不得對來源路徑做破壞性操作", () => {
  it("靜態掃描：rm/unlink/rename/truncate 的參數不含來源變數", async () => {
    const text = await readFile(MIGRATION_FILE, "utf8");
    const offenders = [];
    let match;
    DESTRUCTIVE_CALL.lastIndex = 0;
    while ((match = DESTRUCTIVE_CALL.exec(text))) {
      const [whole, , args] = match;
      if (SOURCE_LOOKING_IDENTIFIER.test(args)) {
        offenders.push(whole);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("唯一出現的 rename 只作用在 staging/目標路徑（stagingRoot → paths.data）", async () => {
    const text = await readFile(MIGRATION_FILE, "utf8");
    const renameCalls = text.match(/fsImpl\.rename\([^)]*\)/g) ?? [];
    expect(renameCalls.length).toBeGreaterThan(0);
    for (const call of renameCalls) {
      expect(call).toMatch(/stagingRoot/);
    }
  });
});
