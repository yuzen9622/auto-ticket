import { getRequestConfig } from "next-intl/server"

import { locale, messages } from "@/lib/i18n/config"

export default getRequestConfig(async () => ({ locale, messages }))
