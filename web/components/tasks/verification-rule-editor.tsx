"use client"

import { useTranslations } from "next-intl"
import { Trash2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/animate-ui/components/radix/switch"
import type { VerificationRule } from "@/lib/api/types"

/** 只處理主辦自訂的文字問答題；本專案不辨識任何圖形驗證碼。 */
export function VerificationRuleEditor({
  value,
  onChange,
  errors,
}: {
  value: VerificationRule[]
  onChange: (next: VerificationRule[]) => void
  errors?: Record<string, string>
}) {
  const t = useTranslations("taskForm")

  const update = (i: number, patch: Partial<VerificationRule>) => {
    onChange(value.map((r, idx) => (idx === i ? { ...r, ...patch } : r)))
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Label>{t("verificationSection")}</Label>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() =>
            onChange([...value, { pattern: "", answer: "", is_regex: false }])
          }
        >
          {t("addVerificationRule")}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">{t("verificationHint")}</p>

      {value.length === 0 && (
        <p className="text-xs text-muted-foreground">
          {t("noVerificationRules")}
        </p>
      )}

      {value.map((rule, i) => {
        const patternError = errors?.[`verification.${i}.pattern`]
        const answerError = errors?.[`verification.${i}.answer`]
        return (
          <div
            key={i}
            className="grid grid-cols-1 items-end gap-2 rounded-md bg-muted/20 p-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto_auto]"
          >
            <div className="flex flex-col gap-1">
              <Label htmlFor={`rule-pattern-${i}`} className="text-[10px]">
                {t("verificationPattern")}
              </Label>
              <Input
                id={`rule-pattern-${i}`}
                value={rule.pattern}
                onChange={(e) => update(i, { pattern: e.target.value })}
                aria-invalid={patternError !== undefined}
                aria-describedby={
                  patternError ? `rule-pattern-error-${i}` : undefined
                }
                className="h-7"
              />
              {patternError && (
                <span
                  id={`rule-pattern-error-${i}`}
                  className="text-xs text-destructive"
                >
                  {patternError}
                </span>
              )}
            </div>

            <div className="flex flex-col gap-1">
              <Label htmlFor={`rule-answer-${i}`} className="text-[10px]">
                {t("verificationAnswer")}
              </Label>
              <Input
                id={`rule-answer-${i}`}
                value={rule.answer}
                onChange={(e) => update(i, { answer: e.target.value })}
                aria-invalid={answerError !== undefined}
                aria-describedby={
                  answerError ? `rule-answer-error-${i}` : undefined
                }
                className="h-7"
              />
              {answerError && (
                <span
                  id={`rule-answer-error-${i}`}
                  className="text-xs text-destructive"
                >
                  {answerError}
                </span>
              )}
            </div>

            <div className="flex flex-col gap-1">
              <Label htmlFor={`rule-regex-${i}`} className="text-[10px]">
                {t("verificationIsRegex")}
              </Label>
              <Switch
                id={`rule-regex-${i}`}
                checked={rule.is_regex}
                onCheckedChange={(v) => update(i, { is_regex: v })}
              />
            </div>

            <Button
              type="button"
              size="icon"
              variant="ghost"
              aria-label={t("removeVerificationRule")}
              onClick={() => onChange(value.filter((_, idx) => idx !== i))}
            >
              <Trash2 />
            </Button>
          </div>
        )
      })}
    </div>
  )
}
