import { spawn as realSpawn } from "node:child_process";
import { closeSync, openSync } from "node:fs";
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
    const destDir = path.join(tmpRoot, "out");
    const archivePath = path.join(tmpRoot, "whatever.tar.gz");
    await extract({ archivePath, destDir, spawnImpl });
    expect(seen.cmd).toBe("tar");
    expect(Array.isArray(seen.args)).toBe(true);
    // 以 cwd 取代 -C，archive 給相對路徑：Windows 的 GNU tar 會把 `C:\...`
    // 當成遠端 host:path 而拒絕（實測 `Cannot connect to C: resolve failed`）。
    expect(seen.args).toEqual(["-xzf", path.join("..", "whatever.tar.gz")]);
    expect(seen.opts.cwd).toBe(destDir);
    expect(seen.opts.shell).not.toBe(true);
    for (const arg of seen.args) {
      expect(/^[A-Za-z]:[\\/]/.test(arg), `argv 不得帶磁碟機代號: ${arg}`).toBe(false);
    }
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

describe("download：串流中途出錯", () => {
  it("已收到的位元組會落地，下一次才有斷點可續", async () => {
    const destPath = path.join(tmpRoot, "big.part");
    // 1MB 遠大於 write stream 預設的 64KB 高水位，所以一定有資料還卡在緩衝區。
    // 不把串流關乾淨就拋出去的話，這個檔案會是 0 bytes，續傳永遠不會發生。
    const chunk = new Uint8Array(1024 * 1024).fill(7);
    const fetchImpl = async () => ({
      ok: true,
      status: 200,
      headers: { get: () => "2097152" },
      body: {
        getReader() {
          let sent = false;
          return {
            async read() {
              if (sent) throw new Error("socket hang up");
              sent = true;
              return { done: false, value: chunk };
            },
          };
        },
      },
    });

    await expect(
      download({
        url: "http://example.invalid/big.tar.gz",
        destPath,
        fetchImpl,
        sleepImpl: () => Promise.resolve(),
        maxRetries: 0,
      }),
    ).rejects.toMatchObject({ code: "DOWNLOAD_FAILED" });

    const stat = await fs.stat(destPath);
    expect(stat.size).toBe(chunk.length);
  });

  it("失敗的下載不會洩漏檔案描述符", async () => {
    // 實測：不把 write stream 關乾淨就拋出去，20 次失敗下載就留下 20 個開著的 fd。
    // 下載是可重入流程，使用者在爛線路上反覆重跑時這會一路累積。
    // 用「新開一個檔案拿到的 fd 編號漲了多少」當代理指標，不依賴 lsof。
    const probe = () => {
      const fd = openSync(path.join(tmpRoot, "probe"), "w");
      closeSync(fd);
      return fd;
    };
    const chunk = new Uint8Array(1024 * 1024).fill(7);
    const fetchImpl = async () => ({
      ok: true,
      status: 200,
      headers: { get: () => "99999999" },
      body: {
        getReader() {
          let sent = false;
          return {
            async read() {
              if (sent) throw new Error("socket hang up");
              sent = true;
              return { done: false, value: chunk };
            },
          };
        },
      },
    });

    const before = probe();
    for (let i = 0; i < 10; i += 1) {
      await download({
        url: "http://example.invalid/leak.tar.gz",
        destPath: path.join(tmpRoot, `leak-${i}.part`),
        fetchImpl,
        sleepImpl: () => Promise.resolve(),
        maxRetries: 0,
      }).catch(() => {});
    }
    const after = probe();
    expect(after - before).toBeLessThan(5);
  });

  it("重試時會帶著 Range 從斷點接續，而不是從頭再來", async () => {
    const destPath = path.join(tmpRoot, "resume.part");
    const whole = new Uint8Array(300).fill(3);
    const seen = [];
    let call = 0;
    const fetchImpl = async (_url, init) => {
      call += 1;
      seen.push(init?.headers?.Range ?? null);
      if (call === 1) {
        return {
          ok: true,
          status: 200,
          headers: { get: () => "300" },
          body: Buffer.from(whole.subarray(0, 100)),
        };
      }
      return {
        ok: true,
        status: 206,
        headers: { get: () => "200" },
        body: Buffer.from(whole.subarray(100)),
      };
    };

    // 第一次「成功」但只拿到 100 bytes，雜湊當然不對；這裡只驗續傳的機制本身：
    // 讓第一次拿完就人為失敗，重試必須從 100 接下去。
    await download({
      url: "http://example.invalid/resume.tar.gz",
      destPath,
      fetchImpl,
      sleepImpl: () => Promise.resolve(),
      maxRetries: 0,
    });
    const first = await fs.stat(destPath);
    expect(first.size).toBe(100);

    const result = await download({
      url: "http://example.invalid/resume.tar.gz",
      destPath,
      fetchImpl,
      sleepImpl: () => Promise.resolve(),
      maxRetries: 0,
    });
    expect(seen).toEqual([null, "bytes=100-"]);
    expect(result.size).toBe(300);
    // 整檔雜湊，不是第二段的雜湊。
    expect(result.sha256).toBe(createHash("sha256").update(Buffer.from(whole)).digest("hex"));
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

  it("重裝同一版時，被換下來的舊版會被刪掉，不在家目錄裡無聲堆積", async () => {
    const runtimeRoot = path.join(tmpRoot, "runtime");
    const target = path.join(runtimeRoot, "1.0.0");
    await fs.mkdir(target, { recursive: true });
    await fs.writeFile(path.join(target, "marker.txt"), "old");

    const stagingDir = path.join(tmpRoot, "staging-1.0.0");
    await fs.mkdir(stagingDir, { recursive: true });
    await fs.writeFile(path.join(stagingDir, "marker.txt"), "new");

    const committed = await commitRuntime({ stagingDir, runtimeRoot, version: "1.0.0" });

    expect(await fs.readFile(path.join(committed, "marker.txt"), "utf8")).toBe("new");
    // 一份解開的 runtime 是 600MB 等級，而且沒有任何指令看得到 .tmp 底下的備份。
    const leftovers = await fs.readdir(path.join(runtimeRoot, ".tmp")).catch(() => []);
    expect(leftovers.filter((e) => e.includes(".old-"))).toEqual([]);
  });

  it("備份刪不掉也不算失敗：新版已經就位，回收空間只是附帶", async () => {
    const runtimeRoot = path.join(tmpRoot, "runtime");
    const target = path.join(runtimeRoot, "1.0.0");
    await fs.mkdir(target, { recursive: true });
    const stagingDir = path.join(tmpRoot, "staging-1.0.0");
    await fs.mkdir(stagingDir, { recursive: true });

    const fsImpl = {
      mkdir: fs.mkdir,
      stat: fs.stat,
      rename: fs.rename,
      rm: async () => {
        throw new Error("EBUSY");
      },
    };

    await expect(
      commitRuntime({ stagingDir, runtimeRoot, version: "1.0.0", fsImpl }),
    ).resolves.toBe(target);
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
