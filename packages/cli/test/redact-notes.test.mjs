import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { redactNotes } from "../../../scripts/release/redact_notes.mjs";

const SCRIPT = fileURLToPath(
  new URL("../../../scripts/release/redact_notes.mjs", import.meta.url)
);
const INSTALL_NOTES = `## 安裝

使用 npx：

\`\`\`bash
npx @yuzen9622/auto-ticket
\`\`\`

或使用 pnpm：

\`\`\`bash
pnpm dlx @yuzen9622/auto-ticket
\`\`\`

## 更新

\`\`\`bash
autix update
\`\`\`
`;
const EXPECTED = `## [0.6.3](https://example.com)

### Bug Fixes

* 修正啟動問題
* 內部穩定性與相容性改善

${INSTALL_NOTES}`;
const OLD_NOTES = `## [0.6.3](https://example.com)

### Bug Fixes

* 修正啟動問題
* update captcha selector
${`
---

本工具的付款一律為 mock：不會選票、不會建立訂單、不會送出訂單、不會付款。

安裝：

\`\`\`bash
npx @yuzen9622/auto-ticket
\`\`\`
`.repeat(3)}`;

describe("redactNotes", () => {
  it("保留 changelog，移除重複聲明並更新安裝方法", () => {
    expect(redactNotes(OLD_NOTES)).toBe(EXPECTED);
  });

  it("空白輸入也產生完整說明", () => {
    expect(redactNotes("")).toBe(INSTALL_NOTES);
    expect(redactNotes(INSTALL_NOTES)).toBe(INSTALL_NOTES);
  });

  it("重跑不會累積區塊", () => {
    const once = redactNotes(OLD_NOTES);
    expect(redactNotes(redactNotes(once))).toBe(EXPECTED);
  });

  it("保留公開變更 section 與連結，排除內部 section", () => {
    const output = redactNotes(`## [0.6.3](https://example.com)

### Features

* 新功能 ([abc](https://example.com/abc))

### Performance Improvements

* 改善啟動速度

### Code Refactoring

* internal refactor
`);
    expect(output).toContain("### Features\n\n* 新功能 ([abc](https://example.com/abc))");
    expect(output).toContain("### Performance Improvements\n\n* 改善啟動速度");
    expect(output).not.toContain("internal refactor");
    expect(output).not.toContain("Code Refactoring");
    expect(redactNotes(output)).toBe(output);
  });

  it("CLI 支援 stdin / stdout", () => {
    expect(execFileSync(process.execPath, [SCRIPT], {
      input: OLD_NOTES,
      encoding: "utf8",
    })).toBe(EXPECTED);
  });

  it("發布流程的 --in / --out 可安全重跑", () => {
    const dir = mkdtempSync(join(tmpdir(), "release-notes-"));
    try {
      const input = join(dir, "raw.md");
      const output = join(dir, "public.md");
      writeFileSync(input, OLD_NOTES);
      execFileSync(process.execPath, [SCRIPT, "--in", input, "--out", output]);
      expect(readFileSync(output, "utf8")).toBe(EXPECTED);
      execFileSync(process.execPath, [SCRIPT, "--in", output, "--out", output]);
      expect(readFileSync(output, "utf8")).toBe(EXPECTED);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
