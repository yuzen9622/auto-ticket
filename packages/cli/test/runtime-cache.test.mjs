import { spawn as realSpawn } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ExitCode } from "../src/command.mjs";
import { commitRuntime, download, ensureRuntime, extract, verifySha256 } from "../src/runtime-cache.mjs";

const noopSleep = () => Promise.resolve();

function fakeResponse({ ok = true, status = 200, body = Buffer.from("") } = {}) {
  return { ok, status, body };
}

let tmpRoot;

beforeEach(async () => {
  tmpRoot = await fs.mkdtemp(path.join(os.tmpdir(), "at-runtime-cache-"));
});

afterEach(async () => {
  await fs.rm(tmpRoot, { recursive: true, force: true });
});

describe("download + verifySha256", () => {
  it("① sha256 正確通過", async () => {
    const content = Buffer.from("hello runtime");
    const expected = createHash("sha256").update(content).digest("hex");
    const dest = path.join(tmpRoot, "file.part");
    const fetchImpl = async () => fakeResponse({ body: content });

    const { sha256 } = await download({ url: "https://example.invalid/x", destPath: dest, fetchImpl, sleepImpl: noopSleep });
    expect(sha256).toBe(expected);
    expect(verifySha256(sha256, expected)).toBe(true);
  });

  it("② 竄改 1 byte → 校驗失敗，ensureRuntime 會刪除 .part 並丟 code=5、不重試，且不曾解壓", async () => {
    // 刻意送**合法**的 tar.gz，只是位元組與 manifest 釘選的雜湊不符。
    // 用一段亂碼當內容的話，就算校驗整段被刪掉，解壓也會自己失敗，
    // 於是測試照樣綠——那種測試守不住任何東西。
    const tamperedArchive = await makeTarball(tmpRoot, "tampered", {
      "MANIFEST.json": JSON.stringify({ version: "1.0.0", target: "darwin-arm64" }),
    });
    const otherArchive = await makeTarball(tmpRoot, "other", {
      "MANIFEST.json": JSON.stringify({ version: "1.0.0", target: "darwin-arm64" }),
      "extra.txt": "這一份的位元組不一樣，所以雜湊也不一樣",
    });
    const expectedHash = createHash("sha256").update(otherArchive).digest("hex");

    let fetchCalls = 0;
    const fetchImpl = async () => {
      fetchCalls += 1;
      return fakeResponse({ body: tamperedArchive });
    };
    let spawnCalls = 0;
    const spawnImpl = (cmd, args, opts) => {
      spawnCalls += 1;
      return realTarSpawn(cmd, args, opts);
    };

    const paths = fakePaths(tmpRoot);
    const err = await ensureRuntime({
      manifestEntry: { file: "runtime.tar.gz", sha256: expectedHash },
      target: "darwin-arm64",
      version: "1.0.0",
      paths,
      fetchImpl,
      spawnImpl,
      sleepImpl: noopSleep,
      baseUrl: "https://example.invalid",
    }).then(
      () => null,
      (e) => e,
    );

    expect(err).not.toBeNull();
    expect(err.code).toBe("VERIFY_FAILED");
    expect(ExitCode[err.code]).toBe(ExitCode.VERIFY_FAILED);
    // 訊息必須指出是雜湊不符，而不是下游某個步驟壞掉。
    expect(err.message).toContain("sha256 校驗失敗");
    // 關鍵斷言：校驗是閘門。沒擋住的話這份合法 tar 會被順利解壓並提交。
    expect(spawnCalls).toBe(0);
    await expect(fs.stat(paths.runtimeDir("1.0.0"))).rejects.toThrow();

    expect(fetchCalls).toBe(1); // 校驗失敗不重試
    const partPath = path.join(paths.runtimeTmp, "runtime.tar.gz.part");
    await expect(fs.stat(partPath)).rejects.toThrow();
  });

  it("位元組與 manifest 相符時，同一條路徑會真的解壓並提交（證明上一條不是恆綠）", async () => {
    const archive = await makeTarball(tmpRoot, "good-1.0.0", {
      "MANIFEST.json": JSON.stringify({ version: "1.0.0", target: "darwin-arm64" }),
    });
    const sha256 = createHash("sha256").update(archive).digest("hex");
    const paths = fakePaths(tmpRoot);

    const committed = await ensureRuntime({
      manifestEntry: { file: "runtime.tar.gz", sha256 },
      target: "darwin-arm64",
      version: "1.0.0",
      paths,
      fetchImpl: async () => fakeResponse({ body: archive }),
      spawnImpl: realTarSpawn,
      sleepImpl: noopSleep,
      baseUrl: "https://example.invalid",
    });

    expect(committed).toBe(paths.runtimeDir("1.0.0"));
    const inner = JSON.parse(
      await fs.readFile(path.join(committed, "MANIFEST.json"), "utf8"),
    );
    expect(inner.version).toBe("1.0.0");
  });

  it("③ 429/500 重試 3 次後放棄，code=3", async () => {
    let calls = 0;
    const fetchImpl = async () => {
      calls += 1;
      return fakeResponse({ ok: false, status: 500 });
    };
    const dest = path.join(tmpRoot, "file2.part");
    await expect(
      download({ url: "https://example.invalid/y", destPath: dest, fetchImpl, sleepImpl: noopSleep, maxRetries: 3 }),
    ).rejects.toMatchObject({ code: "DOWNLOAD_FAILED" });
    expect(calls).toBe(4); // 第一次 + 3 次重試
  });

  it("成功前重試幾次也能拿到結果", async () => {
    let calls = 0;
    const content = Buffer.from("eventually ok");
    const fetchImpl = async () => {
      calls += 1;
      if (calls < 3) return fakeResponse({ ok: false, status: 502 });
      return fakeResponse({ body: content });
    };
    const dest = path.join(tmpRoot, "file3.part");
    const { size } = await download({ url: "https://x.invalid", destPath: dest, fetchImpl, sleepImpl: noopSleep });
    expect(size).toBe(content.length);
    expect(calls).toBe(3);
  });

  it("④ request 不帶 Authorization，即使環境變數設了 GITHUB_TOKEN/GH_TOKEN", async () => {
    process.env.GITHUB_TOKEN = "should-not-be-used";
    process.env.GH_TOKEN = "should-not-be-used-either";
    const seenInits = [];
    const fetchImpl = async (url, init) => {
      seenInits.push(init);
      return fakeResponse({ body: Buffer.from("x") });
    };
    try {
      await download({ url: "https://example.invalid/z", destPath: path.join(tmpRoot, "f4.part"), fetchImpl, sleepImpl: noopSleep });
    } finally {
      delete process.env.GITHUB_TOKEN;
      delete process.env.GH_TOKEN;
    }
    for (const init of seenInits) {
      expect(init?.headers?.Authorization).toBeUndefined();
      expect(JSON.stringify(init ?? {})).not.toMatch(/should-not-be-used/);
    }
  });
});

