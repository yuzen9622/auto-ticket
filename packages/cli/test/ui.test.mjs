/**
 * 進度列與橫幅。
 *
 * 這裡真正要守住的不是「畫得好不好看」，而是兩件會實際傷到人的事：
 * 一、非互動輸出（重導到檔案、CI）絕不能吐覆寫控制碼，也不能整段靜默；
 * 二、藏起來的游標一定要還回去，包含被 Ctrl-C 打斷的那條路徑。
 */
import { describe, expect, it, vi } from "vitest";

import {
  banner,
  createProgress,
  displayWidth,
  formatBytes,
  formatDuration,
  isInteractive,
  renderBar,
  supportsColor,
  truncateToWidth,
} from "../src/ui.mjs";

/** 去掉 ANSI 控制碼，只留下真正印在螢幕上的字。 */
// eslint-disable-next-line no-control-regex
const stripAnsi = (text) => text.replace(/\u001B\[[0-9;?]*[A-Za-z]/g, "");

/**
 * `columns` 要在這裡給，不能用 `{ ...fakeStream(true), columns }`——
 * 物件展開會把 `text` 這個 getter 求值成當下的空字串並固定下來，
 * 於是所有斷言都變成拿空字串去比，測試全綠卻什麼都沒測到。
 */
function fakeStream(isTTY, columns) {
  const chunks = [];
  return {
    isTTY,
    ...(columns === undefined ? {} : { columns }),
    write: (text) => chunks.push(text),
    get text() {
      return chunks.join("");
    },
    chunks,
  };
}

const ESC_RE = /\u001B\[/;

describe("終端機能力偵測", () => {
  it("非 TTY、CI、TERM=dumb 一律不做動畫", () => {
    expect(isInteractive({ stream: fakeStream(false), env: {} })).toBe(false);
    expect(isInteractive({ stream: fakeStream(true), env: { CI: "1" } })).toBe(false);
    expect(isInteractive({ stream: fakeStream(true), env: { TERM: "dumb" } })).toBe(false);
    expect(isInteractive({ stream: fakeStream(true), env: {} })).toBe(true);
  });

  it("NO_COLOR 只關顏色，不關進度", () => {
    const stream = fakeStream(true);
    expect(supportsColor({ stream, env: { NO_COLOR: "1" } })).toBe(false);
    expect(isInteractive({ stream, env: { NO_COLOR: "1" } })).toBe(true);
  });
});

describe("格式化", () => {
  it("位元組隨量級縮減小數位", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1024)).toBe("1.0 KB");
    // 三位數以上不留小數：進度列每 80ms 重畫一次，寬度跳動比精度重要。
    expect(formatBytes(254_346_892)).toBe("243 MB");
    expect(formatBytes(42 * 1024 * 1024)).toBe("42.0 MB");
    expect(formatBytes(2 * 1024 ** 3)).toBe("2.0 GB");
    expect(formatBytes(Number.NaN)).toBe("—");
  });

  it("秒數超過一分鐘才帶分鐘", () => {
    expect(formatDuration(5000)).toBe("5s");
    expect(formatDuration(63_000)).toBe("1m 03s");
  });

  it("進度條寬度固定，比例夾在 0..1", () => {
    expect(renderBar(0, 10)).toBe("░".repeat(10));
    expect(renderBar(1, 10)).toBe("█".repeat(10));
    expect(renderBar(2, 10)).toBe("█".repeat(10));
    expect(renderBar(-1, 10)).toBe("░".repeat(10));
    expect(renderBar(0.5, 10)).toHaveLength(10);
  });
});

describe("橫幅", () => {
  it("互動終端印圖樣，非互動一行都不印", () => {
    expect(banner({ version: "1.2.3", stream: fakeStream(true), env: {} }).length).toBeGreaterThan(
      5,
    );
    expect(banner({ version: "1.2.3", stream: fakeStream(false), env: {} })).toEqual([]);
  });

  it("圖樣寬度守在 80 欄以內", () => {
    const lines = banner({ version: "1.2.3", stream: fakeStream(true), env: { NO_COLOR: "1" } });
    for (const line of lines) expect(line.length).toBeLessThanOrEqual(80);
  });

  it("視窗比圖樣窄時退成一行，不畫出被折爛的圖", () => {
    const narrow = { ...fakeStream(true), columns: 40 };
    const lines = banner({ version: "1.2.3", stream: narrow, env: { NO_COLOR: "1" } });
    for (const line of lines) expect(line.length).toBeLessThanOrEqual(40);
    expect(lines.join("\n")).toContain("auto-ticket 1.2.3");
    expect(lines.join("\n")).not.toContain("███");
  });
});

