// 指令路由 + 旗標解析 + 結束碼／錯誤型別 + target 解析 + 本機路徑 + manifest 驗證 + doctor。
// 這裡是唯一允許 import 四個領域模組（runtime-cache / migration / host / supervisor）的檔案，
// 依賴方向固定由此向外，四個領域模組彼此不得互相 import。
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import fsPromises, { readFile, statfs } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import * as hostMod from "./host.mjs";
import * as migrationMod from "./migration.mjs";
import * as runtimeCacheMod from "./runtime-cache.mjs";
import * as supervisorMod from "./supervisor.mjs";
import * as uiMod from "./ui.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PACKAGE_ROOT = path.resolve(__dirname, "..");

// 結束碼表：對外承諾的行為契約，見 .pi/plans 的「驗收條件」。
export const ExitCode = Object.freeze({
  SUCCESS: 0,
  UNKNOWN_FLAG: 1,
  UNSUPPORTED_PLATFORM: 2,
  DOWNLOAD_FAILED: 3,
  MANIFEST_MISMATCH: 4,
  VERIFY_FAILED: 5,
  MIGRATION_CONFLICT: 6,
  START_TIMEOUT: 7,
  ALREADY_RUNNING: 8,
  CHROME_MISSING: 9,
  PORT_IN_USE: 10,
});

export class CliError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "CliError";
    this.code = code;
    this.exitCode = ExitCode[code] ?? ExitCode.UNKNOWN_FLAG;
  }

  /**
   * 以外形辨識而非 `instanceof`。四個領域模組不 import 這個檔——那會形成循環——
   * 所以它們丟出的是帶 `code` 的一般 Error。對映結束碼的責任在這裡，不在它們身上。
   */
  static matches(err) {
    return err?.name === "CliError" && typeof err?.code === "string";
  }

  static exitCodeOf(err) {
    return err?.exitCode ?? ExitCode[err?.code] ?? ExitCode.UNKNOWN_FLAG;
  }
}

/**
 * 依 process.platform / process.arch 解析目標三元組。
 * Rosetta 下的 x64 Node 不當成原生執行：那是轉譯，時序特性失真，
 * 必須明確引導使用者換原生 arm64 Node，而不是默默下載 x64 runtime 將就。
 */
export function resolveTarget({
  platform = process.platform,
  arch = process.arch,
  sysctlImpl,
} = {}) {
  if (platform === "darwin") {
    if (arch === "arm64") return "darwin-arm64";
    if (arch === "x64") {
      // Apple Silicon 上跑 x64 Node（Rosetta）與真的 Intel Mac 長得一模一樣，
      // 但前者的機器其實是支援的——值得分開講，否則使用者會以為自己的 Mac 不能用。
      if (isRunningUnderRosetta(sysctlImpl)) {
        throw new CliError(
          "UNSUPPORTED_PLATFORM",
          "偵測到目前的 Node 透過 Rosetta 在 Apple Silicon 上轉譯執行。" +
            "請改安裝原生 arm64 版本的 Node 後再執行 auto-ticket，" +
            "而不是繼續在 x64 runtime 上運作（會讓瀏覽器自動化與 OCR 的時序特性失真）。",
        );
      }
      throw new CliError(
        "UNSUPPORTED_PLATFORM",
        "首版不支援 Intel Mac：憑證加密用的 cryptography 自 49.0.0 起不再發行 " +
          "macOS x86_64 wheel，無法為這個平台組出可用的 runtime。",
      );
    }
  }
  if (platform === "win32" && arch === "x64") return "win32-x64";
  throw new CliError(
    "UNSUPPORTED_PLATFORM",
    `首版僅支援 macOS(Apple Silicon) 與 Windows x64，偵測到 ${platform}/${arch}。`,
  );
}

function isRunningUnderRosetta(sysctlImpl) {
  const impl = sysctlImpl ?? defaultSysctlProcTranslated;
  try {
    const out = impl();
    return String(out).trim() === "1";
  } catch {
    // sysctl 不存在或查不到這個 key，一律視為原生執行（沒有反面證據就不阻擋）。
    return false;
  }
}

