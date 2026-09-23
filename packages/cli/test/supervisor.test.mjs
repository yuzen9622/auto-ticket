import { EventEmitter } from "node:events";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { CliError, ExitCode } from "../src/command.mjs";
import { openLogSink, shutdown, supervise, waitReady } from "../src/supervisor.mjs";

function makeFakeChild(pid) {
  const child = new EventEmitter();
  child.pid = pid;
  child.killed = false;
  child.exitCode = null;
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  child.kill = () => {
    child.killed = true;
  };
  return child;
}

function nextTick(times = 3) {
  return new Promise((resolve) => {
    let n = 0;
    const tick = () => {
      n += 1;
      if (n >= times) resolve();
      else setImmediate(tick);
    };
    setImmediate(tick);
  });
}

function fakeClock(start = 0) {
  const state = { t: start };
  return {
    now: () => state.t,
    sleepImpl: async (ms) => {
      state.t += ms;
    },
    state,
  };
}

function fakePaths(root) {
  const runtimeRoot = path.join(root, "runtime");
  const logs = path.join(root, "logs");
  const state = path.join(root, "state");
  return {
    runtimeDir: (v) => path.join(runtimeRoot, v),
    logFile: (name) => path.join(logs, `${name}.log`),
    state,
    supervisorState: path.join(state, "supervisor.json"),
  };
}

let tmpRoot;

beforeEach(async () => {
  tmpRoot = await fs.mkdtemp(path.join(os.tmpdir(), "at-supervisor-"));
});

afterEach(async () => {
  await fs.rm(tmpRoot, { recursive: true, force: true });
});

describe("waitReady", () => {
  it("checkImpl 為 true 立刻回傳", async () => {
    await expect(waitReady({ checkImpl: () => true, timeoutMs: 1000, sleepImpl: async () => {} })).resolves.toBe(true);
  });

  it("逾時丟出 CliError code=7", async () => {
    const { now, sleepImpl } = fakeClock();
    await expect(
      waitReady({ checkImpl: () => false, timeoutMs: 500, sleepImpl, now }),
    ).rejects.toMatchObject({ code: "START_TIMEOUT" });
  });
});

describe("openLogSink", () => {
  it("同時前綴轉發與 append 落檔", async () => {
    const writes = [];
    const appended = [];
    const fsImpl = {
      mkdir: async () => {},
      stat: async () => {
        throw new Error("not found");
      },
      appendFile: async (p, data) => appended.push({ p, data }),
    };
    const stdoutImpl = { write: (s) => writes.push(s) };
    const sink = await openLogSink({ name: "api", logPath: "/fake/logs/api.log", fsImpl, stdoutImpl });
    await sink.write("hello");
    expect(writes[0]).toBe("[api] hello\n");
    expect(appended[0]).toEqual({ p: "/fake/logs/api.log", data: "[api] hello\n" });
  });

  it("超過 50MB 只告警、不改名、不刪檔", async () => {
    const writes = [];
    let renameCalled = false;
    let rmCalled = false;
    const fsImpl = {
      mkdir: async () => {},
      stat: async () => ({ size: 60 * 1024 * 1024 }),
      appendFile: async () => {},
      rename: async () => {
        renameCalled = true;
      },
      rm: async () => {
        rmCalled = true;
      },
    };
    const stdoutImpl = { write: (s) => writes.push(s) };
    await openLogSink({ name: "web", logPath: "/fake/logs/web.log", fsImpl, stdoutImpl });
    expect(writes.some((w) => w.includes("50MB"))).toBe(true);
    expect(renameCalled).toBe(false);
    expect(rmCalled).toBe(false);
  });
});

describe("shutdown", () => {
  it("⑦ Windows 分支呼叫 taskkill /pid <pid> /T /F", async () => {
    const calls = [];
    const child = makeFakeChild(4321);
    const spawnImpl = (cmd, args) => {
      calls.push({ cmd, args });
      const tk = new EventEmitter();
      setImmediate(() => tk.emit("exit", 0));
      return tk;
    };
    await shutdown({ children: [{ child }], platform: "win32", spawnImpl });
    expect(calls).toEqual([{ cmd: "taskkill", args: ["/pid", "4321", "/T", "/F"] }]);
  });

  it("POSIX 分支對 process group 送 SIGTERM，逾期未退則 SIGKILL", async () => {
    const child = makeFakeChild(999);
    const killCalls = [];
    const killImpl = (pid, signal) => killCalls.push([pid, signal]);
    await shutdown({ children: [{ child }], platform: "darwin", killImpl, graceMs: 20, sleepImpl: async (ms) => {
      await new Promise((r) => setTimeout(r, ms));
    } });
    expect(killCalls[0]).toEqual([-999, "SIGTERM"]);
    expect(killCalls.some(([pid, sig]) => pid === -999 && sig === "SIGKILL")).toBe(true);
  });
});

