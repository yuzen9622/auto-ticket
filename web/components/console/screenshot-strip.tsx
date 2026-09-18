"use client"

import { useTranslations } from "next-intl"

import { Panel } from "@/components/terminal/panel"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { screenshotUrl } from "@/lib/config"
import { TONE_TEXT_CLASS, purchaseStateTone } from "@/lib/fsm"
import { usePurchaseStateLabel } from "@/lib/i18n/labels"
import { cn } from "@/lib/utils"
import type { ScreenshotPayload } from "@/lib/ws/types"

export function ScreenshotStrip({ shots }: { shots: ScreenshotPayload[] }) {
  const t = useTranslations("taskConsole")
  const purchaseStateLabel = usePurchaseStateLabel()
  return (
    <Panel
      title={t("screenshots", { count: shots.length })}
      className="min-h-0"
      bodyClassName="overflow-y-auto p-3"
    >
      {shots.length === 0 ? (
        <p className="text-xs text-muted-foreground">{t("screenshotsEmpty")}</p>
      ) : (
        <ul className="grid grid-cols-2 gap-2">
          {shots.map((shot) => (
            <li key={`${shot.sequence}-${shot.url}`}>
              <Dialog>
                <DialogTrigger asChild>
                  <button
                    type="button"
                    className="block w-full cursor-pointer overflow-hidden rounded-md border border-border p-0 text-left transition-colors hover:border-primary"
                  >
                    {/* 原生 img：截圖僅供除錯，不需 Image Optimizer（計畫 §3.3）。 */}
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={screenshotUrl(shot.url)}
                      alt={t("screenshotAlt", {
                        sequence: shot.sequence,
                        state: purchaseStateLabel(shot.state),
                      })}
                      className="h-20 w-full bg-muted object-cover"
                    />
                    <span className="flex items-baseline gap-1 border-t border-border px-1.5 py-0.5 text-xs">
                      <span className="tabular text-muted-foreground">
                        #{shot.sequence}
                      </span>
                      <span
                        className={cn(
                          "truncate font-medium tracking-wider uppercase",
                          TONE_TEXT_CLASS[purchaseStateTone(shot.state)]
                        )}
                      >
                        {purchaseStateLabel(shot.state)}
                      </span>
                    </span>
                  </button>
                </DialogTrigger>
                <DialogContent className="max-w-5xl">
                  <DialogHeader>
                    <DialogTitle>
                      #{shot.sequence} {purchaseStateLabel(shot.state)}
                    </DialogTitle>
                  </DialogHeader>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={screenshotUrl(shot.url)}
                    alt={t("screenshotAlt", {
                      sequence: shot.sequence,
                      state: purchaseStateLabel(shot.state),
                    })}
                    className="max-h-[75vh] w-full object-contain"
                  />
                </DialogContent>
              </Dialog>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}
