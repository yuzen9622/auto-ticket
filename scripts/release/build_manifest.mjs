#!/usr/bin/env node
/**
 * 由 GitHub Release 的 assets 產生 `packages/cli/runtime-manifest.json`。
 *
 * manifest 隨 npm 套件一起出貨，用意是杜絕 TOFU：sha256 的真值來自使用者已經
 * 信任的 npm registry，不是執行期再向 GitHub 問一次。CLI 因此**絕不**在執行期
 * 重新取得 digest 當真值——那等於把信任鏈的兩端接成同一個來源。
 *
 * 用法：
 *     node scripts/release/build_manifest.mjs --tag v1.0.0
 *     node scripts/release/build_manifest.mjs --verify --tag v1.0.0
 */

import { execFile } from "node:child_process"
import { readFileSync, writeFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import { promisify } from "node:util"

const execFileAsync = promisify(execFile)

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..")
const MANIFEST_PATH = resolve(REPO_ROOT, "packages/cli/runtime-manifest.json")
const PACKAGE_JSON_PATH = resolve(REPO_ROOT, "packages/cli/package.json")

export const TARGETS = ["darwin-arm64", "win32-x64"]
export const MANIFEST_SCHEMA = 1

export function assetName(target, version) {
  return `auto-ticket-runtime-${target}-${version}.tar.gz`
}

/** 從 `<hex>  <filename>` 這種 sha256sum 格式抽出雜湊。 */
export function parseSha256(text) {
  const hex = String(text).trim().split(/\s+/)[0]
  if (!/^[0-9a-f]{64}$/.test(hex)) {
    throw new Error(`不是合法的 sha256：${String(text).slice(0, 80)}`)
  }
  return hex
}

/**
 * runtime 的 Python 與 ORT 版本從它們各自的事實來源讀，不在這裡抄第二份——
 * `auto-ticket version` 與 `doctor` 印的就是這兩個值，抄錯不會有人發現。
 */
export function readRuntimeVersions(root = REPO_ROOT) {
  const builder = readFileSync(resolve(root, "packaging/runtime/build_runtime.py"), "utf8")
  const constraints = readFileSync(resolve(root, "packaging/runtime/constraints.txt"), "utf8")
  const python = builder.match(/^PYTHON_SERIES\s*=\s*"([^"]+)"/m)?.[1]
  const onnxruntime = constraints.match(/^onnxruntime==(\S+)/m)?.[1]
  if (!python || !onnxruntime) {
    throw new Error("讀不到 runtime 的 Python / onnxruntime 版本")
  }
  return { python, onnxruntime }
}

export function buildManifest({ version, tag, repo, assets, runtimeVersions }) {
  const targets = {}
  for (const target of TARGETS) {
    const file = assetName(target, version)
    const asset = assets[target]
    if (!asset) throw new Error(`Release ${tag} 缺少 ${target} 的 asset`)
    targets[target] = { file, sha256: asset.sha256, size: asset.size }
  }
  const { python, onnxruntime } = runtimeVersions ?? readRuntimeVersions()
  return {
    schema: MANIFEST_SCHEMA,
    version,
    releaseTag: tag,
    baseUrl: `https://github.com/${repo}/releases/download/${tag}`,
    python,
    onnxruntime,
    targets,
  }
}

async function gh(args) {
  const { stdout } = await execFileAsync("gh", args, { maxBuffer: 32 << 20 })
  return stdout
}

/** 讀 Release 上的 `.sha256` 附檔與主檔大小。digest 只信這份純文字檔。 */
async function readReleaseAssets({ tag, repo, version, tmpDir }) {
  const listed = JSON.parse(
    await gh(["release", "view", tag, "--repo", repo, "--json", "assets"])
  )
  const bySize = new Map(listed.assets.map((a) => [a.name, a.size]))

  const assets = {}
  for (const target of TARGETS) {
    const file = assetName(target, version)
    const shaFile = `${file}.sha256`
    if (!bySize.has(file)) throw new Error(`Release ${tag} 找不到 ${file}`)
    if (!bySize.has(shaFile)) throw new Error(`Release ${tag} 找不到 ${shaFile}`)
    await gh([
      "release",
      "download",
      tag,
      "--repo",
      repo,
      "--pattern",
      shaFile,
      "--dir",
      tmpDir,
      "--clobber",
    ])
    assets[target] = {
      sha256: parseSha256(readFileSync(resolve(tmpDir, shaFile), "utf8")),
      size: bySize.get(file),
    }
  }
  return assets
}

function parseArgs(argv) {
  const args = { tag: null, repo: "yuzen9622/auto-ticket", verify: false, out: MANIFEST_PATH }
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--tag") args.tag = argv[++i]
    else if (argv[i] === "--repo") args.repo = argv[++i]
    else if (argv[i] === "--out") args.out = argv[++i]
    else if (argv[i] === "--verify") args.verify = true
    else throw new Error(`未知旗標: ${argv[i]}`)
  }
  if (!args.tag) throw new Error("需要 --tag")
  return args
}

async function main(argv) {
  const args = parseArgs(argv)
  const version = JSON.parse(readFileSync(PACKAGE_JSON_PATH, "utf8")).version
  if (args.tag !== `v${version}`) {
    throw new Error(
      `tag ${args.tag} 與 packages/cli/package.json 的版本 ${version} 不一致`
    )
  }

  const tmpDir = process.env.RUNNER_TEMP || process.env.TMPDIR || "/tmp"
  const assets = await readReleaseAssets({ ...args, version, tmpDir })
  const manifest = buildManifest({ version, tag: args.tag, repo: args.repo, assets })

  if (args.verify) {
    const current = JSON.parse(readFileSync(args.out, "utf8"))
    let ok = 0
    for (const target of TARGETS) {
      const mine = current.targets?.[target]
      const theirs = manifest.targets[target]
      if (mine?.sha256 === theirs.sha256 && mine?.file === theirs.file) ok += 1
      else console.error(`✗ ${target}: 套件內 manifest 與 release asset 不一致`)
    }
    console.log(`${ok}/${TARGETS.length} targets verified`)
    return ok === TARGETS.length ? 0 : 1
  }

  writeFileSync(args.out, `${JSON.stringify(manifest, null, 2)}\n`)
  console.log(`寫入 ${args.out}（${TARGETS.length} targets, ${args.tag}）`)
  return 0
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main(process.argv.slice(2)).then(
    (code) => process.exit(code),
    (err) => {
      console.error(`build_manifest 失敗：${err.message}`)
      process.exit(1)
    }
  )
}
