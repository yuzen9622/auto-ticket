import * as React from "react"

import { cn } from "@/lib/utils"

/** 高密度表格：列高 36px、px-3、1px 分隔線、hover 底色，全部數值用 .tabular。 */
export interface Column<T> {
  key: string
  header: React.ReactNode
  cell: (row: T) => React.ReactNode
  className?: string
  headerClassName?: string
}

export function DataGrid<T>({
  columns,
  rows,
  rowKey,
  empty,
  className,
}: {
  columns: Column<T>[]
  rows: T[]
  rowKey: (row: T) => string
  empty?: React.ReactNode
  className?: string
}) {
  return (
    <div className={cn("oc-scroll min-w-0 overflow-x-auto", className)}>
      <table className="w-full border-collapse text-[12px]">
        <thead>
          <tr className="border-b border-[var(--oc-border)]">
            {columns.map((c) => (
              <th
                key={c.key}
                scope="col"
                className={cn(
                  "h-8 px-3 text-left text-[11px] tracking-wider whitespace-nowrap text-[var(--oc-muted)] uppercase",
                  c.headerClassName
                )}
              >
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td
                colSpan={columns.length}
                className="h-24 px-3 text-center text-[var(--oc-muted)]"
              >
                {empty ?? "無資料"}
              </td>
            </tr>
          ) : (
            rows.map((row) => (
              <tr
                key={rowKey(row)}
                className="h-9 border-b border-[var(--oc-border)] transition-colors duration-150 ease-out hover:bg-[var(--oc-surface-2)]"
              >
                {columns.map((c) => (
                  <td
                    key={c.key}
                    className={cn(
                      "px-3 align-middle whitespace-nowrap",
                      c.className
                    )}
                  >
                    {c.cell(row)}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  )
}
