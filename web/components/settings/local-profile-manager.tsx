"use client"

import * as React from "react"
import { Pencil, Plus, Trash2 } from "lucide-react"
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
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  clearAllLocalProfiles,
  loadAttendees,
  loadContacts,
  removeAttendee,
  removeContact,
  saveContact,
  type StoredAttendee,
  type StoredContact,
  updateContact,
} from "@/lib/local-profile"
import { EMAIL_PATTERN, PHONE_PATTERN } from "@/lib/contract"
import { maskEmail, maskPhone } from "@/lib/mask"

const EMPTY_CONTACT: StoredContact = { name: "", phone: "", email: "" }

export function LocalProfileManager() {
  const t = useTranslations("settings")
  const tc = useTranslations("common")
  const [contacts, setContacts] = React.useState<StoredContact[]>([])
  const [attendees, setAttendees] = React.useState<StoredAttendee[]>([])
  const [editorOpen, setEditorOpen] = React.useState(false)
  const [editingIndex, setEditingIndex] = React.useState<number | null>(null)
  const [draft, setDraft] = React.useState<StoredContact>(EMPTY_CONTACT)

  // localStorage 只在瀏覽器存在，必須等掛載後才讀，否則 SSR/CSR 內容不一致。
  React.useEffect(() => {
    queueMicrotask(() => {
      setContacts(loadContacts())
      setAttendees(loadAttendees())
    })
  }, [])

  const closeEditor = () => {
    setEditorOpen(false)
    setEditingIndex(null)
    setDraft(EMPTY_CONTACT)
  }

  const editContact = (index: number) => {
    setEditingIndex(index)
    setDraft(contacts[index])
    setEditorOpen(true)
  }

  const submitContact = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const contact = {
      name: draft.name.trim(),
      phone: draft.phone.trim(),
      email: draft.email.trim(),
    }
    if (
      contact.name === "" ||
      !PHONE_PATTERN.test(contact.phone) ||
      !EMAIL_PATTERN.test(contact.email)
    ) {
      toast.error(t("contactInvalid"))
      return
    }

    if (editingIndex === null) {
      setContacts(saveContact(contact))
      toast.success(t("contactCreated"))
    } else {
      setContacts(updateContact(editingIndex, contact))
      toast.success(t("contactUpdated"))
    }
    closeEditor()
  }

  return (
    <Panel title={t("localProfileHeading")}>
      <div className="flex flex-col gap-3">
        <section className="flex flex-col gap-2">
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-xs font-semibold tracking-wider text-muted-foreground uppercase">
              {t("contactsCount", { count: contacts.length })}
            </h3>
            {!editorOpen && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-7 gap-1 text-xs"
                onClick={() => {
                  setEditingIndex(null)
                  setDraft(EMPTY_CONTACT)
                  setEditorOpen(true)
                }}
              >
                <Plus className="size-3" />
                {t("createContact")}
              </Button>
            )}
          </div>

          {editorOpen && (
            <form
              className="grid grid-cols-1 gap-2 rounded-md border border-border/50 p-2.5 md:grid-cols-3"
              onSubmit={submitContact}
              noValidate
            >
              <div className="flex flex-col gap-1">
                <Label htmlFor="local-contact-name" className="text-xs">
                  {t("contactName")}
                </Label>
                <Input
                  id="local-contact-name"
                  value={draft.name}
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      name: event.target.value,
                    }))
                  }
                  className="h-7 text-xs"
                  autoFocus
                />
              </div>
              <div className="flex flex-col gap-1">
                <Label htmlFor="local-contact-phone" className="text-xs">
                  {t("contactPhone")}
                </Label>
                <Input
                  id="local-contact-phone"
                  value={draft.phone}
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      phone: event.target.value,
                    }))
                  }
                  className="h-7 text-xs"
                  inputMode="tel"
                />
              </div>
              <div className="flex flex-col gap-1">
                <Label htmlFor="local-contact-email" className="text-xs">
                  {t("contactEmail")}
                </Label>
                <Input
                  id="local-contact-email"
                  type="email"
                  value={draft.email}
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      email: event.target.value,
                    }))
                  }
                  className="h-7 text-xs"
                />
              </div>
              <div className="flex items-center gap-2 md:col-span-3">
                <Button type="submit" size="sm">
                  {t("saveContact")}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={closeEditor}
                >
                  {t("cancelContactEdit")}
                </Button>
              </div>
            </form>
          )}

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
                    aria-label={t("editContact", { name: c.name })}
                    onClick={() => editContact(i)}
                  >
                    <Pencil />
                  </Button>
                  <Button
                    size="icon-sm"
                    variant="ghost"
                    aria-label={t("deleteContact", { name: c.name })}
                    onClick={() => {
                      setContacts(removeContact(i))
                      if (editorOpen) closeEditor()
                      toast.success(t("contactDeleted"))
                    }}
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