function defaultSysctlProcTranslated() {
  return execFileSync("sysctl", ["-n", "sysctl.proc_translated"], {
    encoding: "utf8",
  });
}

/**
 * 本機目錄配置的單一事實來源。永遠以 os.homedir() 為根，
 * Windows 不得改用 %APPDATA%/%LOCALAPPDATA%，否則會跟 Python 端的
 * Path.home()/".auto-ticket" 指向不同位置。
 */
/**
 * runtime 自帶 CPython；整套發行的意義就在於不依賴使用者機器上的 Python。
 * 少了這一步，三個行程會悄悄跑在系統 python3 上，然後在 import 階段才炸。
 */
export function runtimePythonIn(runtimeDir, platform = process.platform) {
  return platform === "win32"
    ? path.join(runtimeDir, "python", "python.exe")
    : path.join(runtimeDir, "python", "bin", "python3");
}

export function paths({ homeDir = os.homedir(), platform = process.platform } = {}) {
  const root = path.join(homeDir, ".auto-ticket");
  const data = path.join(root, "data");
  const credentials = path.join(data, "credentials");
  const runtimeRoot = path.join(root, "runtime");
  return {
    root,
    data,
    db: path.join(data, "auto-ticket.db"),
    credentials,
    vaultKey: path.join(credentials, ".vault_key"),
    screenshots: path.join(data, "screenshots"),
    timelines: path.join(data, "timelines"),
    migratedMarker: path.join(data, ".migrated.json"),
    chromeProfile: path.join(root, "chrome-profile"),
    browserProfiles: path.join(root, "browser-profiles"),
    msPlaywright: path.join(root, "ms-playwright"),
    runtimeRoot,
    runtimeTmp: path.join(runtimeRoot, ".tmp"),
    runtimeDir: (version) => path.join(runtimeRoot, version),
    // runtime 自帶 CPython；整套發行的意義就在於不依賴使用者機器上的 Python。
    // 沒有這一條，三個行程會悄悄跑在系統 python3 上，然後在 import 階段才炸。
    runtimePython: (version) => runtimePythonIn(path.join(runtimeRoot, version), platform),
    logs: path.join(root, "logs"),
    logFile: (name) => path.join(root, "logs", `${name}.log`),
    state: path.join(root, "state"),
    supervisorState: path.join(root, "state", "supervisor.json"),
  };
}

/**
 * 驗證隨包出貨的 runtime-manifest.json：版本必須與 package.json 一致，
 * 且必須含有目前 target 的下載資訊。不符即代表套件被竄改或建置有誤。
 */
export function loadManifest(manifest, { packageVersion, target }) {
  if (manifest.version !== packageVersion) {
    throw new CliError(
      "MANIFEST_MISMATCH",
      `runtime-manifest.json 版本（${manifest.version}）與套件版本（${packageVersion}）不一致，套件可能被竄改或建置有誤。`,
    );
  }
  const entry = manifest.targets?.[target];
  if (!entry) {
    throw new CliError(
      "MANIFEST_MISMATCH",
      `runtime-manifest.json 缺少目前平台 ${target} 的下載資訊。`,
    );
  }
  return entry;
}

async function readPackageVersion(fsImpl = { readFile }) {
  const raw = await fsImpl.readFile(
    path.join(PACKAGE_ROOT, "package.json"),
    "utf8",
  );
  return JSON.parse(raw).version;
}

/** 已安裝 runtime 的 MANIFEST.json；沒裝就是 null，不是錯誤。 */
async function readInstalledManifest(p, version, fsImpl = { readFile }) {
  try {
    return JSON.parse(
      await fsImpl.readFile(
        path.join(p.runtimeDir(version), "MANIFEST.json"),
        "utf8",
      ),
    );
  } catch {
    return null;
  }
}

export function formatVersion({ packageVersion, manifest, installed, paths: p }) {
  const python = installed?.python ?? manifest.python;
  const ort = installed?.onnxruntime ?? manifest.onnxruntime;
  return [
    `auto-ticket ${packageVersion}`,
    installed
      ? `runtime ${installed.version} (${p.runtimeDir(packageVersion)})`
      : `runtime ${manifest.version} (未安裝)`,
    `python=${python}`,
    `onnxruntime=${ort}`,
  ];
}

