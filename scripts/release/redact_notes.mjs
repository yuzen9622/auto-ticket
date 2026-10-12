#!/usr/bin/env node
/**
 * 公開 Release notes 保留經敏感詞遮蔽的 changelog，附上安裝與更新方法。
 * 先移除既有頁尾再重新產生，避免重跑發布流程時累積重複區塊。
 *
 * 用法：
 *     node scripts/release/redact_notes.mjs --in notes.md --out public-notes.md
 *     cat notes.md | node scripts/release/redact_notes.mjs
 */

import { readFileSync, writeFileSync } from "node:fs"
import { sensitiveTermRe } from "./sensitive-terms.mjs"

const KEPT_SECTIONS = ["Features", "Bug Fixes", "Performance Improvements"]
const REDACTED_LINE = "* 內部穩定性與相容性改善"

const PUBLIC_NOTES = [
  "## 安裝",
  "",
  "使用 npx：",
  "",
  "```bash",
  "npx @yuzen9622/auto-ticket",
  "```",
  "",
  "或使用 pnpm：",
  "",
  "```bash",
  "pnpm dlx @yuzen9622/auto-ticket",
  "```",
  "",
  "## 更新",
  "",
  "```bash",
  "autix update",
  "```",
  "",
].join("\n")

export function redactNotes(input) {
  const out = []
  let keeping = true
  for (const line of String(input).split("\n")) {
    // 舊頁尾沒有 Markdown 標題；新頁尾從「安裝」標題開始。
    // 兩者都只屬於產生器的頁尾，不是 changelog。
    if (/^(?:本工具的付款一律為 mock：|安裝：\s*$|#{1,4}\s+(?:安裝|更新)\s*$)/.test(line)) break
    const heading = line.match(/^#{2,4}\s+(.*?)\s*$/)?.[1]
    if (heading !== undefined) {
      keeping = /^\[?v?\d+\.\d+\.\d+/.test(heading)
        || KEPT_SECTIONS.some((section) => heading.toLowerCase().includes(section.toLowerCase()))
    }
    if (!keeping) continue
    if (/^\s*[*-]\s+/.test(line) && sensitiveTermRe().test(line)) {
      if (out.at(-1) !== REDACTED_LINE) out.push(REDACTED_LINE)
    } else if (line.trim() !== "" || out.at(-1)?.trim()) {
      out.push(line)
    }
  }
  while (out.length && (out.at(-1).trim() === "" || out.at(-1).trim() === "---")) out.pop()
  const changelog = out.join("\n")
  return changelog ? `${changelog}\n\n${PUBLIC_NOTES}` : PUBLIC_NOTES
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