describe("進度列：非互動輸出", () => {
  const plainArgs = (stream, now) => ({
    label: "Downloading runtime",
    stream,
    env: {},
    now,
    setIntervalImpl: () => null,
    clearIntervalImpl: () => {},
    signalSource: { once: () => {}, removeListener: () => {} },
  });

  it("不吐任何 ANSI 控制碼", () => {
    const stream = fakeStream(false);
    const p = createProgress(plainArgs(stream, () => 0));
    p.start();
    p.update({ loaded: 100, total: 1000 });
    p.done("好了");
    expect(ESC_RE.test(stream.text)).toBe(false);
  });

  it("下載過程會持續補行，不是整段靜默", () => {
    let t = 0;
    const stream = fakeStream(false);
    const p = createProgress(plainArgs(stream, () => t));
    p.start();
    // 每次推進 1%：只靠比例門檻不會補行，但時間門檻會。
    for (let i = 1; i <= 20; i += 1) {
      t += 3000;
      p.update({ loaded: i * 10, total: 1000 });
    }
    p.done("好了");
    const lines = stream.text.trim().split("\n");
    expect(lines.length).toBeGreaterThan(3);
    expect(stream.text).toContain("%");
  });

  it("沒有 content-length 時只報已下載量，不假裝算得出百分比", () => {
    let t = 0;
    const stream = fakeStream(false);
    const p = createProgress(plainArgs(stream, () => t));
    p.start();
    t += 20_000;
    p.update({ loaded: 4096, total: null });
    expect(stream.text).toContain("4.0 KB");
    expect(stream.text).not.toContain("%");
    p.stop();
  });
});

describe("進度列：互動輸出", () => {
  const ttyArgs = (stream, extra = {}) => ({
    label: "Downloading runtime",
    stream,
    env: {},
    now: () => 1000,
    setIntervalImpl: () => ({ unref: () => {} }),
    clearIntervalImpl: () => {},
    signalSource: { once: () => {}, removeListener: () => {} },
    ...extra,
  });

  it("藏游標後一定會還回去", () => {
    const stream = fakeStream(true);
    const p = createProgress(ttyArgs(stream));
    p.start();
    expect(stream.text).toContain("\u001B[?25l");
    p.done("好了");
    expect(stream.text).toContain("\u001B[?25h");
  });

  it("失敗路徑也會還游標", () => {
    const stream = fakeStream(true);
    const p = createProgress(ttyArgs(stream));
    p.start();
    p.fail("壞了");
    expect(stream.text).toContain("\u001B[?25h");
  });

  it("Ctrl-C 打斷時還游標並以 130 結束", () => {
    const stream = fakeStream(true);
    const handlers = {};
    const exitImpl = vi.fn();
    const p = createProgress(
      ttyArgs(stream, {
        signalSource: {
          once: (sig, fn) => {
            handlers[sig] = fn;
          },
          removeListener: () => {},
        },
        exitImpl,
      }),
    );
    p.start();
    handlers.SIGINT("SIGINT");
    expect(stream.text).toContain("\u001B[?25h");
    expect(exitImpl).toHaveBeenCalledWith(130);
  });

  it("stop() 之後會把信號監聽器拆掉，不留給 supervisor 收拾", () => {
    const stream = fakeStream(true);
    const removed = [];
    const p = createProgress(
      ttyArgs(stream, {
        signalSource: { once: () => {}, removeListener: (sig) => removed.push(sig) },
      }),
    );
    p.start();
    p.stop();
    expect(removed.sort()).toEqual(["SIGINT", "SIGTERM"]);
  });

  it("切到沒有位元組可數的階段時，不再擺一根停在 100% 的進度條", () => {
    const stream = fakeStream(true);
    const p = createProgress(ttyArgs(stream));
    p.start();
    p.update({ loaded: 1000, total: 1000 });
    stream.chunks.length = 0;
    p.setLabel("解壓 runtime", { indeterminate: true });
    expect(stream.text).toContain("解壓 runtime");
    expect(stream.text).not.toContain("█");
    expect(stream.text).not.toContain("%");
    p.stop();
  });

  it("stop() 之後的 update 不再輸出任何東西", () => {
    const stream = fakeStream(true);
    const p = createProgress(ttyArgs(stream));
    p.start();
    p.stop();
    stream.chunks.length = 0;
    p.update({ loaded: 5, total: 10 });
    p.setLabel("還在動");
    expect(stream.text).toBe("");
  });
});