async function readRuntimeManifest(fsImpl = { readFile }) {
  const raw = await fsImpl.readFile(
    path.join(PACKAGE_ROOT, "runtime-manifest.json"),
    "utf8",
  );
  return JSON.parse(raw);
}

/**
 * 只讀診斷。刻意不接受任何會寫檔的 I/O 注入，呼叫端也不得在這個函式裡新增寫入呼叫——
 * `command.test.mjs` 會用 fs spy 斷言零寫入。
 */
export async function doctor({
  homeDir = os.homedir(),
  spawnImpl,
  existsImpl = existsSync,
  fsImpl = { readFile },
  statfsImpl = statfs,
  target,
} = {}) {
  const report = { platform: process.platform, arch: process.arch, node: process.version };
  const p = paths({ homeDir });

  try {
    report.target = target ?? resolveTarget();
  } catch (err) {
    report.target = null;
    report.targetError = err.message;
  }

  report.tar = checkTarAvailable(spawnImpl);
  report.chrome = { present: detectSystemChrome(existsImpl) };

  try {
    await hostMod.assertPortsFree({});
    report.ports = { ok: true };
  } catch (err) {
    report.ports = { ok: false, message: err.message };
  }

  try {
    const packageVersion = await readPackageVersion(fsImpl);
    const manifest = await readRuntimeManifest(fsImpl);
    const installed = await readInstalledManifest(p, packageVersion, fsImpl);
    report.runtime = {
      packageVersion,
      manifestVersion: manifest.version,
      installedPath: p.runtimeDir(packageVersion),
      installed: existsImpl(p.runtimeDir(packageVersion)),
      python: installed?.python ?? manifest.python,
    };
    // OCR 靠 onnxruntime 這個 native extension。Intel Mac 的 wheel 供給隨時可能
    // 斷掉，屆時使用者需要知道「不是壞了，是退回人工輸入驗證碼」。
    report.ocr = {
      onnxruntime: installed?.onnxruntime ?? manifest.onnxruntime,
      enabled: process.env.AUTO_TICKET_OCR_ENABLED !== "0",
      note: "設 AUTO_TICKET_OCR_ENABLED=0 可關閉 OCR，改以人工輸入驗證碼。",
    };
  } catch (err) {
    report.runtime = { error: err.message };
  }

  report.disk = await checkDiskSpace(p.root, statfsImpl);

  return report;
}

/** runtime 約 300MB、Chromium 約 150MB，再加上資料副本；1.5GB 是實測的下界。 */
export const REQUIRED_FREE_BYTES = 1.5 * 1024 ** 3;

async function checkDiskSpace(root, statfsImpl) {
  try {
    // 目錄可能還不存在，往上找到第一個存在的祖先再問。
    let probe = root;
    while (!existsSync(probe) && path.dirname(probe) !== probe) {
      probe = path.dirname(probe);
    }
    const stats = await statfsImpl(probe);
    const freeBytes = stats.bavail * stats.bsize;
    return {
      path: probe,
      freeBytes,
      requiredBytes: REQUIRED_FREE_BYTES,
      ok: freeBytes >= REQUIRED_FREE_BYTES,
    };
  } catch (err) {
    return { ok: null, message: err.message };
  }
}

function checkTarAvailable(spawnImpl) {
  try {
    const run = spawnImpl ?? ((cmd, args) => execFileSync(cmd, args, { encoding: "utf8" }));
    run("tar", ["--version"]);
    return { available: true };
  } catch (err) {
    return { available: false, message: err.message };
  }
}

function detectSystemChrome(existsImpl) {
  const candidates =
    process.platform === "darwin"
      ? ["/Applications/Google Chrome.app"]
      : process.platform === "win32"
        ? [
            "C:/Program Files/Google/Chrome/Application/chrome.exe",
            "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
          ]
        : [];
  return candidates.some((c) => {
    try {
      return existsImpl(c);
    } catch {
      return false;
    }
  });
}

