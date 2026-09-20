export const DEFAULT_PROFILE = "live"
export const BROWSER_PROFILES_KEY = "auto-ticket.browser-profiles.v1"
export const CURRENT_PROFILE_KEY = "auto-ticket.current-profile.v1"

const PROFILE_NAME_PATTERN = /^[a-zA-Z0-9_-]{1,32}$/

export function isValidProfileName(name: string): boolean {
  return PROFILE_NAME_PATTERN.test(name.trim())
}

function isBrowser(): boolean {
  return typeof window !== "undefined"
}

export function loadBrowserProfiles(): string[] {
  if (!isBrowser()) return [DEFAULT_PROFILE]
  let raw: string | null
  try {
    raw = window.localStorage.getItem(BROWSER_PROFILES_KEY)
  } catch {
    return [DEFAULT_PROFILE]
  }
  if (!raw) return [DEFAULT_PROFILE]

  try {
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return [DEFAULT_PROFILE]
    const valid = parsed.flatMap((x) =>
      typeof x === "string" && isValidProfileName(x) ? [x.trim()] : []
    )
    return Array.from(new Set([DEFAULT_PROFILE, ...valid]))
  } catch {
    return [DEFAULT_PROFILE]
  }
}

export function saveBrowserProfile(name: string): string[] {
  const trimmed = name.trim()
  if (!isValidProfileName(trimmed)) {
    throw new Error("Invalid profile name")
  }
  const current = loadBrowserProfiles()
  if (!current.includes(trimmed)) {
    current.push(trimmed)
    writeProfiles(current)
  }
  return current
}

export function removeBrowserProfile(name: string): string[] {
  const trimmed = name.trim()
  if (trimmed === DEFAULT_PROFILE) {
    return loadBrowserProfiles()
  }
  const next = loadBrowserProfiles().filter((p) => p !== trimmed)
  writeProfiles(next)
  return next
}

export function loadCurrentBrowserProfile(): string {
  if (!isBrowser()) return DEFAULT_PROFILE
  try {
    const current = window.localStorage.getItem(CURRENT_PROFILE_KEY)
    if (current && isValidProfileName(current)) {
      return current.trim()
    }
  } catch {
    // ignore
  }
  return DEFAULT_PROFILE
}

export function saveCurrentBrowserProfile(name: string): void {
  if (!isBrowser()) return
  const trimmed = name.trim()
  if (!isValidProfileName(trimmed)) return
  try {
    window.localStorage.setItem(CURRENT_PROFILE_KEY, trimmed)
  } catch {
    // ignore
  }
}

function writeProfiles(profiles: string[]): void {
  if (!isBrowser()) return
  try {
    window.localStorage.setItem(BROWSER_PROFILES_KEY, JSON.stringify(profiles))
  } catch {
    // ignore
  }
}
