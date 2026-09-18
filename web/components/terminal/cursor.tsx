import { cn } from "@/lib/utils"

/** 終端游標。設計規格中唯二允許的動態之一；prefers-reduced-motion 下自動靜止。 */
export function Cursor({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={cn(
        "oc-cursor inline-block h-[1em] w-[0.5em] translate-y-[0.15em] bg-[var(--oc-accent)]",
        className
      )}
    />
  )
}
