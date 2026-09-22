// 啟動前置：組裝子行程共用環境變數、固定埠位預檢、Playwright Chromium 安裝。
// 這是領域模組，不得 import command.mjs 或其他領域模組；所有共用常數／路徑一律由呼叫端傳入。
import { existsSync } from "node:fs";
import path from "node:path";
import net from "node:net";

// 領域模組不 import `command.mjs`——那會形成循環，也會讓依賴方向失去單一走向。
// 錯誤只帶 `code`，由 `command.mjs` 負責對映結束碼；`name` 讓它在那邊被認出來。
function cliError(code, message) {
  const err = new Error(message);
  err.name = "CliError";
  err.code = code;
  return err;
}

// 首版固定埠位，不開放覆寫（見計畫「技術方案 §5」）。
export const DEFAULT_PORTS = Object.freeze({ api: 8000, web: 3000 });

/**
 * 組出三個子行程共用的環境變數基底。純函式：只讀 `paths`，不做任何 I/O。
 */
/**
 * 三個子行程共用的環境基底。
 *
 * `PYTHONPATH` 是這裡最要緊的一條：runtime 刻意不建 venv（venv 會把建置機的絕對
 * 路徑寫進 `pyvenv.cfg` 與 shebang），import 路徑完全靠它組出來。少了它，
 * `serve_api.py` 會在 `import uvicorn` 當場死掉。
 */
export function buildEnv({ paths, runtimeDir, delimiter = path.delimiter, extra = {} } = {}) {
  const env = {
    AUTO_TICKET_DB_PATH: paths.db,
    AUTO_TICKET_SCREENSHOT_DIR: paths.screenshots,
    AUTO_TICKET_VAULT_ROOT: paths.credentials,
    AUTO_TICKET_TIMELINE_DIR: paths.timelines,
    AUTO_TICKET_BROWSER_PROFILE_ROOT: paths.browserProfiles,
    AUTO_TICKET_CORS_ORIGINS: `http://127.0.0.1:${DEFAULT_PORTS.web},http://localhost:${DEFAULT_PORTS.web}`,
    PLAYWRIGHT_BROWSERS_PATH: paths.msPlaywright,
    PYTHONNOUSERSITE: "1",
    PYTHONUTF8: "1",
    ...(runtimeDir
      ? {
          PYTHONPATH: [
            path.join(runtimeDir, "site-packages"),
            path.join(runtimeDir, "app", "src"),
          ].join(delimiter),
        }
      : {}),
    ...extra,
  };
  return env;
}

/**
 * 啟動前以真的 listen 探測固定埠位是否可用。被佔用即中止，
 * 由呼叫端（command.mjs 的 start）決定要不要真的中止；`doctor` 只是捕捉這個錯誤來報告。
 */
export async function assertPortsFree({
  ports = DEFAULT_PORTS,
  host = "127.0.0.1",
  netImpl = net,
} = {}) {
  for (const [name, port] of Object.entries(ports)) {
    await assertOnePortFree({ name, port, host, netImpl });
  }
}

function assertOnePortFree({ name, port, host, netImpl }) {
  return new Promise((resolve, reject) => {
    const server = netImpl.createServer();
    server.once("error", (err) => {
      if (err && err.code === "EADDRINUSE") {
        reject(
          cliError(
            "PORT_IN_USE",
            `埠 ${port}（${name}）已被佔用，請關閉佔用該埠的行程後重試。`,
          ),
        );
        return;
      }
      reject(err);
    });
    server.listen(port, host, () => {
      server.close(() => resolve());
    });
  });
}

/**
 * 確保 Playwright Chromium 已安裝於 `~/.auto-ticket/ms-playwright`。
 * 可重入：已安裝時第二次呼叫是 no-op，靠 `existsImpl` 檢查安裝標記目錄。
 */
export async function ensureBrowsers({
  paths,
  pythonPath,
  spawnImpl,
  existsImpl = existsSync,
} = {}) {
  if (existsImpl(paths.msPlaywright)) {
    return { installed: true, skipped: true };
  }
  const run = spawnImpl ?? defaultSpawnSync;
  await run(pythonPath ?? "python3", ["-m", "playwright", "install", "chromium"], {
    env: { ...process.env, PLAYWRIGHT_BROWSERS_PATH: paths.msPlaywright },
  });
  return { installed: true, skipped: false };
}

function defaultSpawnSync(cmd, args, opts) {
  return new Promise((resolve, reject) => {
    import("node:child_process").then(({ spawn }) => {
      const child = spawn(cmd, args, { ...opts, shell: false });
      child.on("error", reject);
      child.on("exit", (code) => {
        if (code === 0) resolve();
        else reject(new Error(`${cmd} 結束碼 ${code}`));
      });
    });
  });
}