/**
 * 搶票一律借用使用者本機的真 Chrome，沒有它整個流程走不到最後一步。
 * 擺在下載 300MB runtime 之前，讓缺件當場說清楚，而不是等到領任務才炸。
 */
export function assertChromePresent({ existsImpl = existsSync } = {}) {
  if (detectSystemChrome(existsImpl)) return;
  throw new CliError(
    "CHROME_MISSING",
    "找不到 Google Chrome。搶票必須借用你本機的真 Chrome，" +
      "請先安裝後再執行（安裝需求見 docs/INSTALL.md）。",
  );
}

/**
 * runtime 的兩個旁路，給 CI 與離線環境用：
 * `AUTO_TICKET_RUNTIME_DIR` 直接指向已解壓的目錄（完全跳過下載），
 * `AUTO_TICKET_RUNTIME_URL` 只換下載來源，雜湊仍以套件內釘選值為準。
 */
export function runtimeOverrides(env = process.env) {
  return {
    dir: env.AUTO_TICKET_RUNTIME_DIR || null,
    baseUrl: env.AUTO_TICKET_RUNTIME_URL || null,
  };
}

// 進度列上的階段名稱。`indeterminate` 表示這一步沒有位元組可數，只轉 spinner。
const RUNTIME_INSTALL_PHASES = {
  download: { label: "Downloading runtime", indeterminate: false },
  verify: { label: "Verifying SHA-256", indeterminate: true },
  extract: { label: "Extracting runtime", indeterminate: true },
  commit: { label: "Installing runtime", indeterminate: true },
};
/**
 * 取得 runtime 目錄：旁路優先，否則走下載與校驗。
 *
 * 這一步要搬 250MB 下來再解成 600MB，在慢速網路上是好幾分鐘。先前它整段靜默，
 * 使用者看到的是一個像當掉的終端機——進度回報不是裝飾，是這條路徑的必要輸出。
 */
async function resolveRuntimeDir({ manifest, entry, target, packageVersion, p, ui = uiMod }) {
  const override = runtimeOverrides();
  if (override.dir) return override.dir;

  const progress = ui.createProgress({
    label: `${RUNTIME_INSTALL_PHASES.download.label} ${packageVersion}`,
  });
  progress.start();
  try {
    const dir = await runtimeCacheMod.ensureRuntime({
      manifestEntry: entry,
      target,
      version: packageVersion,
      paths: p,
      baseUrl: override.baseUrl ?? manifest.baseUrl,
      onProgress: (update) => progress.update(update),
      onRetry: ({ attempt, maxRetries }) => {
        progress.reset();
        progress.setLabel(
          `${RUNTIME_INSTALL_PHASES.download.label} ${packageVersion} (retry ${attempt}/${maxRetries})`,
        );
      },
      onPhase: (phase) => {
        const step = RUNTIME_INSTALL_PHASES[phase];
        if (!step || phase === "download") return;
        progress.setLabel(step.label, { indeterminate: step.indeterminate });
      },
    });
    progress.done(`Runtime ${packageVersion} ready (${target})`);
    return dir;
  } catch (err) {
    progress.fail(`Runtime ${packageVersion} install failed`);
    throw err;
  }
}

/** 列出本機已安裝的 runtime 版本與各自佔用的位元組。只讀，不刪任何東西。 */
export async function listInstalledRuntimes(p, currentVersion, fsImpl = fsPromises) {
  let entries;
  try {
    entries = await fsImpl.readdir(p.runtimeRoot, { withFileTypes: true });
  } catch {
    return [];
  }
  const rows = [];
  for (const entry of entries) {
    if (!entry.isDirectory() || entry.name.startsWith(".")) continue;
    rows.push({
      version: entry.name,
      bytes: await dirSize(path.join(p.runtimeRoot, entry.name), fsImpl),
      inUse: entry.name === currentVersion,
    });
  }
  return rows.sort((a, b) => a.version.localeCompare(b.version));
}

async function dirSize(dir, fsImpl) {
  let total = 0;
  const stack = [dir];
  while (stack.length > 0) {
    const current = stack.pop();
    let entries;
    try {
      entries = await fsImpl.readdir(current, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) stack.push(full);
      else if (entry.isFile()) {
        const stat = await fsImpl.stat(full).catch(() => null);
        if (stat) total += stat.size;
      }
    }
  }
  return total;
}

