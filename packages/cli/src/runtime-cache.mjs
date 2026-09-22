// 一次 transactional runtime 安裝：download → verify(sha256) → extract(系統 tar) → 原子 rename。
// 領域模組，不得 import migration.mjs / host.mjs / supervisor.mjs。
import { createHash } from "node:crypto";
import { spawn as nodeSpawn } from "node:child_process";
import { createWriteStream } from "node:fs";
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

function isCliError(err) {
  return err?.name === "CliError" && typeof err?.code === "string";
}

const RETRY_DELAYS_MS = [1000, 4000, 16000];
const RETRYABLE_STATUS = new Set([429, 500, 502, 503, 504]);

function defaultSleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * 下載一個檔案並邊寫邊算 sha256，不二次讀檔。
 * 絕不讀取或帶上任何憑證環境變數：公開 repo 的 release asset 匿名可取，
 * 這裡的 fetch 呼叫刻意不組任何 Authorization header。
 */
export async function download({
  url,
  destPath,
  fetchImpl = fetch,
  sleepImpl = defaultSleep,
  maxRetries = 3,
  timeoutMs = 60_000,
}) {
  let lastErr;
  for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetchImpl(url, { signal: controller.signal });
      clearTimeout(timer);
      if (!response.ok) {
        if (RETRYABLE_STATUS.has(response.status) && attempt < maxRetries) {
          lastErr = new Error(`HTTP ${response.status}`);
          await sleepImpl(RETRY_DELAYS_MS[attempt] ?? RETRY_DELAYS_MS.at(-1));
          continue;
        }
        throw cliError(
          "DOWNLOAD_FAILED",
          `下載失敗：HTTP ${response.status}（${url}）。`,
        );
      }
      return await streamToFileWithHash(response, destPath);
    } catch (err) {
      clearTimeout(timer);
      if (isCliError(err)) throw err;
      lastErr = err;
      if (attempt < maxRetries) {
        await sleepImpl(RETRY_DELAYS_MS[attempt] ?? RETRY_DELAYS_MS.at(-1));
        continue;
      }
    }
  }
  throw cliError(
    "DOWNLOAD_FAILED",
    `下載失敗，已重試 ${maxRetries} 次：${lastErr?.message ?? "未知錯誤"}`,
  );
}

async function streamToFileWithHash(response, destPath) {
  await fs.mkdir(path.dirname(destPath), { recursive: true });
  const hash = createHash("sha256");
  let size = 0;
  const out = createWriteStream(destPath);
  const body = response.body;
  if (body && typeof body.getReader === "function") {
    // Web ReadableStream（global fetch 的回應體）。
    const reader = body.getReader();
    await new Promise((resolve, reject) => {
      out.on("error", reject);
      (async () => {
        try {
          for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            hash.update(value);
            size += value.length;
            if (!out.write(value)) {
              await new Promise((r) => out.once("drain", r));
            }
          }
          out.end(resolve);
        } catch (err) {
          reject(err);
        }
      })();
    });
  } else {
    // 測試替身：允許直接給 Buffer/string 當作 body。
    const buf = Buffer.isBuffer(body) ? body : Buffer.from(body ?? "");
    hash.update(buf);
    size = buf.length;
    await new Promise((resolve, reject) => {
      out.write(buf, (err) => (err ? reject(err) : resolve()));
    });
    await new Promise((resolve) => out.end(resolve));
  }
  return { sha256: hash.digest("hex"), size };
}

/** 純比對，不做任何 I/O。 */
export function verifySha256(actualHex, expectedHex) {
  return (
    typeof actualHex === "string" &&
    typeof expectedHex === "string" &&
    actualHex.toLowerCase() === expectedHex.toLowerCase()
  );
}

/**
 * 呼叫系統 `tar` 解壓，argv 陣列、shell:false，不串接使用者輸入。
 */
