#!/usr/bin/env node
/**
 * 公開 Release notes 的遮蔽器。
 *
 * Release notes 是這個專案公開面上最多人會看的一頁。它直接由 commit subject 組成，
 * 而 commit subject 裡最容易夾帶的就是「我們在盯哪裡」——選擇器、風控機制、重試與
 * 預熱時序。那些東西對使用者沒有意義，對票務平台卻很有意義。
 *
 * 遮蔽器只保護這一頁。commit 歷史本身追不回來，真正的長期控制是
 * `commit-hygiene` 在 PR 階段就擋下敏感 subject（見 CONTRIBUTING.md）。
 *
 * 用法：
 *     node scripts/release/redact_notes.mjs --in notes.md --out public-notes.md
 *     cat notes.md | node scripts/release/redact_notes.mjs
 */

import { readFileSync, writeFileSync } from "node:fs"

import { sensitiveTermRe } from "./sensitive-terms.mjs"

/** conventional-changelog 的 section 標題；只有這三個對使用者有意義。 */
export const KEPT_SECTIONS = [
  "Features",
  "Bug Fixes",
  "Performance Improvements",
]

export const REDACTED_LINE = "* 內部穩定性與相容性改善"

export const FOOTER = [
  "",
  "---",
  "",
  "本工具的付款一律為 mock：不會選票、不會建立訂單、不會送出訂單、不會付款。",
  "",
  "安裝：",
  "",
  "```bash",
  "npx @yuzen9622/auto-ticket",
  "```",
  "",
]

const HEADING_RE = /^#{2,4}\s+(.*?)\s*$/
const BULLET_RE = /^\s*[*-]\s+/

function isKeptSection(heading) {
  return KEPT_SECTIONS.some((s) => heading.toLowerCase().includes(s.toLowerCase()))
}

/** 版本標題（`## [1.2.3](...)`）長得像 section 標題，但不是——它永遠要留下。 */
function isVersionHeading(heading) {
  return /^\[?\d+\.\d+\.\d+/.test(heading) || /^v?\d+\.\d+\.\d+/.test(heading)
}

export function redactNotes(input) {
  const lines = String(input).split("\n")
  const out = []
  let keeping = true

  for (const line of lines) {
    const heading = line.match(HEADING_RE)?.[1]
    if (heading !== undefined) {
      if (isVersionHeading(heading)) {
        keeping = true
        out.push(line)
        continue
      }
      keeping = isKeptSection(heading)
      if (keeping) out.push(line)
      continue
    }

    if (!keeping) continue

    if (BULLET_RE.test(line) && sensitiveTermRe().test(line)) {
      // 整行換掉而不是遮字：把 "fix: update X selector" 打成 "fix: update X ****"
      // 一樣洩漏了「X 有選擇器要修」這件事。
      if (out.at(-1) !== REDACTED_LINE) out.push(REDACTED_LINE)
      continue
    }

    out.push(line)
  }

  // 丟掉 section 之間留下的連續空行，讀起來才不會一堆破洞。
  const compacted = []
  for (const line of out) {
    if (line.trim() === "" && compacted.at(-1)?.trim() === "") continue
    compacted.push(line)
  }
  while (compacted.at(-1)?.trim() === "") compacted.pop()

  return [...compacted, ...FOOTER].join("\n")
}

function parseArgs(argv) {
  const args = { in: null, out: null }
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--in") args.in = argv[++i]
    else if (argv[i] === "--out") args.out = argv[++i]
    else throw new Error(`未知旗標: ${argv[i]}`)
  }
  return args
}

function main(argv) {
  const args = parseArgs(argv)
  const input = readFileSync(args.in ?? 0, "utf8")
  const output = redactNotes(input)
  if (args.out) writeFileSync(args.out, output)
  else process.stdout.write(output)
  return 0
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.exit(main(process.argv.slice(2)))
}
