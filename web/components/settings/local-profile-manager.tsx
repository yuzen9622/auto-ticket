"use client"

import * as React from "react"
import { Trash2 } from "lucide-react"
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
    <Panel title="本機聯絡人">
      <div className="flex flex-col gap-3">
        <p className="rounded-[4px] border border-[var(--oc-border)] bg-[var(--oc-sunken)] px-2 py-1 text-[11px] text-[var(--oc-muted)]">
          這些資料只存在此瀏覽器，不會上傳。身分證字號從不寫入瀏覽器，只隨單次送出。
        </p>

        <section className="flex flex-col gap-1">
          <h3 className="text-[11px] tracking-wider text-[var(--oc-muted)] uppercase">
            聯絡人 · {contacts.length}
          </h3>
          {contacts.length === 0 ? (
            <p className="text-[11px] text-[var(--oc-muted)]">
              尚未儲存任何聯絡人。
            </p>
          ) : (
            <ul className="flex flex-col">
              {contacts.map((c, i) => (
                <li
                  key={`${c.name}-${c.phone}-${i}`}
                  className="flex items-center gap-3 border-b border-[var(--oc-border)] py-1 text-[12px] last:border-b-0"
                >
                  <span className="w-24 shrink-0 truncate">
                    {c.name || "—"}
                  </span>
                  <span className="tabular w-28 shrink-0 text-[var(--oc-muted)]">
                    {maskPhone(c.phone)}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[var(--oc-muted)]">
                    {maskEmail(c.email)}
                  </span>
                  <Button
                    size="icon-sm"
                    variant="ghost"
                    aria-label={`刪除聯絡人 ${c.name}`}
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
          <h3 className="text-[11px] tracking-wider text-[var(--oc-muted)] uppercase">
            參加人 · {attendees.length}
          </h3>
          {attendees.length === 0 ? (
            <p className="text-[11px] text-[var(--oc-muted)]">
              尚未儲存任何參加人。
            </p>
          ) : (
            <ul className="flex flex-col">
              {attendees.map((a, i) => (
                <li
                  key={`${a.name}-${a.phone}-${i}`}
                  className="flex items-center gap-3 border-b border-[var(--oc-border)] py-1 text-[12px] last:border-b-0"
                >
                  <span className="w-24 shrink-0 truncate">
                    {a.name || "—"}
                  </span>
                  <span className="tabular min-w-0 flex-1 text-[var(--oc-muted)]">
                    {maskPhone(a.phone)}
                  </span>
                  <Button
                    size="icon-sm"
                    variant="ghost"
                    aria-label={`刪除參加人 ${a.name}`}
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
              全部清除
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>清除所有本機聯絡人？</AlertDialogTitle>
              <AlertDialogDescription>
                將刪除此瀏覽器儲存的全部聯絡人與參加人資料，此操作無法復原。
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>取消</AlertDialogCancel>
              <AlertDialogAction
                onClick={() => {
                  clearAllLocalProfiles()
                  setContacts([])
                  setAttendees([])
                  toast.success("已清除本機聯絡人")
                }}
              >
                確認清除
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    </Panel>
  )
}
