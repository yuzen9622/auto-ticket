import { describe, expect, it } from "vitest";

import {
  FOOTER,
  KEPT_SECTIONS,
  REDACTED_LINE,
  redactNotes,
} from "../../../scripts/release/redact_notes.mjs";

const SAMPLE = `## [1.2.0](https://github.com/o/r/compare/v1.1.0...v1.2.0) (2026-09-22)

### Features

* 支援三個平台的一鍵啟動 ([aaa](u))
* add captcha OCR retry for the registration flow ([bbb](u))

### Bug Fixes

* update the sold-out selector ([ccc](u))
* 修正時間軸目錄無法覆寫的問題 ([ddd](u))
* shorten warmup to T-3min ([eee](u))

### Performance Improvements

* 縮短啟動時間 ([fff](u))

### Code Refactoring

* drop the legacy log buffer ([ggg](u))

### Miscellaneous Chores

* bump deps ([hhh](u))

### Documentation

* rewrite the install guide ([iii](u))
`;

describe("redactNotes", () => {
  const output = redactNotes(SAMPLE);

  it("只保留 feat / fix / perf 三個 section", () => {
    expect(KEPT_SECTIONS).toEqual([
      "Features",
      "Bug Fixes",
      "Performance Improvements",
    ]);
    for (const kept of KEPT_SECTIONS) {
      expect(output).toContain(`### ${kept}`);
    }
    for (const dropped of [
      "Code Refactoring",
      "Miscellaneous Chores",
      "Documentation",
    ]) {
      expect(output).not.toContain(dropped);
    }
  });

  it("被移除的 section 連同它的條目一起消失", () => {
    expect(output).not.toContain("legacy log buffer");
    expect(output).not.toContain("bump deps");
    expect(output).not.toContain("rewrite the install guide");
  });

  it("命中敏感詞的整行被換成通用字樣，不是遮掉幾個字", () => {
    for (const leak of ["captcha", "selector", "warmup", "T-3min", "OCR retry"]) {
      expect(output).not.toContain(leak);
    }
    expect(output).toContain(REDACTED_LINE);
  });

  it("沒命中的條目原樣保留，包含連結", () => {
    expect(output).toContain("* 支援三個平台的一鍵啟動 ([aaa](u))");
    expect(output).toContain("* 修正時間軸目錄無法覆寫的問題 ([ddd](u))");
    expect(output).toContain("* 縮短啟動時間 ([fff](u))");
  });

  it("版本標題不會被當成 section 標題丟掉", () => {
    expect(output.split("\n")[0]).toBe(
      "## [1.2.0](https://github.com/o/r/compare/v1.1.0...v1.2.0) (2026-09-22)"
    );
  });

  it("同一段連續多行命中只留一行通用字樣", () => {
    const hits = output.split("\n").filter((l) => l === REDACTED_LINE);
    // Bug Fixes 有兩行命中但中間隔了一行沒命中的，所以是兩段，不是一段。
    expect(hits.length).toBe(3);
  });

  it("固定附上 mock 付款聲明與安裝指令", () => {
    expect(output.endsWith(FOOTER.join("\n"))).toBe(true);
    expect(output).toContain("付款一律為 mock");
    expect(output).toContain("npx @yuzen9622/auto-ticket");
  });

  it("輸出穩定（snapshot）", () => {
    expect(output).toMatchInlineSnapshot(`
      "## [1.2.0](https://github.com/o/r/compare/v1.1.0...v1.2.0) (2026-09-22)

      ### Features

      * 支援三個平台的一鍵啟動 ([aaa](u))
      * 內部穩定性與相容性改善

      ### Bug Fixes

      * 內部穩定性與相容性改善
      * 修正時間軸目錄無法覆寫的問題 ([ddd](u))
      * 內部穩定性與相容性改善

      ### Performance Improvements

      * 縮短啟動時間 ([fff](u))

      ---

      本工具的付款一律為 mock：不會選票、不會建立訂單、不會送出訂單、不會付款。

      安裝：

      \`\`\`bash
      npx @yuzen9622/auto-ticket
      \`\`\`
      "
    `);
  });
});