export function formatBytes(bytes) {
  const units = ["B", "KiB", "MiB", "GiB"];
  let value = bytes;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

const KNOWN_COMMANDS = new Set(["start", "doctor", "runtime", "migrate", "logs", "version"]);
const RUNTIME_SUBCOMMANDS = new Set(["install", "path", "list"]);
const LOG_TARGETS = new Set(["api", "worker", "web"]);

/**
 * 解析 argv。旗標面刻意做小：固定埠位不開放 --api-port / --web-port，
 * 也沒有 `runtime clean`——這些都是這次修訂主動拿掉的功能，出現即視為未知旗標／子指令。
 */
export function parseArgs(argv) {
  const args = [...argv];
  let command = "start";
  if (args.length > 0 && KNOWN_COMMANDS.has(args[0])) {
    command = args.shift();
  } else if (args.length > 0 && args[0].startsWith("-") === false) {
    throw new CliError("UNKNOWN_FLAG", `未知的指令：${args[0]}`);
  }

  const flags = {};
  const positionals = [];

  if (command === "runtime") {
    const sub = args.shift();
    if (!sub || !RUNTIME_SUBCOMMANDS.has(sub)) {
      throw new CliError(
        "UNKNOWN_FLAG",
        `runtime 子指令僅支援 install/path/list，收到：${sub ?? "(無)"}。首版不提供 runtime clean。`,
      );
    }
    flags.subcommand = sub;
  }

  if (command === "logs") {
    if (args[0] && LOG_TARGETS.has(args[0])) {
      flags.target = args.shift();
    }
  }

  while (args.length > 0) {
    const arg = args.shift();
    if (!arg.startsWith("-")) {
      positionals.push(arg);
      continue;
    }
    switch (arg) {
      case "--headed":
        assertAllowed(command, "start", arg);
        flags.headed = true;
        break;
      case "--no-ocr":
        assertAllowed(command, "start", arg);
        flags.noOcr = true;
        break;
      case "--skip-migration":
        assertAllowed(command, "start", arg);
        flags.skipMigration = true;
        break;
      case "--from":
        assertAllowed(command, ["start", "migrate"], arg);
        flags.from = args.shift();
        break;
      case "--merge-missing":
        assertAllowed(command, "migrate", arg);
        flags.mergeMissing = true;
        break;
      case "--dry-run":
        assertAllowed(command, "migrate", arg);
        flags.dryRun = true;
        break;
      case "-n":
        assertAllowed(command, "logs", arg);
        flags.lines = Number(args.shift());
        break;
      case "-f":
        assertAllowed(command, "logs", arg);
        flags.follow = true;
        break;
      case "--api-port":
      case "--web-port":
        throw new CliError(
          "UNKNOWN_FLAG",
          `${arg} 不存在：首版固定 api=8000、web=3000，不提供埠位覆寫旗標。`,
        );
      default:
        throw new CliError("UNKNOWN_FLAG", `未知的旗標：${arg}`);
    }
  }

  return { command, flags, positionals };
}

function assertAllowed(command, allowed, flag) {
  const list = Array.isArray(allowed) ? allowed : [allowed];
  if (!list.includes(command)) {
    throw new CliError(
      "UNKNOWN_FLAG",
      `旗標 ${flag} 不適用於指令 ${command}。`,
    );
  }
}

export async function main(argv, io = {}) {
  const log = io.log ?? console.log;
  const errorLog = io.errorLog ?? console.error;
  // 顯示層可注入：整合測試要驗的是接線，不是終端機上長什麼樣。
  const ui = io.ui ?? uiMod;
  try {
    const { command, flags } = parseArgs(argv);
    switch (command) {
      case "doctor": {
        const report = await doctor({});
        log(JSON.stringify(report, null, 2));
        return ExitCode.SUCCESS;
      }
      case "version": {
        const packageVersion = await readPackageVersion();
        const manifest = await readRuntimeManifest();
        const p = paths();
        // 已安裝的 runtime 才是實際會跑的那一份；沒裝就報套件所釘選的版本。
        const installed = await readInstalledManifest(p, packageVersion);
        for (const line of formatVersion({ packageVersion, manifest, installed, paths: p })) {
          log(line);
        }
        return ExitCode.SUCCESS;
      }
      case "runtime": {
        if (flags.subcommand === "path") {
          const packageVersion = await readPackageVersion();
          log(paths().runtimeDir(packageVersion));
          return ExitCode.SUCCESS;
        }
        if (flags.subcommand === "list") {
          const p = paths();
          const packageVersion = await readPackageVersion();
          const rows = await listInstalledRuntimes(p, packageVersion);
          log(`runtime 目錄：${p.runtimeRoot}`);
          if (rows.length === 0) {
            log("（尚未安裝任何版本）");
          } else {
            for (const row of rows) {
              const mark = row.inUse ? "* " : "  ";
              log(`${mark}${row.version}  ${formatBytes(row.bytes)}`);
            }
            log("本指令只讀。要回收空間就手動刪掉沒有標記 * 的版本目錄。");
          }
          return ExitCode.SUCCESS;
        }
        // install
        const target = resolveTarget();
        const packageVersion = await readPackageVersion();
        const manifest = await readRuntimeManifest();
        const entry = loadManifest(manifest, { packageVersion, target });
        log(
          await resolveRuntimeDir({
            manifest,
            entry,
            target,
            packageVersion,
            p: paths(),
          }),
        );
        return ExitCode.SUCCESS;
      }
      case "migrate": {
        const p = paths();
        const packageVersion = await readPackageVersion();
        const result = await migrationMod.migrate({
          from: flags.from,
          mergeMissing: flags.mergeMissing,
          dryRun: flags.dryRun,
          paths: p,
          pythonPath: p.runtimePython(packageVersion),
          runtimeDir: p.runtimeDir(packageVersion),
        });
        log(JSON.stringify(result, null, 2));
        return ExitCode.SUCCESS;
      }
      case "logs": {
        // 讀日誌屬於使用者互動性質，交由呼叫端（bin/auto-ticket.mjs 或整合測試）決定實際輸出方式。
        return ExitCode.SUCCESS;
      }
      case "start":
      default: {
        const target = resolveTarget();
        const packageVersion = await readPackageVersion();
        const manifest = await readRuntimeManifest();
        const entry = loadManifest(manifest, { packageVersion, target });
        const p = paths();
        ui.writeBanner({ version: packageVersion });
        // 搶票一律借用使用者本機的真 Chrome，沒有它整個流程走不到最後一步。
        // 讓它在還沒下載 300MB runtime 之前就明確失敗，而不是等到領任務才炸。
        assertChromePresent();
        await hostMod.assertPortsFree({});
        const runtimeDir = await resolveRuntimeDir({
          manifest,
          entry,
          target,
          packageVersion,
          p,
          ui,
        });
        const pythonPath = runtimePythonIn(runtimeDir);
        if (!flags.skipMigration) {
          await migrationMod.migrate({
            from: flags.from,
            paths: p,
            pythonPath,
            runtimeDir,
            mergeMissing: Boolean(flags.mergeMissing),
          });
        }
        // env 先組好再交給 ensureBrowsers：兩邊各組一份就是漏 PYTHONPATH 的來源。
        const env = hostMod.buildEnv({ paths: p, runtimeDir });
        // Playwright 自己會印下載進度，所以這裡不疊 spinner，只讓它的輸出透出來。
        await hostMod.ensureBrowsers({ paths: p, pythonPath, env });
        return await supervisorMod.supervise({
          env,
          paths: p,
          version: packageVersion,
          headed: Boolean(flags.headed),
          pythonPath,
          runtimeDir,
          ports: hostMod.DEFAULT_PORTS,
        });
      }
    }
  } catch (err) {
    if (CliError.matches(err)) {
      errorLog(`錯誤：${err.message}`);
      return CliError.exitCodeOf(err);
    }
    errorLog(err.stack ?? String(err));
    return ExitCode.UNKNOWN_FLAG;
  }
}
