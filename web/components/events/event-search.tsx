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

  // `navigate` 依賴 searchParams，每次導航都會換一個新的函式實體。把它放進下面的
  // 依賴陣列會變成「導航 → navigate 換新 → effect 再跑 → 再導航」的無限迴圈，
  // 頁面被反覆重掛，查詢永遠來不及完成。改用 ref 取用最新的那一份。
  const navigateRef = React.useRef(navigate)
  React.useEffect(() => {
    navigateRef.current = navigate
  }, [navigate])

  // 只有「使用者真的打過字」才自動同步網址。剛掛載時 debounce 還沒追上 draft，
  // 若不設這道閘，effect 會把「debounce 尚未追上」誤判成「使用者清空了輸入框」，
  // 於是先把 q 砍掉再加回來，在 / 與 /?q=… 之間來回震盪。
  const userTypedRef = React.useRef(false)

  // 使用者在輸入框打字，debounce 穩定後若與目前網址不同，以 replace 自動同步網址（不污染瀏覽歷史）
  React.useEffect(() => {
    if (!userTypedRef.current) return
    if (immediateQuery !== null) return
    const trimmedDebounced = debouncedDraft.trim()
    if (trimmedDebounced === query) return
    navigateRef.current(trimmedDebounced, true)
  }, [debouncedDraft, query, immediateQuery])

  const results = useQuery({
    queryKey: ["event-search", effectiveQuery],
    // 查詢鍵含 query，且舊請求會被中止——連打搜尋時舊結果不會蓋掉新查詢。
    queryFn: ({ signal }) => searchEvents(effectiveQuery, { signal }),
    enabled: effectiveQuery !== "",
  })

  const handleDraftChange = (value: string) => {
    userTypedRef.current = true
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

  const events = React.useMemo(
    () => (results.data?.results ?? []).filter((ev) => ev.status !== "CLOSED"),
    [results.data?.results]
  )

  return (
    <div className="relative flex flex-col gap-8 py-2">
      <div className="mx-auto flex w-full flex-col gap-3 md:w-[70%]">
        <div className="flex flex-col items-center gap-1.5 text-center">
          <h1 className="text-xl font-bold tracking-tight sm:text-2xl">
            {t("heading")}
          </h1>
        </div>

        <form className="flex flex-col gap-1.5" onSubmit={onSubmit}>
          <div className="flex flex-col gap-2 sm:flex-row">
            <div className="relative flex-1">
              <Label htmlFor="event-query" className="sr-only">
                {t("inputLabel")}
              </Label>
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
            <button type="submit" className="sr-only">
              {t("submit")}
            </button>
          </div>
        </form>
      </div>

      {/* 下方顯示活動的 card */}
      <section
        className="sticky top-0 flex flex-col gap-3"
        aria-label={t("resultsHeading")}
      >
        {results.data && events.length > 0 && (
          <div className="flex items-center justify-between px-1">
            <h2 className="text-sm font-semibold tracking-tight">
              {t("resultsHeading")}
            </h2>
            <span className="text-xs text-muted-foreground">
              {t("resultCount", { count: events.length })}
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
          ) : results.data && events.length > 0 ? (
            <ul className="grid min-w-0 grid-cols-1 gap-3 md:grid-cols-2">
              {events.map((event) => (
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
