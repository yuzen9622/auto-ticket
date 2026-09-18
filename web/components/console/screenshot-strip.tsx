"use client"

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
import { cn } from "@/lib/utils"
import type { ScreenshotPayload } from "@/lib/ws/types"

export function ScreenshotStrip({ shots }: { shots: ScreenshotPayload[] }) {
  return (
    <Panel
      title={`截圖 · ${shots.length}`}
      className="min-h-0"
      bodyClassName="oc-scroll overflow-y-auto p-3"
    >
      {shots.length === 0 ? (
        <p className="text-[11px] text-[var(--oc-muted)]">
          尚無截圖；Worker 在關鍵狀態會自動擷取。
        </p>
      ) : (
        <ul className="grid grid-cols-2 gap-2">
          {shots.map((shot) => (
            <li key={`${shot.sequence}-${shot.url}`} className="oc-enter">
              <Dialog>
                <DialogTrigger asChild>
                  <button
                    type="button"
                    className="block w-full rounded-[4px] border border-[var(--oc-border)] p-0 text-left hover:border-[var(--oc-accent)]"
                  >
                    {/* 原生 img：截圖僅供除錯，不需 Image Optimizer（計畫 §3.3）。 */}
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={screenshotUrl(shot.url)}
                      alt={`#${shot.sequence} ${shot.state} 截圖`}
                      className="h-20 w-full bg-[var(--oc-sunken)] object-cover"
                    />
                    <span className="flex items-baseline gap-1 border-t border-[var(--oc-border)] px-1.5 py-0.5 text-[10px]">
                      <span className="tabular text-[var(--oc-muted)]">
                        #{shot.sequence}
                      </span>
                      <span
                        className={cn(
                          "truncate tracking-wider uppercase",
                          TONE_TEXT_CLASS[purchaseStateTone(shot.state)]
                        )}
                      >
                        {shot.state}
                      </span>
                    </span>
                  </button>
                </DialogTrigger>
                <DialogContent className="max-w-5xl">
                  <DialogHeader>
                    <DialogTitle>
                      #{shot.sequence} {shot.state}
                    </DialogTitle>
                  </DialogHeader>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={screenshotUrl(shot.url)}
                    alt={`#${shot.sequence} ${shot.state} 截圖`}
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
