// 子行程生命週期：spawn、啟動序、readiness、重啟退避、log 前綴轉發與落檔、優雅關閉。
// 領域模組，不得 import runtime-cache.mjs / migration.mjs / host.mjs。
import { spawn as nodeSpawn } from "node:child_process";
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

const MAX_LOG_BYTES = 50 * 1024 * 1024;
const RESTART_BACKOFF_MS = [1000, 3000, 9000];
const MAX_RESTARTS = 3;
const FATAL_EARLY_EXIT_MS = 10_000;
const WORKER_READY_BANNER = "Auto-Ticket Worker 已就緒";
const WORKER_READY_GRACE_MS = 10_000;

function defaultSleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** 啟動一個子行程；POSIX 下用獨立 process group，方便關閉時整組收掉。 */
export function spawnChild({ cmd, args = [], env, cwd, spawnImpl = nodeSpawn, platform = process.platform }) {
  return spawnImpl(cmd, args, {
    env,
    cwd,
    shell: false,
    windowsHide: true,
    detached: platform !== "win32",
  });
}

/**
 * 輪詢 checkImpl() 直到回傳 true 或逾時。checkImpl 可以是同步或回傳 Promise。
 */
export async function waitReady({
  checkImpl,
  timeoutMs,
  intervalMs = 250,
  sleepImpl = defaultSleep,
  now = () => Date.now(),
}) {
  const deadline = now() + timeoutMs;
  for (;;) {
    let ok = false;
    try {
      ok = await checkImpl();
    } catch {
      ok = false;
    }
    if (ok) return true;
    if (now() >= deadline) {
      throw cliError("START_TIMEOUT", `等待就緒逾時（${timeoutMs}ms）。`);
    }
    await sleepImpl(intervalMs);
  }
}

/**
 * 建立一個 log sink：同時前綴轉發到終端機、append 落檔。
 * 超過 50MB 只在終端機與 launcher.log 印一行告警，不自動改名、不自動刪除。
 */
export async function openLogSink({
  name,
  logPath,
  launcherLogPath,
  fsImpl = fs,
  stdoutImpl = process.stdout,
  maxBytes = MAX_LOG_BYTES,
}) {
  await fsImpl.mkdir(path.dirname(logPath), { recursive: true });
  const stat = await fsImpl.stat(logPath).catch(() => null);
  if (stat && stat.size > maxBytes) {
    const warning = `[launcher] 警告：${name}.log 已超過 50MB（目前 ${stat.size} bytes），請手動清理，本次不自動改名或刪除。\n`;
    stdoutImpl.write(warning);
    if (launcherLogPath) {
      await fsImpl.appendFile(launcherLogPath, warning, "utf8").catch(() => {});
    }
  }
  return {
    write: async (line) => {
      const prefixed = `[${name}] ${line}`;
      stdoutImpl.write(`${prefixed}\n`);
      await fsImpl.appendFile(logPath, `${prefixed}\n`, "utf8").catch(() => {});
    },
  };
}

function attachLogging(child, sink) {
  const lastLines = [];
  const capture = (chunk) => {
    const text = chunk.toString();
    for (const line of text.split(/\r?\n/).filter(Boolean)) {
      lastLines.push(line);
      if (lastLines.length > 40) lastLines.shift();
      sink.write(line);
    }
  };
  child.stdout?.on?.("data", capture);
  child.stderr?.on?.("data", capture);
  return lastLines;
}

/**
 * 優雅關閉一組子行程，順序由呼叫端決定（預設反向：web → worker → api）。
 * POSIX：對 process group 送 SIGTERM，寬限 graceMs 後 SIGKILL。
 * Windows：呼叫 `taskkill /pid <pid> /T /F` 遞迴終止行程樹。
 */
export async function shutdown({
  children,
  platform = process.platform,
  graceMs = 10_000,
  killImpl = process.kill,
  spawnImpl = nodeSpawn,
  sleepImpl = defaultSleep,
  now = () => Date.now(),
}) {
  for (const { child } of children) {
    if (!child || child.pid == null || child.killed) continue;
    if (platform === "win32") {
      await new Promise((resolve) => {
        const tk = spawnImpl("taskkill", ["/pid", String(child.pid), "/T", "/F"], {
          shell: false,
        });
        tk.once("exit", resolve);
        tk.once("error", resolve);
      });
    } else {
      try {
        killImpl(-child.pid, "SIGTERM");
      } catch {
        // 行程可能已經結束，忽略。
      }
    }
  }

  if (platform !== "win32") {
    const stillAlive = () =>
      children.some(({ child }) => child && child.pid != null && !child.killed && child.exitCode == null);
    const deadline = now() + graceMs;
    while (stillAlive() && now() < deadline) {
      await sleepImpl(200);
    }
    for (const { child } of children) {
      if (child && child.pid != null && !child.killed && child.exitCode == null) {
        try {
          killImpl(-child.pid, "SIGKILL");
        } catch {
          // 忽略。
        }
      }
    }
  }
}

