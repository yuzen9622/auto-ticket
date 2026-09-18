import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "cn"
import { Slot } from "radix-ui"

/**
 * OpenCode Terminal Mono 按鈕：1px 邊框、4px 銳角、零陰影、零漸層。
 * 層級只用 --oc-surface / --oc-surface-2 表達。
 */
const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-2 rounded-[4px] border text-[12px] font-medium whitespace-nowrap transition-colors duration-150 ease-out outline-none select-none disabled:pointer-events-none disabled:opacity-40 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-3.5",
  {
    variants: {
      variant: {
        default:
          "border-[var(--oc-border)] bg-[var(--oc-surface-2)] text-[var(--oc-fg)] hover:bg-[var(--oc-surface)]",
        outline:
          "border-[var(--oc-border)] bg-transparent text-[var(--oc-fg)] hover:bg-[var(--oc-surface-2)]",
        secondary:
          "border-[var(--oc-border)] bg-[var(--oc-surface)] text-[var(--oc-fg)] hover:bg-[var(--oc-surface-2)]",
        ghost:
          "border-transparent bg-transparent text-[var(--oc-muted)] hover:bg-[var(--oc-surface-2)] hover:text-[var(--oc-fg)]",
        accent:
          "border-[var(--oc-accent)] bg-transparent text-[var(--oc-accent)] hover:bg-[var(--oc-surface-2)]",
        destructive:
          "border-[var(--oc-danger)] bg-transparent text-[var(--oc-danger)] hover:bg-[var(--oc-surface-2)]",
        link: "border-transparent bg-transparent text-[var(--oc-accent)] underline underline-offset-2",
      },
      size: {
        default: "h-7 px-5 py-1",
        sm: "h-6 px-3 text-[11px]",
        lg: "h-8 px-6",
        icon: "size-7 px-0",
        "icon-sm": "size-6 px-0",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

function Button({
  className,
  variant = "default",
  size = "default",
  asChild = false,
  ...props
}: React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean
  }) {
  const Comp = asChild ? Slot.Root : "button"

  return (
    <Comp
      data-slot="button"
      data-variant={variant}
      data-size={size}
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  )
}

export { Button, buttonVariants }
