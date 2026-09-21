import { beforeEach, describe, expect, it, vi } from "vitest"

import {
  ATTENDEES_KEY,
  CONTACTS_KEY,
  clearAllLocalProfiles,
  loadAttendees,
  loadContacts,
  removeContact,
  saveAttendee,
  saveContact,
  toStoredAttendee,
  toStoredContact,
  updateContact,
} from "@/lib/local-profile"

function installLocalStorage() {
  const store = new Map<string, string>()
  vi.stubGlobal("window", {
    localStorage: {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
      removeItem: (k: string) => void store.delete(k),
    },
  })
  return store
}

let store: Map<string, string>

beforeEach(() => {
  store = installLocalStorage()
})

describe("個資防線：id_number 絕不進 localStorage", () => {
  it("toStoredAttendee 以白名單挑欄位，丟棄 id_number", () => {
    const stored = toStoredAttendee({
      name: "王小明",
      phone: "0912345678",
      id_number: "A123456789",
    } as { name: string; phone: string })
    expect(stored).toEqual({ name: "王小明", phone: "0912345678" })
    expect(Object.keys(stored)).not.toContain("id_number")
  })

  it("saveAttendee 寫入後的原始 JSON 不含身分證字號", () => {
    saveAttendee({
      name: "王小明",
      phone: "0912345678",
      id_number: "A123456789",
    } as {
      name: string
      phone: string
    })
    const raw = store.get(ATTENDEES_KEY) ?? ""
    expect(raw).not.toContain("A123456789")
    expect(raw).not.toContain("id_number")
    expect(loadAttendees()).toEqual([{ name: "王小明", phone: "0912345678" }])
  })

  it("toStoredContact 丟棄任何額外欄位（含 id_number 與密碼）", () => {
    const stored = toStoredContact({
      name: "Alice",
      phone: "0912345678",
      email: "a@x.com",
      id_number: "A123456789",
      access_key: "super-secret",
    } as { name: string; phone: string; email: string })
    expect(stored).toEqual({
      name: "Alice",
      phone: "0912345678",
      email: "a@x.com",
    })
  })

  it("saveContact 寫入後的原始 JSON 不含身分證字號或憑證", () => {
    saveContact({
      name: "Alice",
      phone: "0912345678",
      email: "a@x.com",
      id_number: "A123456789",
      access_key: "super-secret",
    } as { name: string; phone: string; email: string })
    const raw = store.get(CONTACTS_KEY) ?? ""
    expect(raw).not.toContain("A123456789")
    expect(raw).not.toContain("super-secret")
  })
})

describe("聯絡人 CRUD", () => {
  it("儲存後可讀回，最新的排在最前", () => {
    saveContact({ name: "A", phone: "0911111111", email: "a@x.com" })
    saveContact({ name: "B", phone: "0922222222", email: "b@x.com" })
    expect(loadContacts().map((c) => c.name)).toEqual(["B", "A"])
  })

  it("同名同電話視為同一筆，不重複累積", () => {
    saveContact({ name: "A", phone: "0911111111", email: "a@x.com" })
    saveContact({ name: "A", phone: "0911111111", email: "new@x.com" })
    const all = loadContacts()
    expect(all).toHaveLength(1)
    expect(all[0].email).toBe("new@x.com")
  })

  it("可更新指定索引並保留原位置，不殘留舊筆", () => {
    saveContact({ name: "A", phone: "0911111111", email: "a@x.com" })
    saveContact({ name: "B", phone: "0922222222", email: "b@x.com" })

    expect(
      updateContact(1, {
        name: "A2",
        phone: "0933333333",
        email: "a2@x.com",
      })
    ).toEqual([
      { name: "B", phone: "0922222222", email: "b@x.com" },
      { name: "A2", phone: "0933333333", email: "a2@x.com" },
    ])
    expect(loadContacts().some((contact) => contact.name === "A")).toBe(false)
  })

  it("索引非整數或越界時回傳原資料且不寫入", () => {
    saveContact({ name: "A", phone: "0911111111", email: "a@x.com" })
    const setItem = vi.spyOn(window.localStorage, "setItem")
    setItem.mockClear()
    const original = loadContacts()

    expect(updateContact(0.5, { name: "X" })).toEqual(original)
    expect(updateContact(-1, { name: "X" })).toEqual(original)
    expect(updateContact(1, { name: "X" })).toEqual(original)
    expect(setItem).not.toHaveBeenCalled()
  })

  it("更新仍採白名單序列化，額外敏感欄位不落盤", () => {
    saveContact({ name: "A", phone: "0911111111", email: "a@x.com" })

    updateContact(0, {
      name: "B",
      phone: "0922222222",
      email: "b@x.com",
      id_number: "A123456789",
      access_key: "super-secret",
    } as { name: string; phone: string; email: string })

    const raw = store.get(CONTACTS_KEY) ?? ""
    expect(raw).not.toContain("A123456789")
    expect(raw).not.toContain("super-secret")
    expect(loadContacts()).toEqual([
      { name: "B", phone: "0922222222", email: "b@x.com" },
    ])
  })

  it("可依索引刪除", () => {
    saveContact({ name: "A", phone: "0911111111", email: "a@x.com" })
    saveContact({ name: "B", phone: "0922222222", email: "b@x.com" })
    expect(removeContact(0).map((c) => c.name)).toEqual(["A"])
  })

  it("全部清除會移除兩個 key", () => {
    saveContact({ name: "A", phone: "0911111111", email: "a@x.com" })
    saveAttendee({ name: "B", phone: "0922222222" })
    clearAllLocalProfiles()
    expect(loadContacts()).toEqual([])
    expect(loadAttendees()).toEqual([])
  })

  it("損毀的 JSON 不會拋錯，回空陣列", () => {
    store.set(CONTACTS_KEY, "{not json")
    expect(loadContacts()).toEqual([])
  })

  it("非陣列內容回空陣列", () => {
    store.set(CONTACTS_KEY, '{"name":"A"}')
    expect(loadContacts()).toEqual([])
  })
})
