// 終端機顯示層：橫幅、spinner、進度列。純輸出，不做任何 I/O 決策，也不 import 其他 CLI 模組。
//
// 這裡刻意不引入任何第三方 TUI 套件。`dependencies` 恆為空物件是這個套件的硬約束
// （見 guardrails 測試）：npx 的第一印象就是安裝速度，而且每多一個傳遞依賴，就多一條
// 能在使用者機器上執行程式碼的供應鏈路徑——這支 CLI 會碰到帳密與付款，不值得為了
// 一根進度列去換。下面全部只用 ANSI escape，總共不到兩百行。

const ESC = "\u001B[";
const CLEAR_LINE = `${ESC}2K${ESC}0G`;
const HIDE_CURSOR = `${ESC}?25l`;
const SHOW_CURSOR = `${ESC}?25h`;

const SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];
const SPINNER_INTERVAL_MS = 80;

const BAR_WIDTH = 24;
// 拿不到終端機寬度時的保守假設；VT100 以來的預設就是 80。
const DEFAULT_COLUMNS = 80;

// 非互動終端（重導到檔案、CI）不能靠覆寫同一行，只能每隔一段距離補一行。
const PLAIN_STEP_RATIO = 0.05;
const PLAIN_STEP_MS = 10_000;

// 剛起步時「已下載量 / 耗時」是拿兩個雜訊相除。實測第一幀會算出
// `1.3 KB/s  剩 3201m 12s`——那是使用者看到的第一行，寧可先不報。
const RATE_MIN_ELAPSED_MS = 1500;
const RATE_MIN_BYTES = 256 * 1024;

const BANNER_LINES = [
  " ███  █   █ █████  ███    █████ ███  ████ █   █ █████ █████",
  "█   █ █   █   █   █   █     █    █  █     █  █  █       █  ",
  "█████ █   █   █   █   █     █    █  █     ███   ████    █  ",
  "█   █ █   █   █   █   █     █    █  █     █  █  █       █  ",
  "█   █  ███    █    ███      █   ███  ████ █   █ █████   █  ",
];

const BANNER_WIDTH = Math.max(...BANNER_LINES.map((line) => line.length));

/**
 * 能不能做動畫（覆寫同一行、轉 spinner、藏游標）。
 *
 * 只要輸出不是 TTY，一切覆寫控制碼都會原封不動寫進檔案，把日誌變成亂碼——
 * 這正是先前把 `start` 導到檔案後看不到任何進度的那個坑的反面：不是不輸出，
 * 而是要換一種輸出。
 */
export function isInteractive({ stream = process.stderr, env = process.env } = {}) {
  if (!stream?.isTTY) return false;
  if (env.TERM === "dumb") return false;
  if (env.CI) return false;
  return true;
}

/** 顏色與動畫分開判斷：NO_COLOR 只表示不要顏色，不表示不要進度。 */
export function supportsColor({ stream = process.stderr, env = process.env } = {}) {
  if (env.NO_COLOR) return false;
  return isInteractive({ stream, env });
}

const paint = (on, code, text) => (on ? `${ESC}${code}m${text}${ESC}0m` : text);

/**
 * 字串實際佔幾欄。中日韓字元是全形，一個字佔兩欄——用 `.length` 量會少算，
 * 於是自以為放得下、實際卻折行。
 */
export function displayWidth(text) {
  let width = 0;
  for (const ch of text) {
    const c = ch.codePointAt(0);
    const wide =
      (c >= 0x1100 && c <= 0x115f) ||
      (c >= 0x2e80 && c <= 0xa4cf) ||
      (c >= 0xac00 && c <= 0xd7a3) ||
      (c >= 0xf900 && c <= 0xfaff) ||
      (c >= 0xfe30 && c <= 0xfe6f) ||
      (c >= 0xff00 && c <= 0xff60) ||
      (c >= 0xffe0 && c <= 0xffe6);
    width += wide ? 2 : 1;
  }
  return width;
}

/** 截到指定欄寬為止，不切半個全形字。 */
export function truncateToWidth(text, maxWidth) {
  if (maxWidth <= 0) return "";
  let out = "";
  let width = 0;
  for (const ch of text) {
    const next = width + displayWidth(ch);
    if (next > maxWidth) break;
    out += ch;
    width = next;
  }
  return out;
}

/**
 * 啟動橫幅。只在互動終端印出；輸出被重導到檔案或跑在 CI 時回傳空陣列——
 * 一整片方塊字元在日誌裡只是噪音，而版本資訊 supervisor 的日誌本來就有。
 */
