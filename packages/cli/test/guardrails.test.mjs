import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CLI_ROOT = path.resolve(__dirname, "..");

async function listFiles(dir) {
  const out = [];
  const entries = await readdir(dir, { withFileTypes: true });
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...(await listFiles(full)));
    else out.push(full);
  }
  return out;
}

async function readAllSourceText() {
  const files = [
    ...(await listFiles(path.join(CLI_ROOT, "bin"))),
    ...(await listFiles(path.join(CLI_ROOT, "src"))),
  ];
  const contents = await Promise.all(files.map((f) => readFile(f, "utf8")));
  return { files, joined: contents.join("\n") };
}

const FORBIDDEN_GATEKEEPER_BYPASS = [
  "xattr",
  "com.apple.quarantine",
  "spctl",
  "codesign",
  "--no-sandbox",
];

const FORBIDDEN_CHROME_PROFILE = [
  "Library/Application Support/Google/Chrome",
  "Local/Google/Chrome/User Data",
  "--user-data-dir",
];

const FORBIDDEN_TOKEN_NAMES = ["GITHUB_TOKEN", "GH_TOKEN"];
const FORBIDDEN_CONTAINER_WORDS = ["docker", "Dockerfile", "compose"];

describe("guardrails: packages/cli 靜態掃描", () => {
  it("不得出現任何 Gatekeeper 繞過手法（D5）", async () => {
    const { joined } = await readAllSourceText();
    for (const term of FORBIDDEN_GATEKEEPER_BYPASS) {
      expect(joined.includes(term), `不應出現 "${term}"`).toBe(false);
    }
  });

  it("不得讀寫使用者日常 Chrome profile，也不得設定 --user-data-dir", async () => {
    const { joined } = await readAllSourceText();
    for (const term of FORBIDDEN_CHROME_PROFILE) {
      expect(joined.includes(term), `不應出現 "${term}"`).toBe(false);
    }
  });

  it("不得出現任何 GitHub token 環境變數字樣", async () => {
    const { joined } = await readAllSourceText();
    for (const term of FORBIDDEN_TOKEN_NAMES) {
      expect(joined.includes(term), `不應出現 "${term}"`).toBe(false);
    }
  });

  it("不得引入 Docker（D7）", async () => {
    const { joined } = await readAllSourceText();
    const lower = joined.toLowerCase();
    for (const term of FORBIDDEN_CONTAINER_WORDS) {
      expect(lower.includes(term.toLowerCase()), `不應出現 "${term}"`).toBe(false);
    }
  });

  it("src/ 恰好 5 個 .mjs 檔，檔名固定", async () => {
    const entries = await readdir(path.join(CLI_ROOT, "src"));
    const mjsFiles = entries.filter((f) => f.endsWith(".mjs")).sort();
    expect(mjsFiles).toEqual(
      ["command.mjs", "host.mjs", "migration.mjs", "runtime-cache.mjs", "supervisor.mjs"].sort(),
    );
  });

  it("四個領域模組彼此零 import（無循環依賴）", async () => {
    const domainModules = ["runtime-cache.mjs", "migration.mjs", "host.mjs", "supervisor.mjs"];
    for (const mod of domainModules) {
      const text = await readFile(path.join(CLI_ROOT, "src", mod), "utf8");
      for (const other of domainModules) {
        if (other === mod) continue;
        const otherBase = other.replace(".mjs", "");
        const importRegex = new RegExp(`from\\s+["'][^"']*${otherBase}\\.mjs["']`);
        expect(importRegex.test(text), `${mod} 不得 import ${other}`).toBe(false);
      }
    }
  });

  it("領域模組不得反向 import command.mjs（依賴方向單一，無循環）", async () => {
    const domainModules = ["runtime-cache.mjs", "migration.mjs", "host.mjs", "supervisor.mjs"];
    for (const mod of domainModules) {
      const text = await readFile(path.join(CLI_ROOT, "src", mod), "utf8");
      expect(
        /from\s+["'][^"']*command\.mjs["']/.test(text),
        `${mod} 不得 import command.mjs`,
      ).toBe(false);
    }
  });

  it("bin 對應得到真實檔案，且路徑不帶 ./ 前綴", async () => {
    const pkg = JSON.parse(await readFile(path.join(CLI_ROOT, "package.json"), "utf8"));
    const target = pkg.bin?.["auto-ticket"];
    expect(target, "package.json 缺少 bin.auto-ticket").toBeTruthy();
    // npm 會把帶 ./ 前綴的 bin 路徑判為無效，**在 publish 時整條刪掉**——
    // 發出去的套件就沒有任何指令，而這裡沒有任何測試會因此變紅。
    expect(target.startsWith("./"), "bin 路徑不得帶 ./ 前綴").toBe(false);
    await expect(readFile(path.join(CLI_ROOT, target), "utf8")).resolves.toMatch(
      /^#!\/usr\/bin\/env node/,
    );
    expect(pkg.files).toContain("bin");
  });

  it("package.json 宣告 repository，否則 npm provenance 會拒發", async () => {
    const pkg = JSON.parse(await readFile(path.join(CLI_ROOT, "package.json"), "utf8"));
    expect(pkg.repository?.url).toContain("github.com/yuzen9622/auto-ticket");
  });

  it("package.json 的 dependencies 恆為空物件", async () => {
    const pkg = JSON.parse(await readFile(path.join(CLI_ROOT, "package.json"), "utf8"));
    expect(pkg.dependencies).toEqual({});
  });
});
