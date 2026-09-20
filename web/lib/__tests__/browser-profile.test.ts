import { beforeEach, describe, expect, it } from "vitest"

import {
  BROWSER_PROFILES_KEY,
  CURRENT_PROFILE_KEY,
  DEFAULT_PROFILE,
  isValidProfileName,
  loadBrowserProfiles,
  loadCurrentBrowserProfile,
  removeBrowserProfile,
  saveBrowserProfile,
  saveCurrentBrowserProfile,
} from "@/lib/browser-profile"

describe("browser-profile", () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it("驗證名稱格式合法性", () => {
    expect(isValidProfileName("live")).toBe(true)
    expect(isValidProfileName("account_2")).toBe(true)
    expect(isValidProfileName("user-test-01")).toBe(true)
    expect(isValidProfileName("")).toBe(false)
    expect(isValidProfileName("invalid name")).toBe(false)
    expect(isValidProfileName("slash/not/allowed")).toBe(false)
    expect(isValidProfileName("a".repeat(33))).toBe(false)
  })

  it("預設 loadBrowserProfiles 必定包含 live", () => {
    const profiles = loadBrowserProfiles()
    expect(profiles).toEqual([DEFAULT_PROFILE])
  })

  it("成功儲存新 profile 且不重複", () => {
    const p1 = saveBrowserProfile("account_a")
    expect(p1).toEqual(["live", "account_a"])

    const p2 = saveBrowserProfile("account_a")
    expect(p2).toEqual(["live", "account_a"])

    const fromStorage = loadBrowserProfiles()
    expect(fromStorage).toEqual(["live", "account_a"])
  })

  it("儲存不合法名稱拋出例外", () => {
    expect(() => saveBrowserProfile("bad name!")).toThrow("Invalid profile name")
  })

  it("刪除自訂 profile 成功，但不能刪除 live", () => {
    saveBrowserProfile("temp_account")
    expect(loadBrowserProfiles()).toContain("temp_account")

    const afterDelete = removeBrowserProfile("temp_account")
    expect(afterDelete).toEqual(["live"])

    const deleteLive = removeBrowserProfile("live")
    expect(deleteLive).toEqual(["live"])
  })

  it("儲存 profile 寫入 localStorage 並能正確讀取", () => {
    saveBrowserProfile("account_a")
    expect(window.localStorage.getItem(BROWSER_PROFILES_KEY)).toContain("account_a")

    expect(loadCurrentBrowserProfile()).toBe(DEFAULT_PROFILE)
    saveCurrentBrowserProfile("account_a")
    expect(window.localStorage.getItem(CURRENT_PROFILE_KEY)).toBe("account_a")
    expect(loadCurrentBrowserProfile()).toBe("account_a")
  })
})