export function banner({ version, stream = process.stderr, env = process.env } = {}) {
  if (!isInteractive({ stream, env })) return [];
  const tag = version ? `auto-ticket ${version}` : "auto-ticket";
  const color = supportsColor({ stream, env });
  // 視窗比圖樣窄的話，終端機會把每一行折成兩段，圖就徹底看不出是字了。
  // 分割視窗、SSH 進小終端都常見，那時退成一行字比硬畫好。
  const columns = stream.columns ?? 80;
  if (columns < BANNER_WIDTH) {
    return ["", paint(color, "36", `▌ ${tag}`), ""];
  }
  const art = BANNER_LINES.map((line) => paint(color, "36", line));
  return ["", ...art, "", paint(color, "2", `  ${tag}  ·  本機執行，資料不出你的機器`), ""];
}

export function writeBanner({ version, stream = process.stderr, env = process.env } = {}) {
  for (const line of banner({ version, stream, env })) stream.write(`${line}\n`);
}

/** 1 KB = 1024 B，小數位隨量級縮減，讓寬度穩定不跳動。 */
export function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = unit === 0 ? 0 : value >= 100 ? 0 : 1;
  return `${value.toFixed(digits)} ${units[unit]}`;
}

export function formatDuration(ms) {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const total = Math.round(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m > 0 ? `${m}m ${String(s).padStart(2, "0")}s` : `${s}s`;
}

export function renderBar(ratio, width = BAR_WIDTH) {
  const filled = Math.max(0, Math.min(width, Math.round(ratio * width)));
  return `${"█".repeat(filled)}${"░".repeat(width - filled)}`;
}

/**
 * 一個可重入的進度回報器。
 *
 * `update()` 由下載迴圈高頻呼叫（每個 chunk 一次），所以它自己節流：互動模式下
 * 以 spinner 的節拍重畫，非互動模式下只在跨過 5% 或 10 秒時補一行。呼叫端不必知道
 * 自己身處哪一種終端。
 *
 * `total` 可以是 null——HTTP 回應不一定給 content-length，那時退成「只報已下載量」，
 * 不假裝算得出百分比。
 */
export function createProgress({
  label,
  stream = process.stderr,
  env = process.env,
  now = () => Date.now(),
  setIntervalImpl = setInterval,
  clearIntervalImpl = clearInterval,
  signalSource = process,
  exitImpl = (code) => process.exit(code),
} = {}) {
  const interactive = isInteractive({ stream, env });
  const color = supportsColor({ stream, env });

  // 速率的分母是「這一次嘗試」的耗時，不是整段歷程：重試會整檔重下，
  // 把失敗那次的時間算進去只會算出一個永遠偏低的假速率與假 ETA。
  let windowStart = now();
  let currentLabel = label;
  let indeterminate = false;
  let loaded = 0;
  let total = null;
  let frame = 0;
  let timer = null;
  let finished = false;
  let lastPlainAt = 0;
  let lastPlainRatio = -1;
  let cursorHidden = false;
  let onInterrupt = null;

  const rate = () => {
    const elapsed = now() - windowStart;
    if (elapsed < RATE_MIN_ELAPSED_MS || loaded < RATE_MIN_BYTES) return 0;
    return (loaded / elapsed) * 1000;
  };

  /**
   * 組出細節文字，放不下就從尾端依序捨棄。
   *
   * 折行是這條路徑上最糟的失敗：`ESC[2K` 只清得掉游標所在的那一行，一旦內容
   * 折到第二行，每次重畫都會再折一次而舊的留在上面——畫面會被同一行進度洗版
   * 到滿。寧可少顯示幾項，也不能讓它超出視窗寬度。
   */
  function body(budget = Infinity) {
    if (indeterminate) return "";
    const speed = rate();
    if (!total) {
      const parts = [formatBytes(loaded)];
      if (speed > 0) parts.push(`${formatBytes(speed)}/s`);
      return fitParts(parts, budget);
    }
    const ratio = Math.min(1, loaded / total);
    const pct = `${String(Math.floor(ratio * 100)).padStart(3)}%`;
    const bytes = `${formatBytes(loaded)} / ${formatBytes(total)}`;
    const rest = [];
    if (speed > 0) {
      rest.push(`${formatBytes(speed)}/s`);
      rest.push(`剩 ${formatDuration(((total - loaded) / speed) * 1000)}`);
    }
    const render = (barWidth, drop) =>
      [
        ...(barWidth > 0 ? [renderBar(ratio, barWidth)] : []),
        pct,
        bytes,
        ...rest.slice(0, rest.length - drop),
      ].join("  ");

    // 退讓順序：先縮短進度條，再丟預估剩餘時間，再丟速率，最後才拿掉進度條。
    // 慢速線路上使用者盯的是速率與剩餘時間，一根寬條沒有那麼重要——百分比已經
    // 說了同一件事。
    for (let drop = 0; drop <= rest.length; drop += 1) {
      for (const barWidth of [BAR_WIDTH, 12]) {
        const text = render(barWidth, drop);
        if (displayWidth(text) <= budget) return text;
      }
    }
    for (let drop = 0; drop <= rest.length; drop += 1) {
      const text = render(0, drop);
      if (displayWidth(text) <= budget) return text;
    }
    return truncateToWidth(`${pct}  ${bytes}`, budget);
  }

  function fitParts(parts, budget) {
    for (let drop = 0; drop < parts.length; drop += 1) {
      const text = parts.slice(0, parts.length - drop).join("  ");
      if (displayWidth(text) <= budget) return text;
    }
    return truncateToWidth(parts[0] ?? "", budget);
  }

  function paintLine() {
    // 每次重畫都重讀寬度：使用者隨時可能拉動視窗。留一欄不用，因為有些終端機
    // 寫滿最後一欄就會自動換行。
    const columns = (stream.columns ?? DEFAULT_COLUMNS) - 1;
    const spin = SPINNER_FRAMES[frame % SPINNER_FRAMES.length];
    const label = truncateToWidth(currentLabel, Math.max(0, columns - 2));
    const used = displayWidth(spin) + 1 + displayWidth(label);
    const detail = body(Math.max(0, columns - used - 2));
    stream.write(
      `${CLEAR_LINE}${paint(color, "36", spin)} ${label}${
        detail ? `  ${paint(color, "2", detail)}` : ""
      }`,
    );
  }

  function tick() {
    frame += 1;
    paintLine();
  }

  function maybePlainLine(force) {
    const at = now();
    const ratio = total ? loaded / total : 0;
    const steppedRatio = total && ratio - lastPlainRatio >= PLAIN_STEP_RATIO;
    const steppedTime = at - lastPlainAt >= PLAIN_STEP_MS;
    if (!force && !steppedRatio && !steppedTime) return;
    lastPlainAt = at;
    lastPlainRatio = ratio;
    const detail = body();
    stream.write(`${currentLabel}${detail ? `  ${detail}` : ""}\n`);
  }

  /**
   * 停掉動畫並把游標放回來。任何離開路徑都必須經過這裡——包含 Ctrl-C：
   * 藏了游標卻沒還，使用者的終端機會在我們結束後繼續看不到游標。
   */
  function stop() {
    if (finished) return;
    finished = true;
    if (timer) clearIntervalImpl(timer);
    timer = null;
    if (onInterrupt) {
      signalSource?.removeListener?.("SIGINT", onInterrupt);
      signalSource?.removeListener?.("SIGTERM", onInterrupt);
      onInterrupt = null;
    }
    if (cursorHidden) {
      stream.write(`${CLEAR_LINE}${SHOW_CURSOR}`);
      cursorHidden = false;
    }
  }

  return {
    /** 開始動畫；非互動模式只印一行起手句。 */
    start() {
      if (finished) return;
      if (!interactive) {
        stream.write(`${currentLabel}…\n`);
        lastPlainAt = now();
        return;
      }
      stream.write(HIDE_CURSOR);
      cursorHidden = true;
      // Node 對 SIGINT 的預設處置是直接終止，不會發 'exit'——沒有這個接管，
      // 下載中按 Ctrl-C 就會把使用者的終端機留在「游標消失」的狀態。
      onInterrupt = (signal) => {
        stop();
        exitImpl(signal === "SIGTERM" ? 143 : 130);
      };
      signalSource?.once?.("SIGINT", onInterrupt);
      signalSource?.once?.("SIGTERM", onInterrupt);
      paintLine();
      timer = setIntervalImpl(tick, SPINNER_INTERVAL_MS);
      timer?.unref?.();
    },
    update({ loaded: nextLoaded, total: nextTotal } = {}) {
      if (finished) return;
      if (Number.isFinite(nextLoaded)) loaded = nextLoaded;
      if (Number.isFinite(nextTotal) && nextTotal > 0) total = nextTotal;
      if (!interactive) maybePlainLine(false);
    },
    /**
     * 換到下一個階段。`indeterminate` 用在沒有位元組可數的步驟（解壓、提交）：
     * 那時只轉 spinner，不要擺一根永遠停在 100% 的進度列。
     */
    setLabel(next, options = {}) {
      if (finished) return;
      currentLabel = next;
      indeterminate = Boolean(options.indeterminate);
      if (indeterminate) {
        loaded = 0;
        total = null;
      }
      if (interactive) paintLine();
      else stream.write(`${next}…\n`);
    },
    /** 重試時下載會從頭開始，計數器與速率視窗都要跟著歸零。 */
    reset() {
      loaded = 0;
      windowStart = now();
      lastPlainRatio = -1;
    },
    done(message) {
      stop();
      const mark = paint(color, "32", "✔");
      message = interactive
        ? truncateToWidth(message, (stream.columns ?? DEFAULT_COLUMNS) - 3)
        : message;
      stream.write(interactive ? `${CLEAR_LINE}${mark} ${message}\n` : `${message}\n`);
    },
    fail(message) {
      stop();
      const mark = paint(color, "31", "✖");
      message = interactive
        ? truncateToWidth(message, (stream.columns ?? DEFAULT_COLUMNS) - 3)
        : message;
      stream.write(interactive ? `${CLEAR_LINE}${mark} ${message}\n` : `${message}\n`);
    },
    stop,
  };
}
