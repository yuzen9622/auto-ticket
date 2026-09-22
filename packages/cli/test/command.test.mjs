import os from "node:os";
import path from "node:path";

import { describe, expect, it, vi } from "vitest";

import {
  assertChromePresent,
  formatBytes,
  listInstalledRuntimes,
  CliError,
  doctor,
  ExitCode,
  loadManifest,
  parseArgs,
  paths,
  resolveTarget,
} from "../src/command.mjs";

describe("resolveTarget", () => {
  it("darwin/arm64 → darwin-arm64", () => {
    expect(resolveTarget({ platform: "darwin", arch: "arm64" })).toBe("darwin-arm64");
  });

  it("darwin/x64（非 Rosetta）→ darwin-x64", () => {
    expect(
      resolveTarget({ platform: "darwin", arch: "x64", sysctlImpl: () => "0" }),
    ).toBe("darwin-x64");
  });

  it("win32/x64 → win32-x64", () => {
    expect(resolveTarget({ platform: "win32", arch: "x64" })).toBe("win32-x64");
  });

  it("linux/x64 → CliError code=2", () => {
    try {
      resolveTarget({ platform: "linux", arch: "x64" });
      expect.unreachable();
    } catch (err) {
      expect(err).toBeInstanceOf(CliError);
      expect(err.exitCode).toBe(ExitCode.UNSUPPORTED_PLATFORM);
    }
  });

  it("win32/arm64 → CliError code=2", () => {
    expect(() => resolveTarget({ platform: "win32", arch: "arm64" })).toThrow(CliError);
  });

  it("Rosetta 偵測到時給出原生 Node 指引，不回傳 darwin-x64", () => {
    try {
      resolveTarget({ platform: "darwin", arch: "x64", sysctlImpl: () => "1\n" });
      expect.unreachable("應該要拋錯");
    } catch (err) {
      expect(err).toBeInstanceOf(CliError);
      expect(err.exitCode).toBe(ExitCode.UNSUPPORTED_PLATFORM);
      expect(err.message).toMatch(/Rosetta/);
      expect(err.message).toMatch(/arm64/);
    }
  });
});

describe("paths", () => {
  it("以 os.homedir() 為根", () => {
    const p = paths({ homeDir: "/fake/home" });
    expect(p.root).toBe(path.join("/fake/home", ".auto-ticket"));
    expect(p.db).toBe(path.join("/fake/home", ".auto-ticket", "data", "auto-ticket.db"));
  });

  it("預設使用真正的 os.homedir()，不使用 %APPDATA%/%LOCALAPPDATA%", () => {
    const p = paths();
    expect(p.root.startsWith(os.homedir())).toBe(true);
  });

  it("Windows 平台下也不改用 APPDATA（paths() 完全不看 process.env）", () => {
    const before = process.env.APPDATA;
    process.env.APPDATA = "C:/Users/someone/AppData/Roaming";
    try {
      const p = paths({ homeDir: "C:/Users/someone" });
      expect(p.root).toBe(path.join("C:/Users/someone", ".auto-ticket"));
      expect(p.root.includes("AppData")).toBe(false);
    } finally {
      if (before === undefined) delete process.env.APPDATA;
      else process.env.APPDATA = before;
    }
  });
});

describe("loadManifest", () => {
  const baseManifest = {
    version: "1.2.3",
    targets: { "darwin-arm64": { file: "x.tar.gz", sha256: "a".repeat(64), size: 1 } },
  };

  it("版本相符時回傳 target entry", () => {
    const entry = loadManifest(baseManifest, { packageVersion: "1.2.3", target: "darwin-arm64" });
    expect(entry.file).toBe("x.tar.gz");
  });

  it("manifest 版本不符 → code=4", () => {
    try {
      loadManifest(baseManifest, { packageVersion: "9.9.9", target: "darwin-arm64" });
      expect.unreachable();
    } catch (err) {
      expect(err).toBeInstanceOf(CliError);
      expect(err.exitCode).toBe(ExitCode.MANIFEST_MISMATCH);
    }
  });

  it("缺 target → 明確錯誤", () => {
    try {
      loadManifest(baseManifest, { packageVersion: "1.2.3", target: "win32-x64" });
      expect.unreachable();
    } catch (err) {
      expect(err).toBeInstanceOf(CliError);
      expect(err.message).toMatch(/win32-x64/);
    }
  });
});

