"use client"

import * as React from "react"
import { Check, Copy } from "lucide-react"
import { useTranslations } from "next-intl"

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
  const t = useTranslations("common")
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
      aria-label={label ?? t("copy")}
      title={label ?? t("copy")}
      className={cn(className)}
    >
      {copied ? <Check className="text-emerald-500" /> : <Copy />}
      {size === "sm" && (
        <span>{copied ? t("copied") : (label ?? t("copy"))}</span>
      )}
    </Button>
  )
}
