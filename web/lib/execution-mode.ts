import type { ExecutionMode } from "@/lib/api/types"

/** 產品介面可選的執行模式。付款 adapter 由後端允許清單決定，前端不指定 adapter。 */
export const EXECUTION_MODE: readonly ExecutionMode[] = ["live", "mock"]

/** 目前只有這個票券平台有帳號流程；正式模式的前置檢查對準它。 */
export const LIVE_PLATFORM = "kktix"