async function readSupervisorState(paths, fsImpl) {
  const raw = await fsImpl.readFile(paths.supervisorState, "utf8").catch(() => null);
  return raw ? JSON.parse(raw) : null;
}

function isPidAlive(pid, killImpl) {
  try {
    killImpl(pid, 0);
    return true;
  } catch {
    return false;
  }
}

/** 目前在跑的 supervisor pid；沒有、已結束或狀態檔讀不懂都回傳 null。 */
export async function findRunningInstance({ paths, fsImpl = fs, killImpl = process.kill }) {
  const existing = await readSupervisorState(paths, fsImpl).catch(() => null);
  return existing && isPidAlive(existing.pid, killImpl) ? existing.pid : null;
}

/**
 * 三個子行程的完整啟動序與監督迴圈：api → worker → web，各自等就緒才進下一階段。
 * 任一階段逾時：印出該行程 log 末 40 行、關閉全部已啟動行程、回傳結束碼 7。
 * 就緒後崩潰：退避重啟最多 3 次，第 4 次致命。
 */
export async function supervise({
  env,
  paths,
  version,
  headed = false,
  spawnImpl = nodeSpawn,
  fetchImpl = fetch,
  fsImpl = fs,
  sleepImpl = defaultSleep,
  killImpl = process.kill,
  platform = process.platform,
  signalSource = process,
  pythonPath = "python3",
  // 用執行這支 CLI 的那個 Node，而不是靠子行程的 PATH 去找 "node"——
  // env 是白名單組出來的，賭 PATH 一定在只會換來 ENOENT。
  nodePath = process.execPath,
  runtimeDir: runtimeDirOverride,
  // 埠位由 command.mjs 從 host.mjs 的凍結常數傳進來（領域模組之間不互相 import）。
  // CLI 沒有任何旗標能改它；可注入純粹是為了讓整合測試不必霸佔 8000/3000。
  ports = { api: 8000, web: 3000 },
  now = () => Date.now(),
  errorLog = console.error,
  workerGraceMs = WORKER_READY_GRACE_MS,
  onListenersAttached = () => {},
}) {
  const existing = await readSupervisorState(paths, fsImpl);
  if (existing && isPidAlive(existing.pid, killImpl)) {
    throw cliError(
      "ALREADY_RUNNING",
      `已有 auto-ticket 實例在執行中（pid=${existing.pid}）。`,
    );
  }

  // 旁路（AUTO_TICKET_RUNTIME_DIR）給的目錄不在預設版本佈局下，所以由呼叫端傳入。
  const runtimeDir = runtimeDirOverride ?? paths.runtimeDir(version);
  const started = [];

  async function startOne({ name, cmd, args, timeoutMs, makeCheck, extraEnv }) {
    const sink = await openLogSink({
      name,
      logPath: paths.logFile(name),
      launcherLogPath: paths.logFile("launcher"),
      fsImpl,
    });
    const child = spawnImpl(cmd, args, {
      env: extraEnv ? { ...env, ...extraEnv } : env,
      shell: false,
      windowsHide: true,
      detached: platform !== "win32",
    });
    const lastLines = attachLogging(child, sink);
    // spawn 失敗（例如執行檔不存在）走的是 'error' 事件，不是 'exit'。沒有人接的話
    // 它會變成 unhandled error 直接炸掉 supervisor，繞過下面的關閉流程——已經起來的
    // api/worker 就變成孤兒，繼續佔著 8000，下次啟動被自己擋在門外。
    const spawnFailed = new Promise((_, reject) => {
      child.once("error", (err) =>
        reject(cliError("START_TIMEOUT", `${name} 啟動失敗：${err.message}`)),
      );
    });
    const startedAt = now();
    const checkImpl = makeCheck({ lastLines, startedAt });
    try {
      await Promise.race([
        waitReady({ checkImpl, timeoutMs, sleepImpl, now }),
        spawnFailed,
      ]);
    } catch (err) {
      errorLog(lastLines.slice(-40).join("\n"));
      await shutdown({ children: [...started, { name, child }].reverse(), platform, killImpl, spawnImpl, sleepImpl, now });
      throw err;
    }
    const entry = { name, child, spec: { cmd, args, timeoutMs, makeCheck, extraEnv }, restarts: 0, startedAt };
    started.push(entry);
    return entry;
  }

  const httpOk = (url) => () => async () => {
    try {
      const res = await fetchImpl(url);
      return res.ok || res.status < 500;
    } catch {
      return false;
    }
  };

  // 只看狀態碼不夠：Windows 上別的程式若綁在 0.0.0.0:8000，我們的 API 還在建表時，
  // 127.0.0.1:8000 會先由它回應（連 404 都算 <500），worker 就被提早放行、跟 API
  // 搶著建表。要回應長得像我們的 /healthz、版本也對得上，才算是自己的 API 起來了。
  // 旁路目錄的版本不受 package.json 管，只驗形狀。
  const apiHealthy = (url) => () => async () => {
    try {
      const res = await fetchImpl(url);
      if (!res.ok) return false;
      const body = await res.json();
      if (typeof body?.version !== "string" || typeof body?.broker_ok !== "boolean") return false;
      return runtimeDirOverride != null || body.version === version;
    } catch {
      return false;
    }
  };

  await startOne({
    name: "api",
    cmd: pythonPath,
    args: [
      "-s",
      path.join(runtimeDir, "app", "scripts", "serve_api.py"),
      "--host",
      "127.0.0.1",
      "--port",
      String(ports.api),
    ],
    timeoutMs: 90_000,
    makeCheck: apiHealthy(`http://127.0.0.1:${ports.api}/healthz`),
  });

  await startOne({
    name: "worker",
    cmd: pythonPath,
    args: ["-s", "-m", "worker"],
    timeoutMs: 90_000,
    // 有 banner 就用 banner；沒有 banner 的話，存活到 grace 期滿也算就緒（見計畫 §8）。
    makeCheck: ({ lastLines, startedAt }) => () =>
      lastLines.some((line) => line.includes(WORKER_READY_BANNER)) ||
      now() - startedAt >= workerGraceMs,
  });

  await startOne({
    name: "web",
    cmd: nodePath,
    args: [path.join(runtimeDir, "web", "server.js")],
    // Next standalone 的 server.js 預設綁 0.0.0.0，那會把儀表板開到整個區網。
    // 這是 local-first 的硬要求，不是預設值的偏好問題。
    extraEnv: { PORT: String(ports.web), HOSTNAME: "127.0.0.1" },
    timeoutMs: 60_000,
    makeCheck: httpOk(`http://127.0.0.1:${ports.web}/`),
  });

  await fsImpl.mkdir(paths.state, { recursive: true });
  await fsImpl.writeFile(
    paths.supervisorState,
    JSON.stringify({ pid: process.pid, version, startedAt: now() }, null, 2),
    "utf8",
  );

  let shuttingDown = false;
  const finalExitCode = await new Promise((resolve) => {
    const onSignal = async () => {
      if (shuttingDown) return;
      shuttingDown = true;
      await shutdown({ children: [...started].reverse(), platform, killImpl, spawnImpl, sleepImpl, now });
      await fsImpl.rm(paths.supervisorState, { force: true }).catch(() => {});
      resolve(0);
    };
    signalSource.once?.("SIGINT", onSignal);
    signalSource.once?.("SIGTERM", onSignal);

    onListenersAttached();

    for (const entry of started) {
      entry.child.once("exit", async function onExit(code) {
        if (shuttingDown) return;
        if (now() - (entry.startedAt ?? 0) < FATAL_EARLY_EXIT_MS) {
          shuttingDown = true;
          await shutdown({ children: started.filter((e) => e !== entry).reverse(), platform, killImpl, spawnImpl, sleepImpl, now });
          resolve(1);
          return;
        }
        if (entry.restarts >= MAX_RESTARTS) {
          shuttingDown = true;
          await shutdown({ children: started.filter((e) => e !== entry).reverse(), platform, killImpl, spawnImpl, sleepImpl, now });
          resolve(1);
          return;
        }
        entry.restarts += 1;
        await sleepImpl(RESTART_BACKOFF_MS[entry.restarts - 1] ?? RESTART_BACKOFF_MS.at(-1));
        const restarted = spawnImpl(entry.spec.cmd, entry.spec.args, {
          env,
          shell: false,
          windowsHide: true,
          detached: platform !== "win32",
        });
        entry.child = restarted;
        restarted.once("exit", onExit);
      });
    }
  });

  return finalExitCode;
}
