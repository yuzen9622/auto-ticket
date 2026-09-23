import path from "node:path";

import { describe, expect, it, vi } from "vitest";

import {
  compareVersions,
  detectInstallation,
  fetchLatestVersion,
  update,
} from "../src/update.mjs";

const NPM_ROOT = "/usr/local/lib/node_modules";
const PKG_DIR = path.join(NPM_ROOT, "@yuzen9622", "auto-ticket");

function registry(version, { ok = true, status = 200 } = {}) {
  return vi.fn(async () => ({ ok, status, json: async () => ({ version }) }));
}

/** 只認得 npm 的替身：`npm root -g` 回 NPM_ROOT，安裝指令依 `installResult` 決定。 */
function fakeNpm({ installResult = { code: 0 }, onInstall = () => {} } = {}) {
  const calls = [];
  const runImpl = vi.fn(async (cmd, args) => {
    calls.push([cmd, ...args].join(" "));
    if (cmd === "npm" && args[0] === "root") return { code: 0, stdout: `${NPM_ROOT}\n`, stderr: "" };
    if (cmd === "npm" && args[0] === "install") {
      onInstall();
      return { stdout: "", stderr: "", error: null, ...installResult };
    }
    return { code: 1, stdout: "", stderr: "", error: new Error("ENOENT") };
  });
  return { calls, runImpl };
}

/** realpath 原樣回傳、package.json 讀出 `state.version` 的檔案系統替身。 */
function fakeFs(state) {
  return {
    realpath: async (p) => p,
    readFile: async (p) => {
      if (p === path.join(PKG_DIR, "package.json")) return JSON.stringify({ version: state.version });
      throw Object.assign(new Error("ENOENT"), { code: "ENOENT" });
    },
  };
}

describe("compareVersions", () => {
  it("逐段比數字，不是比字串", () => {
    expect(compareVersions("0.10.0", "0.9.0")).toBeGreaterThan(0);
    expect(compareVersions("0.5.0", "0.5.0")).toBe(0);
    expect(compareVersions("0.5.0", "1.0.0")).toBeLessThan(0);
  });

  it("正式版大於同號的預發版", () => {
    expect(compareVersions("1.0.0", "1.0.0-rc.1")).toBeGreaterThan(0);
    expect(compareVersions("1.0.0-rc.2", "1.0.0-rc.10")).toBeLessThan(0);
  });
});

describe("fetchLatestVersion", () => {
  it("回傳 registry 的 latest 版本", async () => {
    await expect(fetchLatestVersion({ fetchImpl: registry("0.6.0") })).resolves.toBe("0.6.0");
  });

  it("連線失敗 → UPDATE_CHECK_FAILED", async () => {
    const fetchImpl = async () => {
      throw new Error("getaddrinfo ENOTFOUND");
    };
    await expect(fetchLatestVersion({ fetchImpl })).rejects.toMatchObject({
      code: "UPDATE_CHECK_FAILED",
    });
  });

  it("HTTP 錯誤或格式不符 → UPDATE_CHECK_FAILED", async () => {
    await expect(
      fetchLatestVersion({ fetchImpl: registry("x", { ok: false, status: 503 }) }),
    ).rejects.toMatchObject({ code: "UPDATE_CHECK_FAILED" });
    await expect(fetchLatestVersion({ fetchImpl: registry("not-a-version") })).rejects.toMatchObject(
      { code: "UPDATE_CHECK_FAILED" },
    );
  });
});

describe("detectInstallation", () => {
  it("自己所在目錄就是 npm 全域目錄裡的本套件 → npm", async () => {
    const { runImpl } = fakeNpm();
    const found = await detectInstallation({
      packageRoot: PKG_DIR,
      runImpl,
      fsImpl: fakeFs({ version: "0.5.0" }),
    });
    expect(found.kind).toBe("global");
    expect(found.installer.name).toBe("npm");
  });

  it("專案本地依賴不能被當成全域安裝", async () => {
    const { runImpl } = fakeNpm();
    const found = await detectInstallation({
      packageRoot: "/work/project/node_modules/@yuzen9622/auto-ticket",
      runImpl,
      fsImpl: fakeFs({ version: "0.5.0" }),
    });
    expect(found).toMatchObject({ installer: null, kind: "unknown" });
  });

  it("npx 快取與原始碼目錄都不去問套件管理工具", async () => {
    const runImpl = vi.fn();
    const fsImpl = fakeFs({});
    await expect(
      detectInstallation({
        packageRoot: "/home/u/.npm/_npx/abc/node_modules/@yuzen9622/auto-ticket",
        runImpl,
        fsImpl,
      }),
    ).resolves.toMatchObject({ kind: "npx" });
    await expect(
      detectInstallation({ packageRoot: "/work/auto-ticket/packages/cli", runImpl, fsImpl }),
    ).resolves.toMatchObject({ kind: "source" });
    expect(runImpl).not.toHaveBeenCalled();
  });
});

