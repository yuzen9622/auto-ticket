"use client"

import * as React from "react"
import { useTranslations } from "next-intl"

import { Button } from "@/components/ui/button"
import type { UserContactProfile } from "@/lib/api/types"
import {
  loadContacts,
  saveContact,
  type StoredContact,
} from "@/lib/local-profile"
import { maskEmail, maskPhone } from "@/lib/mask"

/** 常用聯絡人只存在這個瀏覽器；身分識別碼與密碼永遠不在其中。 */
export function ContactPicker({
  current,
  onPick,
}: {
  current: UserContactProfile
  onPick: (contact: UserContactProfile) => void
}) {
  const t = useTranslations("taskForm")
  const [contacts, setContacts] = React.useState<StoredContact[]>([])

  // localStorage 只在瀏覽器存在，必須等掛載後才讀，否則 SSR/CSR 內容不一致。
  React.useEffect(() => {
    queueMicrotask(() => {
      setContacts(loadContacts())
    })
  }, [])

  return (
    <div className="flex flex-col gap-2 rounded-md bg-muted/30 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">
          {t("savedContacts")}
        </span>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={current.name.trim() === ""}
          onClick={() => setContacts(saveContact(current))}
        >
          {t("saveContact")}
        </Button>
      </div>

      {contacts.length === 0 ? (
        <p className="text-xs text-muted-foreground">{t("noSavedContacts")}</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {contacts.map((contact, i) => (
            <li key={`${contact.email}-${i}`}>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="w-full justify-start"
                onClick={() =>
                  onPick({
                    name: contact.name,
                    phone: contact.phone,
                    email: contact.email,
                  })
                }
              >
                <span className="truncate">
                  {contact.name} · {maskPhone(contact.phone)} ·{" "}
                  {maskEmail(contact.email)}
                </span>
                <span className="sr-only">{t("useContact")}</span>
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
