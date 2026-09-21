#!/usr/bin/env node
/**
 * 契約漂移守門員：前端 lib/contract.ts 的 `as const` 陣列必須與後端 Python enum
 * 逐值相等（集合相等，不是子集）。呼應後端 scripts/check_invariants.py 的 G1-G32 文化。
 */
import { readFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..")
const repoRoot = resolve(webRoot, "..")

function readRepo(relative) {
  return readFileSync(resolve(repoRoot, relative), "utf8")
}

/** 抽出某個 `class <Name>(str, Enum):` 區塊裡所有 `MEMBER = "value"` 的 value。 */
function pyEnumValues(source, className) {
  const start = source.indexOf(`class ${className}(str, Enum):`)
  if (start === -1) throw new Error(`enum class not found: ${className}`)
  const rest = source.slice(start)
  const nextClass = rest.slice(1).search(/^class /m)
  const body = nextClass === -1 ? rest : rest.slice(0, nextClass + 1)
  const values = []
  for (const line of body.split("\n").slice(1)) {
    if (/^\S/.test(line) && line.trim() !== "") break
    const m = line.match(/^\s+[A-Za-z_][A-Za-z0-9_]* = "([^"]+)"/)
    if (m) values.push(m[1])
  }
  if (values.length === 0) throw new Error(`no members parsed for ${className}`)
  return values
}

/** 抽出 `NAME = frozenset({ PurchaseState.X, ... })` 的成員名。 */
function pyFrozensetMembers(source, constName, enumName) {
  const m = source.match(
    new RegExp(`${constName}[^=]*=\\s*frozenset\\(\\{([\\s\\S]*?)\\}\\)`)
  )
  if (!m) throw new Error(`frozenset not found: ${constName}`)
  const re = new RegExp(`${enumName}\\.([A-Z_][A-Z0-9_]*)`, "g")
  return [...m[1].matchAll(re)].map((x) => x[1])
}

/** 抽出 `CODE_X = "value"` 這類模組層級常數。 */
function pyConstValues(source, prefix) {
  const re = new RegExp(`^${prefix}[A-Z0-9_]* = "([^"]+)"`, "gm")
  return [...source.matchAll(re)].map((x) => x[1])
}

/** 抽出 `field: Literal["a", "b"]` 的字面值。Literal 不是 Enum，前一個抽取器讀不到。 */
function pyLiteralValues(source, className, fieldName) {
  const start = source.indexOf(`class ${className}(`)
  if (start === -1) throw new Error(`class not found: ${className}`)
  const rest = source.slice(start)
  const nextClass = rest.slice(1).search(/^class /m)
  const body = nextClass === -1 ? rest : rest.slice(0, nextClass + 1)
  const m = body.match(new RegExp(`${fieldName}\\s*:\\s*Literal\\[([^\\]]*)\\]`))
  if (!m) throw new Error(`literal field not found: ${className}.${fieldName}`)
  return [...m[1].matchAll(/"([^"]+)"/g)].map((x) => x[1])
}

/** 抽出 `export const NAME = [...] as const` 的字串字面值。 */
function tsConstArray(source, name) {
  const m = source.match(
    new RegExp(`export const ${name} = \\[([\\s\\S]*?)\\] as const`)
  )
  if (!m) throw new Error(`ts const array not found: ${name}`)
  return [...m[1].matchAll(/"([^"]+)"/g)].map((x) => x[1])
}

const states = readRepo("src/fsm/states.py")
const task = readRepo("src/domain/task.py")
const jobs = readRepo("src/broker/jobs.py")
const ws = readRepo("src/api/schemas/ws.py")
const errors = readRepo("src/api/errors.py")
const preference = readRepo("src/domain/preference.py")
const contract = readFileSync(resolve(webRoot, "lib/contract.ts"), "utf8")

const checks = [
  ["TASK_STATUS", pyEnumValues(task, "TaskStatus")],
  ["PURCHASE_STATE", pyEnumValues(states, "PurchaseState")],
  ["PURCHASE_EVENT", pyEnumValues(states, "PurchaseEvent")],
  [
    "PURCHASE_FINAL_STATE",
    pyFrozensetMembers(states, "FINAL_STATES", "PurchaseState"),
  ],
  ["JOB_STATE", pyEnumValues(jobs, "JobState")],
  ["SERVER_MESSAGE_TYPE", pyEnumValues(ws, "ServerMessageType")],
  ["CLIENT_ACTION", pyEnumValues(ws, "ClientAction")],
  ["API_ERROR_CODE", pyConstValues(errors, "CODE_")],
  ["PLATFORM", pyEnumValues(readRepo("src/domain/event.py"), "PlatformEnum")],
  [
    "SEAT_STRATEGY",
    pyLiteralValues(preference, "SeatPreference", "strategy"),
  ],
  [
    "TICKET_PRICE_ORDER",
    pyLiteralValues(preference, "TicketRule", "price_order"),
  ],
]

let failed = false
for (const [name, backend] of checks) {
  const frontend = tsConstArray(contract, name)
  const missing = backend.filter((v) => !frontend.includes(v))
  const extra = frontend.filter((v) => !backend.includes(v))
  if (missing.length || extra.length) {
    failed = true
    console.error(`DRIFT ${name}:`)
    if (missing.length)
      console.error(`  missing in frontend: ${missing.join(", ")}`)
    if (extra.length)
      console.error(`  extra in frontend:    ${extra.join(", ")}`)
  }
}

if (failed) {
  console.error("\nFAIL: frontend contract drifted from backend enums")
  process.exit(1)
}

console.log("OK: frontend contract mirrors backend enums")