describe("update", () => {
  it("已是最新版 → 不偵測安裝、不跑任何指令", async () => {
    const { runImpl } = fakeNpm();
    const log = vi.fn();
    const result = await update({
      currentVersion: "0.5.0",
      packageRoot: PKG_DIR,
      log,
      fetchImpl: registry("0.5.0"),
      runImpl,
      fsImpl: fakeFs({ version: "0.5.0" }),
    });
    expect(result.status).toBe("up-to-date");
    expect(runImpl).not.toHaveBeenCalled();
    expect(log.mock.calls.flat().join("\n")).toContain("已是最新版本");
  });

  it("有新版 → 以偵測到的工具安裝釘選版本，並回讀確認", async () => {
    const state = { version: "0.5.0" };
    const { calls, runImpl } = fakeNpm({ onInstall: () => (state.version = "0.6.0") });
    const log = vi.fn();
    const result = await update({
      currentVersion: "0.5.0",
      packageRoot: PKG_DIR,
      log,
      fetchImpl: registry("0.6.0"),
      runImpl,
      fsImpl: fakeFs(state),
    });
    expect(result).toEqual({ status: "updated", before: "0.5.0", after: "0.6.0" });
    // 裝的是剛查到的那個版本號，而不是 @latest——查完到裝之間 registry 可能又動了。
    expect(calls).toContain("npm install -g @yuzen9622/auto-ticket@0.6.0");
    expect(log.mock.calls.flat().join("\n")).toContain("0.5.0 → 0.6.0");
  });

  it("安裝失敗 → UPDATE_FAILED，並說明目前實際裝著哪一版", async () => {
    const { runImpl } = fakeNpm({ installResult: { code: 1 } });
    const err = await update({
      currentVersion: "0.5.0",
      packageRoot: PKG_DIR,
      log: () => {},
      fetchImpl: registry("0.6.0"),
      runImpl,
      fsImpl: fakeFs({ version: "0.5.0" }),
    }).catch((e) => e);
    expect(err.code).toBe("UPDATE_FAILED");
    expect(err.message).toContain("目前安裝的版本：0.5.0");
    expect(err.message).toContain("npm install -g @yuzen9622/auto-ticket@0.6.0");
  });

  it("工具回報成功但版本沒變 → 不宣稱更新完成", async () => {
    const { runImpl } = fakeNpm();
    await expect(
      update({
        currentVersion: "0.5.0",
        packageRoot: PKG_DIR,
        log: () => {},
        fetchImpl: registry("0.6.0"),
        runImpl,
        fsImpl: fakeFs({ version: "0.5.0" }),
      }),
    ).rejects.toMatchObject({ code: "UPDATE_FAILED" });
  });

  it("認不出安裝方式 → UPDATE_UNSUPPORTED，附上手動指令", async () => {
    const { runImpl } = fakeNpm();
    const err = await update({
      currentVersion: "0.5.0",
      packageRoot: "/home/u/.npm/_npx/abc/node_modules/@yuzen9622/auto-ticket",
      log: () => {},
      fetchImpl: registry("0.6.0"),
      runImpl,
      fsImpl: fakeFs({}),
    }).catch((e) => e);
    expect(err.code).toBe("UPDATE_UNSUPPORTED");
    expect(err.message).toContain("npm install -g @yuzen9622/auto-ticket@0.6.0");
  });

  it("本機版本比 registry 新 → 不降版", async () => {
    const { runImpl } = fakeNpm();
    const result = await update({
      currentVersion: "0.7.0",
      packageRoot: PKG_DIR,
      log: () => {},
      fetchImpl: registry("0.6.0"),
      runImpl,
      fsImpl: fakeFs({}),
    });
    expect(result.status).toBe("ahead");
    expect(runImpl).not.toHaveBeenCalled();
  });
});