export async function extract({ archivePath, destDir, spawnImpl }) {
  await fs.mkdir(destDir, { recursive: true });
  const run = spawnImpl ?? nodeSpawn;
  await new Promise((resolve, reject) => {
    const child = run("tar", ["-xzf", archivePath, "-C", destDir], {
      shell: false,
    });
    let stderr = "";
    child.stderr?.on?.("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.once("error", reject);
    child.once("exit", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`tar 解壓失敗（exit ${code}）：${stderr}`));
    });
  });
}

/**
 * 以 mkdtemp + rename 達成同檔案系統內的原子提交；目標已存在則先 rename 走既有版本。
 */
export async function commitRuntime({ stagingDir, runtimeRoot, version, fsImpl = fs }) {
  const target = path.join(runtimeRoot, version);
  await fsImpl.mkdir(runtimeRoot, { recursive: true });
  try {
    const stat = await fsImpl.stat(target).catch(() => null);
    if (stat) {
      const old = path.join(runtimeRoot, ".tmp", `${version}.old-${Date.now()}`);
      await fsImpl.mkdir(path.dirname(old), { recursive: true });
      await fsImpl.rename(target, old);
    }
    await fsImpl.rename(stagingDir, target);
  } catch (err) {
    throw cliError(
      "VERIFY_FAILED",
      `runtime 提交失敗（${process.platform} rename）：${err.message}`,
    );
  }
  return target;
}

/**
 * 整條安裝流程的協調者：download → verify → extract 到 staging → 驗證內部 MANIFEST.json → commit。
 * 任一步失敗都清乾淨 `.tmp`，不留半殘目錄；校驗失敗不重試（那是完整性事件，不是網路抖動）。
 */
export async function ensureRuntime({
  manifestEntry,
  target,
  version,
  paths,
  fetchImpl,
  spawnImpl,
  sleepImpl,
  fsImpl = fs,
  baseUrl,
}) {
  const runtimeDir = paths.runtimeDir(version);
  const already = await fsImpl.stat(runtimeDir).catch(() => null);
  if (already) return runtimeDir;

  await fsImpl.mkdir(paths.runtimeTmp, { recursive: true });
  const partPath = path.join(paths.runtimeTmp, `${manifestEntry.file}.part`);
  const url = `${baseUrl ?? ""}/${manifestEntry.file}`;

  const { sha256 } = await download({ url, destPath: partPath, fetchImpl, sleepImpl });
  if (!verifySha256(sha256, manifestEntry.sha256)) {
    await fsImpl.rm(partPath, { force: true });
    throw cliError(
      "VERIFY_FAILED",
      `sha256 校驗失敗：expected=${manifestEntry.sha256.slice(0, 16)}… actual=${sha256.slice(0, 16)}…`,
    );
  }

  const stagingParent = await fsImpl.mkdtemp(path.join(paths.runtimeTmp, "unpack-"));
  try {
    await extract({ archivePath: partPath, destDir: stagingParent, spawnImpl });
    const entries = await fsImpl.readdir(stagingParent);
    const rootName = entries[0];
    if (!rootName) {
      throw new Error("解壓後的 runtime 目錄是空的。");
    }
    const stagingDir = path.join(stagingParent, rootName);
    const manifestRaw = await fsImpl.readFile(
      path.join(stagingDir, "MANIFEST.json"),
      "utf8",
    );
    const innerManifest = JSON.parse(manifestRaw);
    if (innerManifest.version !== version || innerManifest.target !== target) {
      throw new Error(
        `runtime 內部 MANIFEST.json 不符：version=${innerManifest.version} target=${innerManifest.target}`,
      );
    }
    const committed = await commitRuntime({
      stagingDir,
      runtimeRoot: paths.runtimeRoot,
      version,
      fsImpl,
    });
    await fsImpl.rm(partPath, { force: true });
    return committed;
  } catch (err) {
    await fsImpl.rm(stagingParent, { recursive: true, force: true }).catch(() => {});
    await fsImpl.rm(partPath, { force: true }).catch(() => {});
    if (isCliError(err)) throw err;
    throw cliError("VERIFY_FAILED", `runtime 解壓或提交失敗：${err.message}`);
  }
}
