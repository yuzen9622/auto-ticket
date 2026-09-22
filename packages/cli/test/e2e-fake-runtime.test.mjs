/**
 * 假 runtime 端到端：真的 HTTP server、真的 tar、真的檔案系統。
 *
 * 單元測試把 fetch 與 spawn 都注入掉了，那守得住邏輯，守不住「系統 tar 在這個
 * 平台上到底吃不吃這組 argv」「串流寫檔與雜湊有沒有對上」這類只有真的跑過才知道
 * 的事。三個 OS 的 CI 都跑這一支。
 */
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import { createServer } from "node:http";
import os from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ensureRuntime } from "../src/runtime-cache.mjs";

const FIXTURE = path.resolve(import.meta.dirname, "fixtures/fake-runtime");
const VERSION = "9.9.9";
const TARGET = "darwin-arm64";
const ROOT_NAME = `auto-ticket-runtime-${TARGET}-${VERSION}`;
const ASSET = `${ROOT_NAME}.tar.gz`;

let tmpRoot;
let server;
let baseUrl;

/**
 * 跑 tar 並把 stderr 帶回錯誤訊息。
 *
 * 一律以 `cwd` ＋ 相對路徑呼叫：Windows 上 `tar` 可能解析到 MSYS 的 GNU tar，
 * 而 GNU tar 會把 `C:\\...` 當成 `host:path` 的遠端規格而整個拒絕。相對路徑
 * 對 bsdtar 與 GNU tar 都成立。
 */
async function run(cmd, args, cwd) {
  await new Promise((resolve, reject) => {
    const child = spawn(cmd, args, { shell: false, cwd });
    let stderr = "";
    child.stderr?.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.once("error", reject);
    child.once("exit", (code) =>
      code === 0
        ? resolve()
        : reject(new Error(`${cmd} ${args.join(" ")} exit ${code}: ${stderr}`)),
    );
  });
}

/** 把 fixture 複製成帶版本號的根目錄，補上 MANIFEST.json，再用系統 tar 打包。 */
async function buildAsset(outDir) {
  const stageParent = path.join(tmpRoot, "stage");
  const stage = path.join(stageParent, ROOT_NAME);
  await fs.cp(FIXTURE, stage, { recursive: true });
  await fs.writeFile(
    path.join(stage, "MANIFEST.json"),
    JSON.stringify({ schema: 1, version: VERSION, target: TARGET }, null, 2),
  );
  await fs.mkdir(outDir, { recursive: true });
  const archive = path.join(outDir, ASSET);
  await run("tar", ["-czf", path.relative(stageParent, archive), ROOT_NAME], stageParent);
  const bytes = await fs.readFile(archive);
  return { archive, bytes, sha256: createHash("sha256").update(bytes).digest("hex") };
}

function fakePaths(root) {
  const runtimeRoot = path.join(root, "runtime");
  return {
    runtimeRoot,
    runtimeTmp: path.join(runtimeRoot, ".tmp"),
    runtimeDir: (version) => path.join(runtimeRoot, version),
  };
}

beforeEach(async () => {
  tmpRoot = await fs.mkdtemp(path.join(os.tmpdir(), "at-e2e-"));
});

afterEach(async () => {
  await new Promise((resolve) => (server ? server.close(resolve) : resolve()));
  server = undefined;
  await fs.rm(tmpRoot, { recursive: true, force: true });
});

async function serve(bytes) {
  server = createServer((req, res) => {
    if (!req.url.endsWith(ASSET)) {
      res.writeHead(404).end();
      return;
    }
    res.writeHead(200, { "content-type": "application/gzip" });
    res.end(bytes);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  baseUrl = `http://127.0.0.1:${server.address().port}`;
}

describe("假 runtime 端到端", () => {
  it("download → verify → extract → commit 全程走真實 I/O", async () => {
    const { bytes, sha256 } = await buildAsset(path.join(tmpRoot, "release"));
    await serve(bytes);
    const paths = fakePaths(tmpRoot);

    const committed = await ensureRuntime({
      manifestEntry: { file: ASSET, sha256 },
      target: TARGET,
      version: VERSION,
      paths,
      baseUrl,
    });

    expect(committed).toBe(paths.runtimeDir(VERSION));
    const manifest = JSON.parse(
      await fs.readFile(path.join(committed, "MANIFEST.json"), "utf8"),
    );
    expect(manifest).toMatchObject({ version: VERSION, target: TARGET });
    // fixture 的內容確實被解出來了，不是只建了個空目錄。
    await expect(
      fs.stat(path.join(committed, "app", "scripts", "serve_api.py")),
    ).resolves.toBeTruthy();
    await expect(fs.stat(path.join(committed, "web", "server.js"))).resolves.toBeTruthy();

    // 暫存目錄整個清乾淨：沒有半個 GB 的 .part，也沒有解壓用的空外殼。
    const leftovers = await fs.readdir(paths.runtimeTmp).catch(() => []);
    expect(leftovers).toEqual([]);
  });

  it("已安裝就直接回傳，不再打網路", async () => {
    const { bytes, sha256 } = await buildAsset(path.join(tmpRoot, "release"));
    await serve(bytes);
    const paths = fakePaths(tmpRoot);
    const args = {
      manifestEntry: { file: ASSET, sha256 },
      target: TARGET,
      version: VERSION,
      paths,
      baseUrl,
    };

    await ensureRuntime(args);
    let secondFetch = 0;
    await ensureRuntime({
      ...args,
      fetchImpl: async (...a) => {
        secondFetch += 1;
        return fetch(...a);
      },
    });
    expect(secondFetch).toBe(0);
  });

  it("server 回 404 時以 DOWNLOAD_FAILED 收場，且不留下半殘的 runtime 目錄", async () => {
    const { sha256 } = await buildAsset(path.join(tmpRoot, "release"));
    await serve(Buffer.from(""));
    const paths = fakePaths(tmpRoot);

    const err = await ensureRuntime({
      manifestEntry: { file: "does-not-exist.tar.gz", sha256 },
      target: TARGET,
      version: VERSION,
      paths,
      baseUrl,
      sleepImpl: () => Promise.resolve(),
    }).then(
      () => null,
      (e) => e,
    );

    expect(err?.code).toBe("DOWNLOAD_FAILED");
    await expect(fs.stat(paths.runtimeDir(VERSION))).rejects.toThrow();
  });
});
