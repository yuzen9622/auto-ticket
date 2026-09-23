/**
 * `start` 這條路徑的接線測試。
 *
 * 起因是一個很便宜、卻沒有任何測試擋得住的錯誤：有人改了一個模組層常數的名字，
 * 三處引用沒跟著換。`node --check` 過、既有 112 條測試全綠，因為沒有任何一條
 * 真的走進 `start` 的下載段——未定義的識別字要等執行到那一行才炸。
 * `packages/cli` 目前沒有 lint，所以這一層只能靠測試守。
 *
 * 四個領域模組全部換成替身：這裡驗的是 command.mjs 的接線，不是它們的行為。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("node:fs", async (importOriginal) => {
  const actual = await importOriginal();
  // Chrome 是否存在不該決定這條測試的成敗；CI runner 有沒有裝不是我們要驗的事。
  return { ...actual, existsSync: () => true };
});

const ensureRuntime = vi.fn();
const migrate = vi.fn();
const supervise = vi.fn();
const ensureBrowsers = vi.fn();
const assertPortsFree = vi.fn();

vi.mock("../src/runtime-cache.mjs", () => ({ ensureRuntime: (...a) => ensureRuntime(...a) }));
vi.mock("../src/migration.mjs", () => ({ migrate: (...a) => migrate(...a) }));
vi.mock("../src/supervisor.mjs", () => ({ supervise: (...a) => supervise(...a) }));
vi.mock("../src/host.mjs", () => ({
  DEFAULT_PORTS: { api: 8000, web: 3000 },
  assertPortsFree: (...a) => assertPortsFree(...a),
  buildEnv: () => ({}),
  ensureBrowsers: (...a) => ensureBrowsers(...a),
}));

const { main, ExitCode } = await import("../src/command.mjs");

/** 靜音的進度列替身，同時記下 command.mjs 到底對它做了什麼。 */
function recordingUi() {
  const calls = [];
  const progress = {
    start: () => calls.push(["start"]),
    update: (u) => calls.push(["update", u]),
    reset: () => calls.push(["reset"]),
    setLabel: (l, o) => calls.push(["setLabel", l, o]),
    done: (m) => calls.push(["done", m]),
    fail: (m) => calls.push(["fail", m]),
    stop: () => calls.push(["stop"]),
  };
  return { calls, createProgress: () => progress, writeBanner: () => {} };
}

beforeEach(() => {
  vi.clearAllMocks();
  supervise.mockResolvedValue(ExitCode.SUCCESS);
  delete process.env.AUTO_TICKET_RUNTIME_DIR;
});

describe("start 的下載段接線", () => {
  it("四個階段回呼都接得上，成功時收在 done", async () => {
    const ui = recordingUi();
    ensureRuntime.mockImplementation(async ({ onPhase, onProgress, onRetry }) => {
      onPhase("download");
      onProgress({ loaded: 10, total: 100 });
      onRetry({ attempt: 1, maxRetries: 3 });
      onPhase("verify");
      onPhase("extract");
      onPhase("commit");
      return "/fake/runtime";
    });

    const code = await main(["start", "--skip-migration"], { ui, log: () => {}, errorLog: () => {} });

    expect(code).toBe(ExitCode.SUCCESS);
    const kinds = ui.calls.map((c) => c[0]);
    expect(kinds).toContain("start");
    expect(kinds).toContain("update");
    expect(kinds).toContain("reset");
    expect(kinds).toContain("done");
    expect(kinds).not.toContain("fail");

    // 重試會換標籤，而且換上去的是實際的嘗試次數，不是寫死的字串。
    const retryLabel = ui.calls.find((c) => c[0] === "setLabel" && /1\/3/.test(c[1]));
    expect(retryLabel, "重試時應更新標籤").toBeTruthy();

    // 沒有位元組可數的階段必須標成 indeterminate，否則會留一根停在 100% 的進度條。
    const extract = ui.calls.find((c) => c[0] === "setLabel" && c[2]?.indeterminate);
    expect(extract, "解壓／安裝階段應為 indeterminate").toBeTruthy();
  });

  it("下載失敗時進度列收在 fail，錯誤碼原樣往外傳", async () => {
    const ui = recordingUi();
    const err = new Error("下載失敗");
    err.name = "CliError";
    err.code = "DOWNLOAD_FAILED";
    ensureRuntime.mockRejectedValue(err);

    const code = await main(["start", "--skip-migration"], { ui, log: () => {}, errorLog: () => {} });

    expect(code).toBe(ExitCode.DOWNLOAD_FAILED);
    expect(ui.calls.map((c) => c[0])).toContain("fail");
    expect(supervise).not.toHaveBeenCalled();
  });

  it("AUTO_TICKET_RUNTIME_DIR 旁路不建立進度列，因為根本沒有下載", async () => {
    const ui = recordingUi();
    process.env.AUTO_TICKET_RUNTIME_DIR = "/preinstalled/runtime";

    await main(["start", "--skip-migration"], { ui, log: () => {}, errorLog: () => {} });

    expect(ensureRuntime).not.toHaveBeenCalled();
    expect(ui.calls).toEqual([]);
    expect(supervise).toHaveBeenCalled();
  });
});
