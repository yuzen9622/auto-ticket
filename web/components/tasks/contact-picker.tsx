"use client"

import * as React from "react"

import { Button } from "@/components/ui/button"
import type { UserContactProfile } from "@/lib/api/types"
import {
  loadContacts,
  saveContact,
  type StoredContact,
} from "@/lib/local-profile"
import { maskEmail, maskPhone } from "@/lib/mask"

/** 常用聯絡人只存在此瀏覽器；身分證字號與密碼永遠不在其中。 */
export function ContactPicker({
  current,
  onPick,
}: {
  current: UserContactProfile
  onPick: (contact: UserContactProfile) => void
}) {
  const [contacts, setContacts] = React.useState<StoredContact[]>([])

  // localStorage 只在瀏覽器存在，必須等掛載後才讀，否則 SSR/CSR 內容不一致。
  React.useEffect(() => {
    queueMicrotask(() => {
      setContacts(loadContacts())
    })
  }, [])

  return (
    <div className="flex flex-col gap-2 rounded-[4px] border border-[var(--oc-border)] p-2">
      <div className="flex items-center justify-between">
        <span className="text-[11px] text-[var(--oc-muted)]">
          常用聯絡人（只存在此瀏覽器，不會上傳）
        </span>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={!current.name || !current.phone}
          onClick={() => setContacts(saveContact(current))}
        >
          儲存此聯絡人
        </Button>
      </div>

      {contacts.length === 0 ? (
        <p className="text-[11px] text-[var(--oc-muted)]">
          尚未儲存任何聯絡人。
        </p>
      ) : (
        <ul className="flex flex-wrap gap-1">
          {contacts.map((c, i) => (
            <li key={`${c.name}-${c.phone}-${i}`}>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() =>
                  onPick({ name: c.name, phone: c.phone, email: c.email })
                }
                title="帶入此聯絡人"
              >
                <span className="tabular">
                  {c.name} · {maskPhone(c.phone)} · {maskEmail(c.email)}
                </span>
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