describe("supervise", () => {
  function makeHarness({ apiReady = true, webReady = true, workerGraceMs = 0 } = {}) {
    const spawnCalls = [];
    const children = { api: null, worker: [], web: null };
    const clock = fakeClock();

    const spawnImpl = (cmd, args) => {
      spawnCalls.push({ cmd, args });
      const pid = 1000 + spawnCalls.length;
      const child = makeFakeChild(pid);
      if (args.some((a) => String(a).includes("serve_api.py"))) children.api = child;
      else if (args.includes("worker")) children.worker.push(child);
      else if (args.some((a) => String(a).includes("server.js"))) children.web = child;
      return child;
    };

    const fetchImpl = async (url) => {
      if (url.includes("8000")) {
        return {
          ok: apiReady,
          status: apiReady ? 200 : 503,
          json: async () => ({ status: "ok", version: "1.0.0", broker_ok: true, worker_seen_at: null }),
        };
      }
      if (url.includes("3000")) return { ok: webReady, status: webReady ? 200 : 503 };
      return { ok: false, status: 500 };
    };

    const killCalls = [];
    const killImpl = (pid, signal) => {
      killCalls.push([pid, signal]);
      if (signal === 0) return; // 存活檢查
    };

    const paths = fakePaths(tmpRoot + `-${Math.random().toString(36).slice(2)}`);

    return { spawnImpl, fetchImpl, killImpl, killCalls, spawnCalls, children, clock, paths };
  }

  it("① 就緒順序 api → worker → web", async () => {
    const h = makeHarness();
    const signalSource = new EventEmitter();
    let attachedResolve;
    const attached = new Promise((r) => (attachedResolve = r));

    const supervisePromise = supervise({
      env: {},
      paths: h.paths,
      version: "1.0.0",
      spawnImpl: h.spawnImpl,
      fetchImpl: h.fetchImpl,
      killImpl: h.killImpl,
      platform: "darwin",
      now: h.clock.now,
      sleepImpl: h.clock.sleepImpl,
      signalSource,
      workerGraceMs: 0,
      onListenersAttached: () => attachedResolve(),
    });

    await attached;
    signalSource.emit("SIGINT");
    const exitCode = await supervisePromise;

    expect(exitCode).toBe(0);
    const order = h.spawnCalls.map((c) =>
      c.args.some((a) => String(a).includes("serve_api.py"))
        ? "api"
        : c.args.includes("worker")
          ? "worker"
          : "web",
    );
    expect(order).toEqual(["api", "worker", "web"]);
  });

  it("④ SIGINT → 三個子行程都被終止（反向），沒有孤兒，state 檔被清掉", async () => {
    const h = makeHarness();
    const signalSource = new EventEmitter();
    let attachedResolve;
    const attached = new Promise((r) => (attachedResolve = r));

    const supervisePromise = supervise({
      env: {},
      paths: h.paths,
      version: "1.0.0",
      spawnImpl: h.spawnImpl,
      fetchImpl: h.fetchImpl,
      killImpl: h.killImpl,
      platform: "darwin",
      now: h.clock.now,
      sleepImpl: h.clock.sleepImpl,
      signalSource,
      workerGraceMs: 0,
      onListenersAttached: () => attachedResolve(),
    });

    await attached;
    const started = [h.children.api, ...h.children.worker, h.children.web];
    expect(started.filter(Boolean).length).toBe(3);

    signalSource.emit("SIGINT");
    expect(await supervisePromise).toBe(0);

    // POSIX 是對整個 process group 送訊號，所以 pid 帶負號；三個都要收到，
    // 少一個就代表有孤兒行程繼續佔著 8000/3000 或 SQLite broker。
    const groups = h.killCalls.filter(([, sig]) => sig === "SIGTERM").map(([pid]) => pid);
    for (const child of started) {
      expect(groups).toContain(-child.pid);
    }
    // 關閉是啟動的反向：web → worker → api。
    expect(groups).toEqual(started.map((c) => -c.pid).reverse());

    await expect(fs.stat(h.paths.supervisorState)).rejects.toThrow();
  });

  it("子行程 spawn 失敗 → 走正常關閉，不留孤兒", async () => {
    const h = makeHarness();
    const killed = [];
    const spawnImpl = (cmd, args) => {
      h.spawnCalls.push({ cmd, args });
      const child = makeFakeChild(2000 + h.spawnCalls.length);
      if (args.some((a) => String(a).includes("server.js"))) {
        // web 的執行檔找不到：Node 以 'error' 事件回報，不是 'exit'。
        queueMicrotask(() => child.emit("error", new Error("spawn node ENOENT")));
      } else {
        h.children[args.includes("worker") ? "worker" : "api"] = child;
      }
      return child;
    };

    const err = await supervise({
      env: {},
      paths: h.paths,
      version: "1.0.0",
      spawnImpl,
      fetchImpl: h.fetchImpl,
      killImpl: (pid, signal) => killed.push([pid, signal]),
      platform: "darwin",
      now: h.clock.now,
      sleepImpl: h.clock.sleepImpl,
      signalSource: new EventEmitter(),
      workerGraceMs: 0,
    }).catch((e) => e);

    expect(err?.name).toBe("CliError");
    expect(ExitCode[err.code]).toBe(ExitCode.START_TIMEOUT);
    expect(err.message).toContain("ENOENT");
    // 已經起來的 api 與 worker 必須被收掉，否則它們會繼續佔著 8000。
    expect(killed.some(([, sig]) => sig === "SIGTERM")).toBe(true);
    await expect(fs.stat(h.paths.supervisorState)).rejects.toThrow();
  });

  it("② api 逾時 → 全體關閉 code=7 且印出 log 末 40 行", async () => {
    const h = makeHarness({ apiReady: false });
    const errorLogs = [];
    const spawnImpl = (cmd, args, opts) => {
      const child = h.spawnImpl(cmd, args, opts);
      if (args.some((a) => String(a).includes("serve_api.py"))) {
        setImmediate(() => child.stdout.emit("data", Buffer.from("api booting up\n")));
      }
      return child;
    };

    await expect(
      supervise({
        env: {},
        paths: h.paths,
        version: "1.0.0",
        spawnImpl,
        fetchImpl: h.fetchImpl,
        killImpl: h.killImpl,
        platform: "darwin",
        now: h.clock.now,
        sleepImpl: h.clock.sleepImpl,
        signalSource: new EventEmitter(),
        errorLog: (msg) => errorLogs.push(msg),
      }),
    ).rejects.toMatchObject({ code: "START_TIMEOUT" });

    expect(errorLogs.length).toBeGreaterThan(0);
  });

  it.each([
    ["別的程式回 404", { ok: false, status: 404, json: async () => ({ detail: "Not Found" }) }],
    ["別的程式回 200 但不是我們的 health", { ok: true, status: 200, json: async () => ({ ok: true }) }],
    ["別的程式回 200 但不是 JSON", { ok: true, status: 200, json: async () => JSON.parse("<html>") }],
    [
      "另一版 auto-ticket 的 API",
      { ok: true, status: 200, json: async () => ({ status: "ok", version: "0.9.0", broker_ok: true }) },
    ],
  ])("8000 上是%s → 不算就緒，worker 不會被提早啟動", async (_label, response) => {
    const h = makeHarness();
    const fetchImpl = async (url) => (url.includes("8000") ? response : h.fetchImpl(url));

    await expect(
      supervise({
        env: {},
        paths: h.paths,
        version: "1.0.0",
        spawnImpl: h.spawnImpl,
        fetchImpl,
        killImpl: h.killImpl,
        platform: "darwin",
        now: h.clock.now,
        sleepImpl: h.clock.sleepImpl,
        signalSource: new EventEmitter(),
        errorLog: () => {},
      }),
    ).rejects.toMatchObject({ code: "START_TIMEOUT" });

    expect(h.children.worker).toHaveLength(0);
  });

  it("⑤ 既有 pid 存活時第二個實例 code=8", async () => {
    const h = makeHarness();
    await fs.mkdir(h.paths.state, { recursive: true });
    await fs.writeFile(h.paths.supervisorState, JSON.stringify({ pid: 42, version: "1.0.0" }), "utf8");
    const killImpl = (pid, signal) => {
      if (signal === 0) return; // 假裝該 pid 還活著
    };

    await expect(
      supervise({
        env: {},
        paths: h.paths,
        version: "1.0.0",
        spawnImpl: h.spawnImpl,
        fetchImpl: h.fetchImpl,
        killImpl,
        platform: "darwin",
        signalSource: new EventEmitter(),
      }),
    ).rejects.toMatchObject({ code: "ALREADY_RUNNING" });

    expect(h.spawnCalls.length).toBe(0);
  });

  it("③ 就緒後崩潰 → 重啟 3 次後致命", async () => {
    const h = makeHarness();
    let attachedResolve;
    const attached = new Promise((r) => (attachedResolve = r));
    const signalSource = new EventEmitter();

    const supervisePromise = supervise({
      env: {},
      paths: h.paths,
      version: "1.0.0",
      spawnImpl: h.spawnImpl,
      fetchImpl: h.fetchImpl,
      killImpl: h.killImpl,
      platform: "darwin",
      now: h.clock.now,
      sleepImpl: h.clock.sleepImpl,
      signalSource,
      workerGraceMs: 0,
      onListenersAttached: () => attachedResolve(),
    });

    await attached;
    // 就緒判定發生在 clock=0；把假時鐘往後撥超過 10s，讓崩潰被視為「就緒後崩潰」而非啟動早夭。
    h.clock.state.t += 20_000;

    for (let i = 0; i < 4; i += 1) {
      const current = h.children.worker.at(-1);
      current.emit("exit", 1);
      await nextTick(5);
    }

    const exitCode = await supervisePromise;
    expect(exitCode).toBe(1);
    expect(h.children.worker.length).toBe(4); // 初次 + 3 次重啟
  });
});
