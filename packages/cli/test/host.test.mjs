import path from "node:path";

import { describe, expect, it } from "vitest";

import { CliError, ExitCode } from "../src/command.mjs";
import { assertPortsFree, buildEnv, DEFAULT_PORTS, ensureBrowsers } from "../src/host.mjs";

function fakeNet(busyPorts = new Set()) {
  return {
    createServer: () => {
      let errorHandler = () => {};
      return {
        once: (evt, cb) => {
          if (evt === "error") errorHandler = cb;
        },
        listen: (port, _host, cb) => {
          if (busyPorts.has(port)) {
            process.nextTick(() => errorHandler(Object.assign(new Error("busy"), { code: "EADDRINUSE" })));
          } else {
            process.nextTick(cb);
          }
        },
        close: (cb) => cb(),
      };
    },
  };
}

function fakePaths(root = "/fake/home/.auto-ticket") {
  return {
    db: `${root}/data/auto-ticket.db`,
    screenshots: `${root}/data/screenshots`,
    credentials: `${root}/data/credentials`,
    timelines: `${root}/data/timelines`,
    browserProfiles: `${root}/browser-profiles`,
    msPlaywright: `${root}/ms-playwright`,
  };
}

describe("DEFAULT_PORTS", () => {
  it("① 恆為 {api:8000, web:3000}", () => {
    expect(DEFAULT_PORTS).toEqual({ api: 8000, web: 3000 });
  });
});

describe("assertPortsFree", () => {
  it("② 8000 被佔用 → CliError code=10 且訊息含埠號", async () => {
    const netImpl = fakeNet(new Set([8000]));
    await expect(assertPortsFree({ netImpl })).rejects.toMatchObject({
      code: "PORT_IN_USE",
    });
    try {
      await assertPortsFree({ netImpl });
      expect.unreachable();
    } catch (err) {
      expect(err.name).toBe("CliError");
      expect(err.message).toMatch(/8000/);
    }
  });

  it("3000 被佔用 → CliError code=10 且訊息含埠號", async () => {
    const netImpl = fakeNet(new Set([3000]));
    try {
      await assertPortsFree({ netImpl });
      expect.unreachable();
    } catch (err) {
      expect(ExitCode[err.code]).toBe(ExitCode.PORT_IN_USE);
      expect(err.message).toMatch(/3000/);
    }
  });

  it("兩個埠都空閒時不拋錯", async () => {
    const netImpl = fakeNet(new Set());
    await expect(assertPortsFree({ netImpl })).resolves.toBeUndefined();
  });
});

describe("buildEnv", () => {
  it("PYTHONPATH 指向 runtime 的 site-packages 與 app/src", () => {
    const env = buildEnv({ paths: fakePaths(), runtimeDir: "/rt", delimiter: ":" });
    // 沒有這一條，serve_api.py 會在 import uvicorn 當場死掉——而且是啟動 90 秒
    // 逾時之後才看得到，不是立刻。分隔字元由 delimiter 決定，路徑分隔則交給
    // path.join，所以預期值也要用 path.join 組，不能寫死斜線。
    expect(env.PYTHONPATH).toBe(
      [path.join("/rt", "site-packages"), path.join("/rt", "app", "src")].join(":"),
    );
  });

  it("Windows 用分號串 PYTHONPATH", () => {
    const env = buildEnv({ paths: fakePaths(), runtimeDir: "C:\\rt", delimiter: ";" });
    expect(env.PYTHONPATH.split(";").length).toBe(2);
  });

  it("③ 產出 §8 表列全部變數，CORS_ORIGINS 指向 3000", () => {
    const env = buildEnv({ paths: fakePaths(), runtimeDir: "/rt" });
    for (const key of [
      "AUTO_TICKET_DB_PATH",
      "AUTO_TICKET_SCREENSHOT_DIR",
      "AUTO_TICKET_VAULT_ROOT",
      "AUTO_TICKET_TIMELINE_DIR",
      "AUTO_TICKET_BROWSER_PROFILE_ROOT",
      "AUTO_TICKET_CORS_ORIGINS",
      "PLAYWRIGHT_BROWSERS_PATH",
      "PYTHONPATH",
      "PYTHONNOUSERSITE",
      "PYTHONUTF8",
    ]) {
      expect(env[key], `buildEnv 缺少 ${key}`).toBeTruthy();
    }
    expect(env.AUTO_TICKET_DB_PATH).toBe("/fake/home/.auto-ticket/data/auto-ticket.db");
    expect(env.AUTO_TICKET_SCREENSHOT_DIR).toBe("/fake/home/.auto-ticket/data/screenshots");
    expect(env.AUTO_TICKET_VAULT_ROOT).toBe("/fake/home/.auto-ticket/data/credentials");
    expect(env.AUTO_TICKET_TIMELINE_DIR).toBe("/fake/home/.auto-ticket/data/timelines");
    expect(env.AUTO_TICKET_BROWSER_PROFILE_ROOT).toBe("/fake/home/.auto-ticket/browser-profiles");
    expect(env.AUTO_TICKET_CORS_ORIGINS).toBe("http://127.0.0.1:3000,http://localhost:3000");
    expect(env.PLAYWRIGHT_BROWSERS_PATH).toBe("/fake/home/.auto-ticket/ms-playwright");
    expect(env.PYTHONNOUSERSITE).toBe("1");
    expect(env.PYTHONUTF8).toBe("1");
  });

  it("⑤ 不含任何 token 類環境變數", () => {
    const env = buildEnv({ paths: fakePaths() });
    const keys = Object.keys(env).join(",");
    expect(keys).not.toMatch(/TOKEN/i);
  });
});

describe("ensureBrowsers", () => {
  it("④ 第二次呼叫為 no-op（可重入）", async () => {
    let spawnCalls = 0;
    const spawnImpl = async () => {
      spawnCalls += 1;
    };
    const paths = { msPlaywright: "/fake/home/.auto-ticket/ms-playwright" };

    let installed = false;
    const existsImpl = () => installed;

    const first = await ensureBrowsers({ paths, spawnImpl, existsImpl });
    installed = true; // 第一次呼叫後視為已安裝
    const second = await ensureBrowsers({ paths, spawnImpl, existsImpl });

    expect(first.skipped).toBe(false);
    expect(second.skipped).toBe(true);
    expect(spawnCalls).toBe(1);
  });
});
