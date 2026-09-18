"use client"

import { KvRow } from "@/components/terminal/kv-row"
import { Panel } from "@/components/terminal/panel"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import type { EventOut } from "@/lib/api/types"
import { maskEmail, maskIdNumber, maskPhone } from "@/lib/mask"
import type { PreferenceDraft } from "@/components/tasks/step-preference"

export interface RunOptions {
  sale_start_at_local: string
  auto_login: boolean
  max_retries: number
  timeout_seconds: number
  qualification_code: string
  profile: string
}

/** 付款一律走 Mock：UI 不提供任何信用卡欄位（G29/G31）。 */
export function MockPaymentNotice() {
  return (
    <p className="rounded-[4px] border border-[var(--oc-warning)] px-3 py-2 text-[11px] text-[var(--oc-warning)]">
      MOCK 測試模式 — 不會發生真實付款。本介面不索取也不傳送任何信用卡資訊。
    </p>
  )
}

export function StepConfirm({
  event,
  draft,
  options,
  onChange,
}: {
  event: EventOut | null
  draft: PreferenceDraft
  options: RunOptions
  onChange: (next: RunOptions) => void
}) {
  const set = (patch: Partial<RunOptions>) => onChange({ ...options, ...patch })

  const saleLocal = options.sale_start_at_local
  const parsed = saleLocal ? new Date(saleLocal) : null
  const validSale = parsed !== null && !Number.isNaN(parsed.getTime())

  return (
    <div className="flex flex-col gap-4">
      <Panel title="步驟 3 / 3 — 確認送出">
        <div className="flex flex-col gap-3">
          <MockPaymentNotice />

          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <div className="flex flex-col gap-1">
              <Label htmlFor="sale-start">開賣時間（本地時間）</Label>
              <Input
                id="sale-start"
                type="datetime-local"
                value={saleLocal}
                onChange={(e) => set({ sale_start_at_local: e.target.value })}
                className="tabular h-7"
              />
              {/* 同時顯示 UTC 供人眼核對，避免排程差 8 小時（R13）。 */}
              <span className="tabular text-[10px] text-[var(--oc-muted)]">
                UTC {validSale ? parsed.toISOString() : "—"}
              </span>
            </div>

            <div className="flex flex-col gap-1">
              <Label htmlFor="max-retries">max_retries（≥0）</Label>
              <Input
                id="max-retries"
                type="number"
                min={0}
                value={options.max_retries}
                onChange={(e) =>
                  set({ max_retries: Math.max(0, Number(e.target.value)) })
                }
                className="tabular h-7"
              />
            </div>

            <div className="flex flex-col gap-1">
              <Label htmlFor="timeout">timeout_seconds（&gt;0）</Label>
              <Input
                id="timeout"
                type="number"
                min={1}
                value={options.timeout_seconds}
                onChange={(e) =>
                  set({ timeout_seconds: Math.max(1, Number(e.target.value)) })
                }
                className="tabular h-7"
              />
            </div>

            <div className="flex flex-col gap-1">
              <Label htmlFor="qualification">qualification_code（選填）</Label>
              <Input
                id="qualification"
                value={options.qualification_code}
                onChange={(e) => set({ qualification_code: e.target.value })}
                className="h-7"
                autoComplete="off"
              />
            </div>

            <div className="flex flex-col gap-1">
              <Label htmlFor="profile">profile</Label>
              <Input
                id="profile"
                value={options.profile}
                onChange={(e) => set({ profile: e.target.value })}
                className="h-7"
              />
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="auto-login">auto_login</Label>
              <Switch
                id="auto-login"
                checked={options.auto_login}
                onCheckedChange={(v) => set({ auto_login: v })}
              />
            </div>
          </div>
        </div>
      </Panel>

      <Panel title="摘要（敏感欄位已遮蔽）">
        <KvRow label="event_title" value={event?.title ?? "—"} />
        <KvRow label="event_url" value={event?.canonical_url ?? "—"} />
        <KvRow
          label="payment_method"
          value="mock"
          tone="text-[var(--oc-warning)]"
        />
        <KvRow label="quantity" value={draft.ticket_preference.quantity} />
        <KvRow
          label="priorities"
          value={`${draft.ticket_preference.priorities.length} 筆`}
        />
        <KvRow
          label="seat_strategy"
          value={draft.ticket_preference.seat_preference.strategy}
        />
        <KvRow label="contact.name" value={draft.contact_profile.name || "—"} />
        <KvRow
          label="contact.phone"
          value={maskPhone(draft.contact_profile.phone) || "—"}
        />
        <KvRow
          label="contact.email"
          value={maskEmail(draft.contact_profile.email) || "—"}
        />
        <KvRow label="attendees" value={`${draft.attendees.length} 人`} />
        {draft.attendees.map((a, i) => (
          <KvRow
            key={i}
            label={`attendee[${i}]`}
            value={`${a.name} · ${maskPhone(a.phone)} · ${
              a.id_number ? maskIdNumber(a.id_number) : "無身分證字號"
            }`}
          />
        ))}
        <KvRow
          label="verification_rules"
          value={`${draft.verification_rules.length} 條`}
        />
      </Panel>
    </div>
  )
}