describe("進度列：不得超出視窗寬度", () => {
  // 互動模式下重畫是 spinner 計時器在做（每個 chunk 都重畫太浪費），
  // 所以測試得自己抓住那一拍手動觸發。
  // 時鐘必須真的走，否則速率與預估剩餘時間都不會出現，測到的就只是半截短行——
  // 實機上那一行連速率帶 ETA 是 93 欄，正是會折行的那個長度。
  const clock = { t: 0 };
  const args = (stream, ticks) => ({
    label: "下載 runtime 0.3.0",
    stream,
    env: {},
    now: () => clock.t,
    setIntervalImpl: (fn) => {
      ticks.push(fn);
      return { unref: () => {} };
    },
    clearIntervalImpl: () => {},
    signalSource: { once: () => {}, removeListener: () => {} },
  });
  const tick = (ticks) => ticks.forEach((fn) => fn());

  // 折行是這條路徑最糟的失敗：ESC[2K 只清得掉游標所在的那一行，內容一旦折到
  // 第二行，每次重畫都再折一次而舊的留在上面，整個畫面會被同一行洗版到滿。
  for (const columns of [40, 60, 72, 80, 100, 200]) {
    it(`${columns} 欄的視窗裡，每一幀都放得下`, () => {
      const ticks = [];
      clock.t = 0;
      const stream = fakeStream(true, columns);
      const p = createProgress(args(stream, ticks));
      p.start();
      clock.t = 44_000; // 1.6MB / 44s ≈ 38 KB/s，剩餘時間就是三位數分鐘
      p.update({ loaded: 1.6 * 1024 * 1024, total: 254_351_945 });
      tick(ticks);
      p.update({ loaded: 120 * 1024 * 1024, total: 254_351_945 });
      tick(ticks);
      p.setLabel("解壓 runtime", { indeterminate: true });
      p.done("runtime 0.3.0 就緒（darwin-arm64）");

      for (const frame of stream.text.split("\u001B[2K\u001B[0G")) {
        const visible = stripAnsi(frame).replace(/\n/g, "");
        expect(
          displayWidth(visible),
          `這一幀寬 ${displayWidth(visible)} 欄，超過 ${columns}：${visible}`,
        ).toBeLessThan(columns);
      }
    });
  }

  it("窄到只剩百分比時，先丟預估剩餘時間、再丟速率，不丟已下載量", () => {
    const ticks = [];
    clock.t = 0;
    const stream = fakeStream(true, 46);
    const p = createProgress(args(stream, ticks));
    p.start();
    clock.t = 44_000;
    p.update({ loaded: 1.6 * 1024 * 1024, total: 254_351_945 });
    stream.chunks.length = 0;
    tick(ticks);
    const visible = stripAnsi(stream.text);
    expect(visible).toContain("%");
    expect(visible).toContain("243 MB");
    expect(visible).not.toContain("剩");
    p.stop();
  });

  it("拿不到 columns 時退回 80 欄，不是無限寬", () => {
    const ticks = [];
    clock.t = 0;
    const stream = fakeStream(true); // 沒有 columns
    const p = createProgress(args(stream, ticks));
    p.start();
    clock.t = 44_000;
    p.update({ loaded: 1.6 * 1024 * 1024, total: 254_351_945 });
    tick(ticks);
    for (const frame of stream.text.split("\u001B[2K\u001B[0G")) {
      expect(displayWidth(stripAnsi(frame))).toBeLessThan(80);
    }
    p.stop();
  });
});

describe("寬度計算", () => {
  it("全形字算兩欄", () => {
    expect(displayWidth("abc")).toBe(3);
    expect(displayWidth("下載")).toBe(4);
    expect(displayWidth("下載 runtime")).toBe(12);
  });

  it("截斷不切半個全形字", () => {
    expect(truncateToWidth("下載 runtime", 5)).toBe("下載 ");
    expect(truncateToWidth("下載 runtime", 3)).toBe("下");
    expect(truncateToWidth("abc", 0)).toBe("");
  });
});

describe("進度列：速率", () => {
  const args = (stream, now) => ({
    label: "下載",
    stream,
    env: {},
    now,
    setIntervalImpl: () => null,
    clearIntervalImpl: () => {},
    signalSource: { once: () => {}, removeListener: () => {} },
  });

  it("剛起步時不報速率與 ETA，因為那是拿兩個雜訊相除", () => {
    let t = 0;
    const stream = fakeStream(false);
    const p = createProgress(args(stream, () => t));
    p.start();
    stream.chunks.length = 0;
    t += 20; // 20ms 收到 1.4KB——實測會算出「剩 3201m 12s」
    p.update({ loaded: 1400, total: 254_351_945 });
    expect(stream.text).toContain("0%");
    expect(stream.text).not.toContain("/s");
    expect(stream.text).not.toContain("剩");
    p.stop();
  });

  it("累積到足夠樣本之後才開始報速率", () => {
    let t = 0;
    const stream = fakeStream(false);
    const p = createProgress(args(stream, () => t));
    p.start();
    t += 2000;
    stream.chunks.length = 0;
    p.update({ loaded: 2 * 1024 * 1024, total: 254_351_945 });
    expect(stream.text).toContain("/s");
    expect(stream.text).toContain("剩");
    p.stop();
  });

  it("重試歸零後，速率以新的一次嘗試計算，不被失敗那次拖低", () => {
    let t = 0;
    const stream = fakeStream(false);
    const p = createProgress({
      label: "下載",
      stream,
      env: {},
      now: () => t,
      setIntervalImpl: () => null,
      clearIntervalImpl: () => {},
      signalSource: { once: () => {}, removeListener: () => {} },
    });
    p.start();
    t += 100_000; // 一次很慢、最後失敗的嘗試
    p.update({ loaded: 1024, total: 2 * 1024 * 1024 });
    p.reset();
    t += 2000; // 重試後 2 秒下了 2MB
    stream.chunks.length = 0;
    p.update({ loaded: 2 * 1024 * 1024, total: 2 * 1024 * 1024 });
    // 沒歸零的話分母會是 102 秒，算出來是 20 KB/s 這個量級的假數字。
    expect(stream.text).toContain("1.0 MB/s");
    p.stop();
  });
});
