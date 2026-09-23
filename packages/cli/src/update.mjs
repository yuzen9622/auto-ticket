// CLI 自我更新：查 registry 最新版 → 找出當初用哪個套件管理工具全域安裝 → 交給它重裝指定版本 → 回讀驗證。
// 領域模組，不得 import runtime-cache.mjs / migration.mjs / host.mjs / supervisor.mjs。
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

export const PACKAGE_NAME = "@yuzen9622/auto-ticket";
export const REGISTRY_LATEST_URL = "https://registry.npmjs.org/@yuzen9622%2Fauto-ticket/latest";
const REGISTRY_TIMEOUT_MS = 15_000;

const SEMVER = /^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$/;

/** 回傳負數／0／正數。只處理這個套件實際會發的 x.y.z 與 x.y.z-pre，不打算當通用 semver。 */
export function compareVersions(a, b) {
  const pa = SEMVER.exec(a);
  const pb = SEMVER.exec(b);
  if (!pa || !pb) throw new Error(`無法比較版本：${a} / ${b}`);
  for (let i = 1; i <= 3; i += 1) {
    const diff = Number(pa[i]) - Number(pb[i]);
    if (diff !== 0) return diff;
  }
  // 同一個 x.y.z，正式版大於任何預發版。
  if (!pa[4] && !pb[4]) return 0;
  if (!pa[4]) return 1;
  if (!pb[4]) return -1;
  return pa[4].localeCompare(pb[4], "en", { numeric: true });
}

