"use client"

import * as React from "react"
import { Check, Copy } from "lucide-react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export function CopyButton({
  value,
  label,
  className,
  size = "icon-sm",
}: {
  value: string
  label?: string
  className?: string
  size?: "icon-sm" | "sm"
}) {
  const [copied, setCopied] = React.useState(false)

  React.useEffect(() => {
    if (!copied) return
    const t = setTimeout(() => setCopied(false), 1200)
    return () => clearTimeout(t)
  }, [copied])

  const onCopy = React.useCallback(() => {
    void navigator.clipboard.writeText(value).then(
      () => setCopied(true),
      () => setCopied(false)
    )
  }, [value])

  return (
    <Button
      type="button"
      variant="ghost"
      size={size}
      onClick={onCopy}
      aria-label={label ?? "複製"}
      title={label ?? "複製"}
      className={cn(className)}
    >
      {copied ? <Check className="text-[var(--oc-success)]" /> : <Copy />}
      {size === "sm" && <span>{copied ? "已複製" : (label ?? "複製")}</span>}
    </Button>
  )
}
