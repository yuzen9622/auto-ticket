"use client"

import { Panel } from "@/components/terminal/panel"
import { AttendeeEditor } from "@/components/tasks/attendee-editor"
import { ContactPicker } from "@/components/tasks/contact-picker"
import { TicketPriorityEditor } from "@/components/tasks/ticket-priority-editor"
import { VerificationRuleEditor } from "@/components/tasks/verification-rule-editor"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import type {
  AttendeeProfile,
  EventOut,
  TicketPreference,
  UserContactProfile,
  VerificationRule,
} from "@/lib/api/types"
import { EMAIL_PATTERN, PHONE_PATTERN, SEAT_STRATEGY } from "@/lib/contract"

const STRATEGY_LABEL: Record<string, string> = {
  best_available: "best_available — 最佳可選",
  same_zone: "same_zone — 同一區",
  specific_zone: "specific_zone — 指定區域",
}

export interface PreferenceDraft {
  ticket_preference: TicketPreference
  contact_profile: UserContactProfile
  attendees: AttendeeProfile[]
  verification_rules: VerificationRule[]
}

export function StepPreference({
  event,
  draft,
  onChange,
}: {
  event: EventOut | null
  draft: PreferenceDraft
  onChange: (next: PreferenceDraft) => void
}) {
  const { ticket_preference: tp, contact_profile: contact } = draft
  const ticketNames = event?.ticket_types.map((t) => t.name) ?? []

  const setTp = (patch: Partial<TicketPreference>) =>
    onChange({ ...draft, ticket_preference: { ...tp, ...patch } })

  const setContact = (patch: Partial<UserContactProfile>) =>
    onChange({ ...draft, contact_profile: { ...contact, ...patch } })

  const phoneInvalid =
    contact.phone !== "" && !PHONE_PATTERN.test(contact.phone)
  const emailInvalid =
    contact.email !== "" && !EMAIL_PATTERN.test(contact.email)

  return (
    <div className="flex flex-col gap-4">
      <Panel title="步驟 2 / 3 — 票務偏好">
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <div className="flex flex-col gap-1">
              <Label htmlFor="quantity">張數（1–10）</Label>
              <Input
                id="quantity"
                type="number"
                min={1}
                max={10}
                value={tp.quantity}
                onChange={(e) =>
                  setTp({
                    quantity: Math.min(10, Math.max(1, Number(e.target.value))),
                  })
                }
                className="tabular h-7"
              />
            </div>

            <div className="flex flex-col gap-1">
              <Label htmlFor="strategy">座位策略</Label>
              <Select
                value={tp.seat_preference.strategy}
                onValueChange={(v) =>
                  setTp({
                    seat_preference: {
                      ...tp.seat_preference,
                      strategy:
                        v as TicketPreference["seat_preference"]["strategy"],
                    },
                  })
                }
              >
                <SelectTrigger id="strategy" size="sm" className="h-7">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SEAT_STRATEGY.map((s) => (
                    <SelectItem key={s} value={s}>
                      {STRATEGY_LABEL[s]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="adjacent">相鄰座位</Label>
              <Switch
                id="adjacent"
                checked={tp.seat_preference.adjacent}
                onCheckedChange={(v) =>
                  setTp({
                    seat_preference: { ...tp.seat_preference, adjacent: v },
                  })
                }
              />
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="fallback">找不到時退而求其次</Label>
              <Switch
                id="fallback"
                checked={tp.fallback_to_any}
                onCheckedChange={(v) => setTp({ fallback_to_any: v })}
              />
            </div>
          </div>

          {tp.seat_preference.strategy === "specific_zone" && (
            <div className="flex flex-col gap-1">
              <Label htmlFor="zones">指定區域（逗號分隔）</Label>
              <Input
                id="zones"
                value={tp.seat_preference.preferred_zones.join(", ")}
                onChange={(e) =>
                  setTp({
                    seat_preference: {
                      ...tp.seat_preference,
                      preferred_zones: e.target.value
                        .split(",")
                        .map((z) => z.trim())
                        .filter(Boolean),
                    },
                  })
                }
                className="h-7"
              />
            </div>
          )}

          <TicketPriorityEditor
            value={tp.priorities}
            ticketNames={ticketNames}
            onChange={(priorities) => setTp({ priorities })}
          />
        </div>
      </Panel>

      <Panel title="聯絡人">
        <div className="flex flex-col gap-3">
          <ContactPicker
            current={contact}
            onPick={(c) => onChange({ ...draft, contact_profile: c })}
          />

          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <div className="flex flex-col gap-1">
              <Label htmlFor="contact-name">姓名</Label>
              <Input
                id="contact-name"
                value={contact.name}
                onChange={(e) => setContact({ name: e.target.value })}
                className="h-7"
                autoComplete="off"
              />
            </div>

            <div className="flex flex-col gap-1">
              <Label htmlFor="contact-phone">電話</Label>
              <Input
                id="contact-phone"
                value={contact.phone}
                onChange={(e) => setContact({ phone: e.target.value })}
                aria-invalid={phoneInvalid}
                className="tabular h-7"
                autoComplete="off"
              />
              {phoneInvalid && (
                <span className="text-[10px] text-[var(--oc-danger)]">
                  須為 8–20 碼，僅允許數字與 + - ( ) 空白
                </span>
              )}
            </div>

            <div className="flex flex-col gap-1">
              <Label htmlFor="contact-email">Email</Label>
              <Input
                id="contact-email"
                value={contact.email}
                onChange={(e) => setContact({ email: e.target.value })}
                aria-invalid={emailInvalid}
                className="h-7"
                autoComplete="off"
              />
              {emailInvalid && (
                <span className="text-[10px] text-[var(--oc-danger)]">
                  Email 格式不正確
                </span>
              )}
            </div>
          </div>
        </div>
      </Panel>

      <Panel title="參加人與驗證規則">
        <div className="flex flex-col gap-4">
          <AttendeeEditor
            value={draft.attendees}
            onChange={(attendees) => onChange({ ...draft, attendees })}
          />
          <VerificationRuleEditor
            value={draft.verification_rules}
            onChange={(verification_rules) =>
              onChange({ ...draft, verification_rules })
            }
          />
        </div>
      </Panel>
    </div>
  )
}
