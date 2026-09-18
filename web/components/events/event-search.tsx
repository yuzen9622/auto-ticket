"use client"

import * as React from "react"
import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { useTranslations } from "next-intl"
import { useQuery } from "@tanstack/react-query"
import { SearchIcon } from "lucide-react"

import { EventCard } from "@/components/events/event-card"
import { EmptyState } from "@/components/terminal/empty-state"

import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useDebounce } from "@/hooks/use-debounce"
import { ApiError } from "@/lib/api/client"
import { searchEvents } from "@/lib/api/events"

const SEARCH_PARAM = "q"

/** 後端錯誤碼 → 搜尋頁自己的說法，比通用錯誤訊息更貼近當下在做的事。 */
const SEARCH_MESSAGE_KEY: Record<string, string> = {
  network_error: "networkError",
  invalid_base_url: "networkError",
  upstream_failed: "upstreamError",
  invalid_request: "invalidRequest",
}

export function EventSearch() {
  const t = useTranslations("search")
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()

  // 網址是查詢條件的初始真相與持久化：重新整理、上一頁與下一頁都靠它還原。
  const query = (searchParams.get(SEARCH_PARAM) ?? "").trim()
  const [draft, setDraft] = React.useState(query)
  const debouncedDraft = useDebounce(draft, 300)

  // 手動提交或外部同步時的立即查詢（不用等 debounce）
  const [immediateQuery, setImmediateQuery] = React.useState<string | null>(
    null
  )

  // 網址變了就把輸入框拉回同步（上一頁／下一頁也走這裡）。
  // 在 render 期間調整 state 是 React 建議的做法，比 effect 少一輪繪製。
  const [syncedQuery, setSyncedQuery] = React.useState(query)
  if (syncedQuery !== query) {
    setSyncedQuery(query)
    setDraft(query)
    setImmediateQuery(query)
  }

  // 實際生效的查詢字串：手動送出/外部同步優先，否則取 debounce 結果
  const effectiveQuery = (
    immediateQuery !== null ? immediateQuery : debouncedDraft
  ).trim()

  const navigate = React.useCallback(
    (next: string, replace = false) => {
      const trimmed = next.trim()
      const params = new URLSearchParams(searchParams.toString())
      if (trimmed === "") {
        params.delete(SEARCH_PARAM)
      } else {
        params.set(SEARCH_PARAM, trimmed)
      }
      const search = params.toString()
      const target = search ? `${pathname}?${search}` : pathname
      if (replace) {
        router.replace(target)
      } else {
        router.push(target)
      }
    },
    [pathname, router, searchParams]
  )

  // 使用者在輸入框打字，debounce 穩定後若與目前網址不同，以 replace 自動同步網址（不污染瀏覽歷史）
  React.useEffect(() => {
    const trimmedDebounced = debouncedDraft.trim()
    if (immediateQuery === null && trimmedDebounced !== query) {
      navigate(trimmedDebounced, true)
    }
  }, [debouncedDraft, query, immediateQuery, navigate])

  const results = useQuery({
    queryKey: ["event-search", effectiveQuery],
    // 查詢鍵含 query，且舊請求會被中止——連打搜尋時舊結果不會蓋掉新查詢。
    queryFn: ({ signal }) => searchEvents(effectiveQuery, { signal }),
    enabled: effectiveQuery !== "",
  })

  const handleDraftChange = (value: string) => {
    setDraft(value)
    setImmediateQuery(null)
  }

  const onSubmit = (event: React.FormEvent) => {
    event.preventDefault()
    const trimmed = draft.trim()
    setImmediateQuery(trimmed)
    navigate(trimmed, false)
  }

  const errorMessage = () => {
    const error = results.error
    if (error instanceof ApiError) {
      return t(SEARCH_MESSAGE_KEY[error.code] ?? "unknownError")
    }
    return t("unknownError")
  }

  return (
    <div className="relative flex flex-col gap-8 py-2">
      {/* 搜尋框框上方致中 70% width */}
      <div className="mx-auto flex w-full flex-col gap-3 md:w-[70%]">
        <div className="flex flex-col items-center gap-1.5 text-center">
          <h1 className="text-xl font-bold tracking-tight sm:text-2xl">
            {t("heading")}
          </h1>
          <p className="text-xs text-muted-foreground sm:text-sm">
            {t("subheading")}
          </p>
        </div>

        <form className="flex flex-col gap-1.5" onSubmit={onSubmit}>
          <Label
            htmlFor="event-query"
            className="text-xs font-medium text-muted-foreground"
          >
            {t("inputLabel")}
          </Label>
          <div className="flex flex-col gap-2 sm:flex-row">
            <div className="relative flex-1">
              <SearchIcon
                className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden
              />
              <Input
                id="event-query"
                name={SEARCH_PARAM}
                type="search"
                value={draft}
                onChange={(event) => handleDraftChange(event.target.value)}
                placeholder={t("placeholder")}
                className="h-10 w-full min-w-0 pl-9"
                autoComplete="off"
              />
            </div>
          </div>
        </form>
      </div>

      {/* 下方顯示活動的 card */}
      <section
        className="sticky top-0 flex flex-col gap-3"
        aria-label={t("resultsHeading")}
      >
        {results.data && results.data.results.length > 0 && (
          <div className="flex items-center justify-between px-1">
            <h2 className="text-sm font-semibold tracking-tight">
              {t("resultsHeading")}
            </h2>
            <span className="text-xs text-muted-foreground">
              {t("resultCount", { count: results.data.results.length })}
            </span>
          </div>
        )}

        <div aria-live="polite" aria-busy={results.isFetching}>
          {effectiveQuery === "" ? (
            <EmptyState message={t("initial")} hint={t("initialHint")} />
          ) : results.isPending || results.isFetching ? (
            <EmptyState message={t("searching")} />
          ) : results.isError ? (
            <EmptyState message={t("errorTitle")} hint={errorMessage()} />
          ) : results.data && results.data.results.length > 0 ? (
            <ul className="grid min-w-0 grid-cols-1 gap-3 md:grid-cols-2">
              {results.data.results.map((event) => (
                <EventCard key={event.id} event={event} />
              ))}
            </ul>
          ) : (
            <EmptyState message={t("empty")} hint={t("emptyHint")} />
          )}
        </div>
      </section>
    </div>
  )
}