/** 問 registry 的 `latest` dist-tag。只讀，不碰本機任何東西。 */
export async function fetchLatestVersion({
  fetchImpl = fetch,
  url = REGISTRY_LATEST_URL,
  timeoutMs = REGISTRY_TIMEOUT_MS,
} = {}) {
  let res;
  try {
    res = await fetchImpl(url, {
      headers: { accept: "application/json" },
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (err) {
    throw cliError("UPDATE_CHECK_FAILED", `無法連線到 npm registry 檢查最新版本：${err.message}`);
  }
  if (!res.ok) {
    throw cliError(
      "UPDATE_CHECK_FAILED",
      `npm registry 回應 HTTP ${res.status}，無法取得 ${PACKAGE_NAME} 的最新版本。`,
    );
  }
  const body = await res.json().catch(() => null);
  const version = body?.version;
  if (typeof version !== "string" || !SEMVER.test(version)) {
    throw cliError("UPDATE_CHECK_FAILED", "npm registry 回傳的版本資訊格式不符，已中止更新。");
  }
  return version;
}

/**
 * 三種受支援的全域安裝方式。`rootArgs` 問出該工具的全域目錄，
 * `packageDir` 從那裡推回本套件的位置——偵測與事後驗證走同一條路，才不會各說各話。
 */
export const INSTALLERS = Object.freeze([
  {
    name: "npm",
    cmd: "npm",
    rootArgs: ["root", "-g"],
    packageDir: (root) => path.join(root, PACKAGE_NAME),
    installArgs: (version) => ["install", "-g", `${PACKAGE_NAME}@${version}`],
  },
  {
    name: "pnpm",
    cmd: "pnpm",
    rootArgs: ["root", "-g"],
    packageDir: (root) => path.join(root, PACKAGE_NAME),
    installArgs: (version) => ["add", "-g", `${PACKAGE_NAME}@${version}`],
  },
  {
    name: "yarn",
    cmd: "yarn",
    rootArgs: ["global", "dir"],
    packageDir: (root) => path.join(root, "node_modules", PACKAGE_NAME),
    installArgs: (version) => ["global", "add", `${PACKAGE_NAME}@${version}`],
  },
]);

export function formatInstallCommand(installer, version) {
  return [installer.cmd, ...installer.installArgs(version)].join(" ");
}

/**
 * 跑一個套件管理工具指令。Windows 上三者都是 .cmd 檔，Node 不經 shell 無法直接執行；
 * 參數全是固定字串加上通過 SEMVER 檢查的版本號，交給 shell 不會有注入空間。
 */
export function runCommand(cmd, args, { platform = process.platform, spawnImpl = nodeSpawn, inherit = false } = {}) {
  return new Promise((resolve) => {
    let stdout = "";
    let stderr = "";
    let child;
    try {
      child = spawnImpl(cmd, args, {
        shell: platform === "win32",
        stdio: inherit ? "inherit" : ["ignore", "pipe", "pipe"],
        windowsHide: true,
      });
    } catch (err) {
      resolve({ code: null, stdout, stderr, error: err });
      return;
    }
    child.stdout?.on("data", (d) => (stdout += d));
    child.stderr?.on("data", (d) => (stderr += d));
    child.on("error", (error) => resolve({ code: null, stdout, stderr, error }));
    child.on("close", (code) => resolve({ code, stdout, stderr, error: null }));
  });
}

function samePath(a, b, platform) {
  return platform === "win32" ? a.toLowerCase() === b.toLowerCase() : a === b;
}

async function realpathOrNull(p, fsImpl) {
  try {
    return await fsImpl.realpath(p);
  } catch {
    return null;
  }
}

/** 名稱帶出線索的工具先問，其餘照順序；問不到（沒裝）的直接跳過。 */
function probeOrder(realRoot) {
  const lower = realRoot.toLowerCase();
  const hinted = INSTALLERS.filter((i) => i.name !== "npm" && lower.includes(i.name));
  return [...hinted, ...INSTALLERS.filter((i) => !hinted.includes(i))];
}

/**
 * 判斷正在執行的這份 CLI 是被哪個工具全域安裝的。判準是「該工具全域目錄裡的本套件」
 * 解析後就是自己所在的目錄——只看路徑字樣會把專案本地依賴誤認成全域安裝，
 * 然後 `npm install -g` 裝了一份沒人會執行的新版。
 */
export async function detectInstallation({
  packageRoot,
  platform = process.platform,
  runImpl = runCommand,
  fsImpl = fs,
}) {
  const realRoot = (await realpathOrNull(packageRoot, fsImpl)) ?? packageRoot;
  const segments = realRoot.split(/[\\/]+/);
  if (segments.includes("_npx")) return { installer: null, kind: "npx", packageRoot: realRoot };
  if (!segments.includes("node_modules")) return { installer: null, kind: "source", packageRoot: realRoot };

  for (const installer of probeOrder(realRoot)) {
    const res = await runImpl(installer.cmd, installer.rootArgs, { platform });
    if (res.code !== 0) continue;
    const root = res.stdout.trim().split(/\r?\n/).pop()?.trim();
    if (!root) continue;
    const dir = await realpathOrNull(installer.packageDir(root), fsImpl);
    if (dir && samePath(dir, realRoot, platform)) {
      return { installer, kind: "global", packageRoot: realRoot, packageDir: installer.packageDir(root) };
    }
  }
  return { installer: null, kind: "unknown", packageRoot: realRoot };
}

async function readInstalledVersion(packageDir, fsImpl) {
  try {
    return JSON.parse(await fsImpl.readFile(path.join(packageDir, "package.json"), "utf8")).version ?? null;
  } catch {
    return null;
  }
}

function unsupportedMessage(detection, latest) {
  const manual = INSTALLERS.map((i) => `  ${formatInstallCommand(i, latest)}`).join("\n");
  switch (detection.kind) {
    case "npx":
      return (
        "目前是透過 npx 暫時執行，沒有可以原地更新的安裝。\n" +
        `改用 npx ${PACKAGE_NAME}@latest 取得最新版，或全域安裝後即可使用 autix update：\n${manual}`
      );
    case "source":
      return `目前是從原始碼目錄執行（${detection.packageRoot}），請以 git pull 更新。`;
    default:
      return (
        `找不到安裝這份 CLI 的全域套件管理工具（位置：${detection.packageRoot}）。\n` +
        `請用當初安裝的工具手動更新：\n${manual}`
      );
  }
}

/**
 * 整個更新流程。任何在交給套件管理工具之前的失敗都不會動到本機；
 * 交出去之後以該工具自己的交易語意為準，結束後一律回讀全域目錄確認實際落地的版本，
 * 失敗時把「現在裝的是哪一版」講清楚，不讓使用者猜自己是否卡在半途。
 */
export async function update({
  currentVersion,
  packageRoot,
  log = console.log,
  platform = process.platform,
  fetchImpl = fetch,
  runImpl = runCommand,
  fsImpl = fs,
}) {
  log(`目前版本：${currentVersion}`);
  log("正在檢查最新版本…");
  const latest = await fetchLatestVersion({ fetchImpl });
  log(`最新版本：${latest}`);

  const order = compareVersions(currentVersion, latest);
  if (order === 0) {
    log("已是最新版本，不需要更新。");
    return { status: "up-to-date", before: currentVersion, after: currentVersion };
  }
  if (order > 0) {
    log(`目前版本比 registry 上的最新版（${latest}）還新，不做降版。`);
    return { status: "ahead", before: currentVersion, after: currentVersion };
  }

  const detection = await detectInstallation({ packageRoot, platform, runImpl, fsImpl });
  if (!detection.installer) {
    throw cliError("UPDATE_UNSUPPORTED", unsupportedMessage(detection, latest));
  }

  const { installer, packageDir } = detection;
  const command = formatInstallCommand(installer, latest);
  log(`以 ${installer.name} 更新：${command}`);
  const res = await runImpl(installer.cmd, installer.installArgs(latest), { platform, inherit: true });
  const installed = await readInstalledVersion(packageDir, fsImpl);

  if (res.code !== 0) {
    const reason = res.error ? res.error.message : `結束碼 ${res.code}`;
    throw cliError(
      "UPDATE_FAILED",
      `${installer.name} 更新失敗（${reason}）。目前安裝的版本：${installed ?? "無法讀取"}。\n` +
        `排除問題（網路、權限）後可手動重試：${command}`,
    );
  }
  if (installed !== latest) {
    throw cliError(
      "UPDATE_FAILED",
      `${installer.name} 回報成功，但 ${packageDir} 的版本是 ${installed ?? "無法讀取"}，不是 ${latest}。\n` +
        `請手動重試：${command}`,
    );
  }

  log(`更新完成：${currentVersion} → ${installed}`);
  log("新版的 runtime 會在下次 autix start 時下載；要提前下載可執行 autix runtime install。");
  return { status: "updated", before: currentVersion, after: installed };
}