describe("doctor", () => {
  it("不寫入任何檔案", async () => {
    const fsPromises = await import("node:fs/promises");
    const writeSpy = vi.spyOn(fsPromises.default, "writeFile");
    const appendSpy = vi.spyOn(fsPromises.default, "appendFile");
    const mkdirSpy = vi.spyOn(fsPromises.default, "mkdir");

    await doctor({
      homeDir: "/tmp/does-not-matter",
      spawnImpl: () => "tar (fake)",
      existsImpl: () => false,
    });

    expect(writeSpy).not.toHaveBeenCalled();
    expect(appendSpy).not.toHaveBeenCalled();
    expect(mkdirSpy).not.toHaveBeenCalled();

    writeSpy.mockRestore();
    appendSpy.mockRestore();
    mkdirSpy.mockRestore();
  });
});

describe("parseArgs", () => {
  it("預設指令是 start", () => {
    expect(parseArgs([]).command).toBe("start");
  });

  it("涵蓋 start/doctor/runtime/migrate/logs/version", () => {
    expect(parseArgs(["doctor"]).command).toBe("doctor");
    expect(parseArgs(["runtime", "list"]).command).toBe("runtime");
    expect(parseArgs(["migrate"]).command).toBe("migrate");
    expect(parseArgs(["logs"]).command).toBe("logs");
    expect(parseArgs(["version"]).command).toBe("version");
  });

  it("runtime clean 不存在", () => {
    expect(() => parseArgs(["runtime", "clean"])).toThrow(CliError);
  });

  it("--api-port / --web-port 旗標不存在", () => {
    expect(() => parseArgs(["start", "--api-port", "9000"])).toThrow(CliError);
    expect(() => parseArgs(["start", "--web-port", "9000"])).toThrow(CliError);
  });

  it("未知旗標會報錯", () => {
    expect(() => parseArgs(["start", "--totally-unknown"])).toThrow(CliError);
  });

  it("start 旗標解析", () => {
    const { flags } = parseArgs(["start", "--headed", "--no-ocr", "--skip-migration", "--from", "/x"]);
    expect(flags).toMatchObject({ headed: true, noOcr: true, skipMigration: true, from: "/x" });
  });
});

describe("start 的前置條件", () => {
  it("找不到 Chrome → CliError code=9", () => {
    const err = (() => {
      try {
        assertChromePresent({ existsImpl: () => false });
        return null;
      } catch (e) {
        return e;
      }
    })();
    expect(err).not.toBeNull();
    expect(err.code).toBe("CHROME_MISSING");
    expect(err.exitCode).toBe(ExitCode.CHROME_MISSING);
    expect(err.message).toContain("Chrome");
  });

  it("Chrome 在就直接通過，不丟錯", () => {
    expect(() => assertChromePresent({ existsImpl: () => true })).not.toThrow();
  });
});

describe("runtime list", () => {
  it("列出版本與大小，標記目前使用中的那一版，且完全不寫入", async () => {
    const writes = [];
    // 鍵一律用 path.join 組：實作內部也是 path.join，寫死斜線在 Windows 上對不起來。
    const tree = {
      "/rt": [
        { name: "0.1.0", isDirectory: () => true, isFile: () => false },
        { name: "0.0.9", isDirectory: () => true, isFile: () => false },
        { name: ".tmp", isDirectory: () => true, isFile: () => false },
      ],
      [path.join("/rt", "0.1.0")]: [
        { name: "a.bin", isDirectory: () => false, isFile: () => true },
      ],
      [path.join("/rt", "0.0.9")]: [
        { name: "b.bin", isDirectory: () => false, isFile: () => true },
      ],
    };
    const fsImpl = {
      readdir: async (dir) => tree[dir] ?? [],
      stat: async () => ({ size: 1024 }),
      writeFile: async (...a) => writes.push(a),
      rm: async (...a) => writes.push(a),
    };

    const rows = await listInstalledRuntimes(
      { runtimeRoot: "/rt" },
      "0.1.0",
      fsImpl,
    );

    expect(rows).toEqual([
      { version: "0.0.9", bytes: 1024, inUse: false },
      { version: "0.1.0", bytes: 1024, inUse: true },
    ]);
    // `.tmp` 是下載暫存，不是可回收的版本。
    expect(rows.some((r) => r.version === ".tmp")).toBe(false);
    expect(writes).toEqual([]);
  });

  it("formatBytes 用 IEC 單位", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1536)).toBe("1.5 KiB");
    expect(formatBytes(2 * 1024 ** 3)).toBe("2.0 GiB");
  });
});
