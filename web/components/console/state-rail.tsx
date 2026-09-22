"use client"

import { useTranslations } from "next-intl"

import { Panel } from "@/components/terminal/panel"
import {
  PURCHASE_PHASES,
  TONE_DOT_CLASS,
  TONE_TEXT_CLASS,
  purchasePhaseIndex,
  purchaseStateTone,
} from "@/lib/fsm"
import { usePurchaseStateLabel } from "@/lib/i18n/labels"
import { cn } from "@/lib/utils"

/**
 * 購票進度：五個階段的垂直軌道。
 *
 * 最後一格在任務結束後改寫成實際結果（已完成／已售罄／已逾時／失敗），
 * 使用者不必再去別的面板對照才知道結局。
 */
export function StateRail({
  currentState,
  visitedStates,
}: {
  currentState: string | null
  visitedStates: string[]
}) {
  const t = useTranslations("taskConsole")
  const phaseLabel = useTranslations("taskConsole.phase")
  const purchaseStateLabel = usePurchaseStateLabel()

  const currentIndex = purchasePhaseIndex(currentState)
  const visitedIndex = visitedStates.reduce(
    (max, state) => Math.max(max, purchasePhaseIndex(state)),
    currentIndex
  )
  const lastPhase = PURCHASE_PHASES.length - 1
  const finished = currentIndex === lastPhase

  return (
    <Panel title={t("progress")} className="min-h-0">
      <ol className="flex flex-col">
        {PURCHASE_PHASES.map((phase, i) => {
          const isCurrent = i === currentIndex
          const reached = i <= visitedIndex
          const tone =
            finished && i === lastPhase
              ? purchaseStateTone(currentState as string)
              : "accent"
          const label =
            finished && i === lastPhase
              ? purchaseStateLabel(currentState)
              : phaseLabel(phase.key)

          return (
            <li
              key={phase.key}
              aria-current={isCurrent ? "step" : undefined}
              className="relative flex items-center gap-2.5 py-1.5 text-xs"
            >
              <span
                aria-hidden
                className={cn(
                  "absolute top-0 bottom-0 left-[0.21rem] w-px",
                  i === 0 && "top-1/2",
                  i === lastPhase && "bottom-1/2",
                  reached ? "bg-border" : "bg-transparent"
                )}
              />
              <span
                aria-hidden
                className={cn(
                  "relative z-10 size-2 shrink-0 rounded-full border",
                  isCurrent
                    ? cn(TONE_DOT_CLASS[tone], "border-transparent")
                    : reached
                      ? "border-muted-foreground bg-muted-foreground"
                      : "border-border bg-background"
                )}
              />
              <span
                className={cn(
                  "truncate",
                  isCurrent
                    ? cn(TONE_TEXT_CLASS[tone], "font-semibold")
                    : reached
                      ? "text-foreground"
                      : "text-muted-foreground"
                )}
              >
                {label}
              </span>
            </li>
          )
        })}
      </ol>
    </Panel>
  )
}
