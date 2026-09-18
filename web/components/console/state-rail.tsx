"use client"

import { useTranslations } from "next-intl"

import { Panel } from "@/components/terminal/panel"
import {
  PURCHASE_STATE_ORDER,
  TONE_DOT_CLASS,
  TONE_TEXT_CLASS,
  isFinalState,
  purchaseStateTone,
} from "@/lib/fsm"
import { usePurchaseStateLabel } from "@/lib/i18n/labels"
import { cn } from "@/lib/utils"

/** 14 個 PurchaseState 的垂直軌道，順序直接沿用後端 enum 宣告順序。 */
export function StateRail({
  currentState,
  visitedStates,
}: {
  currentState: string | null
  visitedStates: string[]
}) {
  const t = useTranslations("taskConsole")
  const purchaseStateLabel = usePurchaseStateLabel()
  const visited = new Set(visitedStates)

  return (
    <Panel
      title={t("stateRail")}
      className="min-h-0"
      bodyClassName="overflow-y-auto p-0"
    >
      <ol className="flex flex-col py-1">
        {PURCHASE_STATE_ORDER.map((state, i) => {
          const isCurrent = state === currentState
          const wasVisited = visited.has(state)
          const final = isFinalState(state)
          const tone = purchaseStateTone(state)
          const lit = isCurrent || (final && wasVisited)

          return (
            <li
              key={state}
              aria-current={isCurrent ? "step" : undefined}
              className={cn(
                "relative flex items-center gap-2 px-3 py-1.5 text-xs",
                isCurrent && "bg-accent font-medium"
              )}
            >
              <span
                aria-hidden
                className={cn(
                  "absolute top-0 bottom-0 left-[1.1rem] w-px",
                  i === 0 && "top-1/2",
                  i === PURCHASE_STATE_ORDER.length - 1 && "bottom-1/2",
                  wasVisited ? "bg-border" : "bg-transparent"
                )}
              />
              <span
                aria-hidden
                className={cn(
                  "relative z-10 size-2 shrink-0 rounded-full border",
                  lit
                    ? cn(TONE_DOT_CLASS[tone], "border-transparent")
                    : wasVisited
                      ? "border-muted-foreground bg-muted-foreground"
                      : "border-border bg-background"
                )}
              />
              <span
                className={cn(
                  "truncate",
                  lit
                    ? cn(TONE_TEXT_CLASS[tone], "font-bold")
                    : wasVisited
                      ? "text-foreground"
                      : "text-muted-foreground"
                )}
              >
                {purchaseStateLabel(state)}
              </span>
              {isCurrent && (
                <span className="ml-auto shrink-0 text-primary">◀</span>
              )}
            </li>
          )
        })}
      </ol>
    </Panel>
  )
}