describe("extract", () => {
  it("⑥ spawnImpl 收到 argv 陣列且 shell 非 true", async () => {
    let seen;
    const fakeChild = makeFakeChild(0);
    const spawnImpl = (cmd, args, opts) => {
      seen = { cmd, args, opts };
      return fakeChild;
    };
    await extract({ archivePath: "/tmp/whatever.tar.gz", destDir: path.join(tmpRoot, "out"), spawnImpl });
    expect(seen.cmd).toBe("tar");
    expect(Array.isArray(seen.args)).toBe(true);
    expect(seen.args).toEqual(["-xzf", "/tmp/whatever.tar.gz", "-C", path.join(tmpRoot, "out")]);
    expect(seen.opts.shell).not.toBe(true);
  });
});

describe("ensureRuntime：解壓中途失敗", () => {
  it("⑤ runtime/<ver> 不存在、.tmp 已清、既有舊版完好", async () => {
    const paths = fakePaths(tmpRoot);
    // 先放一個「既有舊版」，之後應保持完好不動。
    const oldVersionDir = paths.runtimeDir("0.9.0");
    await fs.mkdir(oldVersionDir, { recursive: true });
    await fs.writeFile(path.join(oldVersionDir, "marker.txt"), "old-and-intact");

    const content = Buffer.from("archive-bytes");
    const sha256 = createHash("sha256").update(content).digest("hex");
    const fetchImpl = async () => fakeResponse({ body: content });
    const failingSpawn = () => makeFakeChild(1, "boom");

    await expect(
      ensureRuntime({
        manifestEntry: { file: "runtime.tar.gz", sha256 },
        target: "darwin-arm64",
        version: "1.0.0",
        paths,
        fetchImpl,
        spawnImpl: failingSpawn,
        sleepImpl: noopSleep,
        baseUrl: "https://example.invalid",
      }),
    ).rejects.toMatchObject({ name: "CliError" });

    await expect(fs.stat(paths.runtimeDir("1.0.0"))).rejects.toThrow();
    const tmpEntries = await fs.readdir(paths.runtimeTmp).catch(() => []);
    const leftoverUnpackDirs = tmpEntries.filter((e) => e.startsWith("unpack-"));
    expect(leftoverUnpackDirs).toEqual([]);

    const oldStillThere = await fs.readFile(path.join(oldVersionDir, "marker.txt"), "utf8");
    expect(oldStillThere).toBe("old-and-intact");
  });
});

