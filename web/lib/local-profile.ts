/**
 * Local-first 常用聯絡人：只存在使用者自己的瀏覽器，永不上傳第三方。
 *
 * 個資防線：序列化採**白名單**挑欄位。`id_number` 絕不寫入 localStorage ——
 * 它只活在表單的 React state，隨頁面卸載即消失。帳號密碼同理，永不進此模組。
 */

export const CONTACTS_KEY = "auto-ticket.contacts.v1"
export const ATTENDEES_KEY = "auto-ticket.attendees.v1"

export interface StoredContact {
  name: string
  phone: string
  email: string
}

export interface StoredAttendee {
  name: string
  phone: string
}

/** 白名單序列化：多餘欄位（含 id_number）在此被丟棄。 */
export function toStoredContact(input: {
  name?: unknown
  phone?: unknown
  email?: unknown
}): StoredContact {
  return {
    name: asString(input.name),
    phone: asString(input.phone),
    email: asString(input.email),
  }
}

/** 白名單序列化：只取 name / phone，id_number 永遠不會被帶出。 */
export function toStoredAttendee(input: {
  name?: unknown
  phone?: unknown
}): StoredAttendee {
  return { name: asString(input.name), phone: asString(input.phone) }
}

function asString(v: unknown): string {
  return typeof v === "string" ? v : ""
}

function read<T>(key: string, map: (raw: Record<string, unknown>) => T): T[] {
  if (typeof window === "undefined") return []
  let raw: string | null
  try {
    raw = window.localStorage.getItem(key)
  } catch {
    return []
  }
  if (!raw) return []
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed
      .filter(
        (x): x is Record<string, unknown> => typeof x === "object" && x !== null
      )
      .map(map)
  } catch {
    return []
  }
}

function write(key: string, value: unknown): void {
  if (typeof window === "undefined") return
  try {
    window.localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // 無痕模式或配額用罄：Local-first 是加值功能，失敗不該打斷主流程。
  }
}

export function loadContacts(): StoredContact[] {
  return read(CONTACTS_KEY, toStoredContact)
}

export function saveContact(contact: {
  name?: unknown
  phone?: unknown
  email?: unknown
}): StoredContact[] {
  const entry = toStoredContact(contact)
  const rest = loadContacts().filter(
    (c) => !(c.name === entry.name && c.phone === entry.phone)
  )
  const next = [entry, ...rest].slice(0, 20)
  write(CONTACTS_KEY, next)
  return next
}

export function removeContact(index: number): StoredContact[] {
  const next = loadContacts().filter((_, i) => i !== index)
  write(CONTACTS_KEY, next)
  return next
}

export function loadAttendees(): StoredAttendee[] {
  return read(ATTENDEES_KEY, toStoredAttendee)
}

export function saveAttendee(attendee: {
  name?: unknown
  phone?: unknown
}): StoredAttendee[] {
  const entry = toStoredAttendee(attendee)
  const rest = loadAttendees().filter(
    (a) => !(a.name === entry.name && a.phone === entry.phone)
  )
  const next = [entry, ...rest].slice(0, 20)
  write(ATTENDEES_KEY, next)
  return next
}

export function removeAttendee(index: number): StoredAttendee[] {
  const next = loadAttendees().filter((_, i) => i !== index)
  write(ATTENDEES_KEY, next)
  return next
}

export function clearAllLocalProfiles(): void {
  if (typeof window === "undefined") return
  try {
    window.localStorage.removeItem(CONTACTS_KEY)
    window.localStorage.removeItem(ATTENDEES_KEY)
  } catch {
    // 同上：清除失敗不該拋錯中斷 UI。
  }
}
