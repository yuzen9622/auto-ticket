"use client"

import type * as React from "react"
import { Toaster as Sonner, type ToasterProps } from "sonner"
import {
  CircleCheckIcon,
  InfoIcon,
  OctagonXIcon,
  TriangleAlertIcon,
} from "lucide-react"

/** 設計規格明載淺色「Not Recommended」，本專案只做深色，不提供切換入口。 */
const Toaster = ({ ...props }: ToasterProps) => {
  return (
    <Sonner
      theme="dark"
      className="toaster group"
      icons={{
        success: <CircleCheckIcon className="size-4" />,
        info: <InfoIcon className="size-4" />,
        warning: <TriangleAlertIcon className="size-4" />,
        error: <OctagonXIcon className="size-4" />,
      }}
      style={
        {
          "--normal-bg": "var(--oc-surface)",
          "--normal-text": "var(--oc-fg)",
          "--normal-border": "var(--oc-border)",
          "--border-radius": "4px",
        } as React.CSSProperties
      }
      toastOptions={{
        classNames: {
          toast:
            "cn-toast rounded-[4px] border border-[var(--oc-border)] font-mono text-[12px]",
        },
      }}
      {...props}
    />
  )
}

export { Toaster }