describe("commitRuntime", () => {
  it("⑦ rename 失敗時丟出明確錯誤，且不留半殘目錄", async () => {
    const runtimeRoot = path.join(tmpRoot, "runtime");
    const stagingDir = path.join(tmpRoot, "staging-1.0.0");
    await fs.mkdir(stagingDir, { recursive: true });

    const fsImpl = {
      mkdir: fs.mkdir,
      stat: fs.stat,
      rename: async () => {
        const err = new Error("EPERM: operation not permitted, rename");
        err.code = "EPERM";
        throw err;
      },
    };

    await expect(
      commitRuntime({ stagingDir, runtimeRoot, version: "1.0.0", fsImpl }),
    ).rejects.toMatchObject({ code: "VERIFY_FAILED" });
  });
});

/** 用系統 tar 打一個真的 tar.gz，回傳它的位元組。 */
async function makeTarball(root, name, files) {
  const stage = path.join(root, `mk-${name}`, name);
  await fs.mkdir(stage, { recursive: true });
  for (const [rel, content] of Object.entries(files)) {
    await fs.writeFile(path.join(stage, rel), content);
  }
  const archivePath = path.join(root, `${name}.tar.gz`);
  const stageParent = path.dirname(stage);
  await new Promise((resolve, reject) => {
    // cwd ＋ 相對路徑：Windows 上 `tar` 可能是 MSYS 的 GNU tar，而它會把
    // `C:\\...` 當成遠端 host:path 規格拒絕掉。
    const child = realSpawn(
      "tar",
      ["-czf", path.relative(stageParent, archivePath), name],
      { shell: false, cwd: stageParent },
    );
    let stderr = "";
    child.stderr?.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.once("error", reject);
    child.once("exit", (code) =>
      code === 0 ? resolve() : reject(new Error(`tar exit ${code}: ${stderr}`)),
    );
  });
  return fs.readFile(archivePath);
}

/** 真的呼叫系統 tar，讓「解壓確實發生了」成為可觀察的事實。 */
function realTarSpawn(cmd, args, opts) {
  return realSpawn(cmd, args, { ...opts, shell: false });
}

function fakePaths(root) {
  const runtimeRoot = path.join(root, "runtime");
  return {
    runtimeRoot,
    runtimeTmp: path.join(runtimeRoot, ".tmp"),
    runtimeDir: (version) => path.join(runtimeRoot, version),
  };
}

function makeFakeChild(exitCode, stderrText = "") {
  const listeners = {};
  const child = {
    stdout: { on: () => {} },
    stderr: {
      on: (event, cb) => {
        if (event === "data" && stderrText) cb(Buffer.from(stderrText));
      },
    },
    once: (event, cb) => {
      listeners[event] = cb;
      if (event === "exit") {
        queueMicrotask(() => cb(exitCode));
      }
    },
  };
  return child;
}
