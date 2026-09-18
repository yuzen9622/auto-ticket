"use client"

import * as React from "react"
import { Trash2 } from "lucide-react"
import { useTranslations } from "next-intl"
import { toast } from "sonner"

import { Panel } from "@/components/terminal/panel"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  clearAllLocalProfiles,
  loadAttendees,
  loadContacts,
  removeAttendee,
  removeContact,
  type StoredAttendee,
  type StoredContact,
} from "@/lib/local-profile"
import { maskEmail, maskPhone } from "@/lib/mask"

export function LocalProfileManager() {
  const t = useTranslations("settings")
  const tc = useTranslations("common")
  const [contacts, setContacts] = React.useState<StoredContact[]>([])
  const [attendees, setAttendees] = React.useState<StoredAttendee[]>([])

  // localStorage 只在瀏覽器存在，必須等掛載後才讀，否則 SSR/CSR 內容不一致。
  React.useEffect(() => {
    queueMicrotask(() => {
      setContacts(loadContacts())
      setAttendees(loadAttendees())
    })
  }, [])

  return (
    <Panel title={t("localProfileHeading")}>
      <div className="flex flex-col gap-3">
        <p className="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          {t("localProfileNotice")}
        </p>

        <section className="flex flex-col gap-1">
          <h3 className="text-xs font-semibold tracking-wider text-muted-foreground uppercase">
            {t("contactsCount", { count: contacts.length })}
          </h3>
          {contacts.length === 0 ? (
            <p className="text-xs text-muted-foreground">{t("noContacts")}</p>
          ) : (
            <ul className="flex flex-col">
              {contacts.map((c, i) => (
                <li
                  key={`${c.name}-${c.phone}-${i}`}
                  className="flex items-center gap-3 border-b border-border py-1.5 text-xs last:border-b-0"
                >
                  <span className="w-24 shrink-0 truncate">
                    {c.name || "—"}
                  </span>
                  <span className="tabular w-28 shrink-0 text-muted-foreground">
                    {maskPhone(c.phone)}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-muted-foreground">
                    {maskEmail(c.email)}
                  </span>
                  <Button
                    size="icon-sm"
                    variant="ghost"
                    aria-label={t("deleteContact", { name: c.name })}
                    onClick={() => setContacts(removeContact(i))}
                  >
                    <Trash2 />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="flex flex-col gap-1">
          <h3 className="text-xs font-semibold tracking-wider text-muted-foreground uppercase">
            {t("attendeesCount", { count: attendees.length })}
          </h3>
          {attendees.length === 0 ? (
            <p className="text-xs text-muted-foreground">{t("noAttendees")}</p>
          ) : (
            <ul className="flex flex-col">
              {attendees.map((a, i) => (
                <li
                  key={`${a.name}-${a.phone}-${i}`}
                  className="flex items-center gap-3 border-b border-border py-1.5 text-xs last:border-b-0"
                >
                  <span className="w-24 shrink-0 truncate">
                    {a.name || "—"}
                  </span>
                  <span className="tabular min-w-0 flex-1 text-muted-foreground">
                    {maskPhone(a.phone)}
                  </span>
                  <Button
                    size="icon-sm"
                    variant="ghost"
                    aria-label={t("deleteAttendee", { name: a.name })}
                    onClick={() => setAttendees(removeAttendee(i))}
                  >
                    <Trash2 />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button
              variant="destructive"
              size="sm"
              className="self-start"
              disabled={contacts.length === 0 && attendees.length === 0}
            >
              {t("clearAll")}
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>{t("clearHeading")}</AlertDialogTitle>
              <AlertDialogDescription>{t("clearBody")}</AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
              <AlertDialogAction
                onClick={() => {
                  clearAllLocalProfiles()
                  setContacts([])
                  setAttendees([])
                  toast.success(t("cleared"))
                }}
              >
                {t("clearConfirm")}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    </Panel>
  )
}
