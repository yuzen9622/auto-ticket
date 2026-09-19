import { ChevronDownIcon } from "lucide-react"

import {
  Accordion as AccordionPrimitive,
  AccordionItem as AccordionItemPrimitive,
  AccordionHeader as AccordionHeaderPrimitive,
  AccordionTrigger as AccordionTriggerPrimitive,
  AccordionContent as AccordionContentPrimitive,
  type AccordionProps as AccordionPrimitiveProps,
  type AccordionItemProps as AccordionItemPrimitiveProps,
  type AccordionTriggerProps as AccordionTriggerPrimitiveProps,
  type AccordionContentProps as AccordionContentPrimitiveProps,
} from "@/components/animate-ui/primitives/radix/accordion"
import { cn } from "@/lib/utils"

type AccordionProps = AccordionPrimitiveProps

function Accordion(props: AccordionProps) {
  return <AccordionPrimitive {...props} />
}

type AccordionItemProps = AccordionItemPrimitiveProps

function AccordionItem({ className, ...props }: AccordionItemProps) {
  return (
    <AccordionItemPrimitive
      className={cn("border-b last:border-b-0", className)}
      {...props}
    />
  )
}

type AccordionTriggerProps = AccordionTriggerPrimitiveProps & {
  showArrow?: boolean
}

function AccordionTrigger({
  className,
  children,
  showArrow = true,
  ...props
}: AccordionTriggerProps) {
  return (
    <AccordionHeaderPrimitive className="flex">
      <AccordionTriggerPrimitive
        className={cn(
          "flex flex-1 items-start justify-between gap-4 rounded-md py-4 text-left text-sm font-medium transition-all outline-none hover:underline focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50 [&[data-state=open]>svg]:rotate-180",
          className
        )}
        {...props}
      >
        {children}
        {showArrow && (
          <ChevronDownIcon className="pointer-events-none size-4 shrink-0 translate-y-0.5 text-muted-foreground transition-transform duration-200" />
        )}
      </AccordionTriggerPrimitive>
    </AccordionHeaderPrimitive>
  )
}

type AccordionContentProps = AccordionContentPrimitiveProps

function AccordionContent({
  className,
  children,
  ...props
}: AccordionContentProps) {
  return (
    <AccordionContentPrimitive {...props}>
      <div className={cn("pt-0 pb-4 text-sm", className)}>{children}</div>
    </AccordionContentPrimitive>
  )
}

export {
  Accordion,
  AccordionItem,
  AccordionTrigger,
  AccordionContent,
  type AccordionProps,
  type AccordionItemProps,
  type AccordionTriggerProps,
  type AccordionContentProps,
}
