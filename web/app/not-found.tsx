import Link from "next/link"

import { EmptyState } from "@/components/terminal/empty-state"

export default function NotFound() {
  return (
    <EmptyState
      message="404 — 找不到這個頁面"
      action={<Link href="/">回到儀表板</Link>}
    />
  )
}
