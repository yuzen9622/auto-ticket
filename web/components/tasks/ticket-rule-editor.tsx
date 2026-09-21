"use client"

import * as React from "react"
import { useTranslations } from "next-intl"

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
import type { TicketRule } from "@/lib/api/types"
import { TICKET_PRICE_ORDER, type TicketPriceOrder } from "@/lib/contract"
import { createDefaultRule } from "@/lib/task-draft"

export interface TicketRuleEditorProps {
  value: TicketRule | null
  onChange: (next: TicketRule | null) => void
  error?: string
}

/** 逗號分隔的關鍵字欄位，沿用「指定區域」既有的輸入慣例。 */
function KeywordField({
  id,
  label,
  hint,
  value,
  onChange,
}: {
  id: string
  label: string
  hint: string
  value: string[]
  onChange: (next: string[]) => void
}) {
  return (
    <div className="flex flex-col gap-1">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        value={value.join(", ")}
        onChange={(e) =>
          onChange(
            e.target.value
              .split(",")
              .map((word) => word.trim())
              .filter(Boolean)
          )
        }
        aria-describedby={`${id}-hint`}
        className="h-7"
      />
      <span id={`${id}-hint`} className="text-xs text-muted-foreground">
        {hint}
      </span>
    </div>
  )
}

/** 空字串代表「不設限」，要送 null 而不是 0——0 在後端是「免費票」不是「無上限」。 */
function priceOf(raw: string): number | null {
  const trimmed = raw.trim()
  if (trimmed === "") return null
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : null
}

/**
 * 不需要知道票價也寫得出來的挑票規則。
 *
 * 決策順序是 精確票種 → 這組規則 → 找不到就隨便挑，所以它擺在那兩者中間。
 */
export function TicketRuleEditor({
  value,
  onChange,
  error,
}: TicketRuleEditorProps) {
  const t = useTranslations("taskForm")
  const enabled = value !== null

  const patch = (next: Partial<TicketRule>) => {
    if (value === null) return
    onChange({ ...value, ...next })
  }

  return (
    <div className="flex flex-col gap-3 rounded-lg border bg-card/40 p-3 shadow-2xs">
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <Label htmlFor="rule-enabled" className="text-sm font-semibold">
            {t("rule")}
          </Label>
          <span className="text-xs text-muted-foreground">{t("ruleHint")}</span>
        </div>
        <Switch
          id="rule-enabled"
          checked={enabled}
          onCheckedChange={(on) => onChange(on ? createDefaultRule() : null)}
        />
      </div>

      {enabled && (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div className="flex flex-col gap-1">
              <Label htmlFor="rule-max-price">{t("ruleMaxPrice")}</Label>
              <Input
                id="rule-max-price"
                type="number"
                min={0}
                inputMode="numeric"
                placeholder={t("ruleNoLimit")}
                value={value.max_price ?? ""}
                onChange={(e) => patch({ max_price: priceOf(e.target.value) })}
                aria-invalid={error !== undefined}
                className="tabular h-7"
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor="rule-min-price">{t("ruleMinPrice")}</Label>
              <Input
                id="rule-min-price"
                type="number"
                min={0}
                inputMode="numeric"
                placeholder={t("ruleNoLimit")}
                value={value.min_price ?? ""}
                onChange={(e) => patch({ min_price: priceOf(e.target.value) })}
                aria-invalid={error !== undefined}
                className="tabular h-7"
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label htmlFor="rule-price-order">{t("rulePriceOrder")}</Label>
              <Select
                value={value.price_order}
                onValueChange={(v) =>
                  patch({ price_order: v as TicketPriceOrder })
                }
              >
                <SelectTrigger id="rule-price-order" className="h-7">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TICKET_PRICE_ORDER.map((order) => (
                    <SelectItem key={order} value={order}>
                      {t(`rulePriceOrder_${order}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <span
            id="rule-hint"
            className={
              error ? "text-xs text-destructive" : "text-xs text-muted-foreground"
            }
          >
            {error ?? t("rulePriceWindowHint")}
          </span>

          <KeywordField
            id="rule-prefer"
            label={t("rulePrefer")}
            hint={t("rulePreferHint")}
            value={value.prefer_name_patterns}
            onChange={(prefer_name_patterns) => patch({ prefer_name_patterns })}
          />
          <KeywordField
            id="rule-exclude"
            label={t("ruleExclude")}
            hint={t("ruleExcludeHint")}
            value={value.exclude_name_patterns}
            onChange={(exclude_name_patterns) =>
              patch({ exclude_name_patterns })
            }
          />
        </>
      )}
    </div>
  )
}
