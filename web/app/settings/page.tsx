"use client"

import { AccountCard } from "@/components/settings/account-card"
import { CredentialForm } from "@/components/settings/credential-form"
import { LocalProfileManager } from "@/components/settings/local-profile-manager"
import { SessionJobWatcher } from "@/components/settings/session-job-watcher"

/** 目前僅 kktix 有 resolver 與帳號流程（計畫 §未決事項 3）。 */
const PLATFORM = "kktix"

export default function SettingsPage() {
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <div className="flex flex-col gap-4">
        <AccountCard platform={PLATFORM} />
        <CredentialForm platform={PLATFORM} />
      </div>
      <div className="flex flex-col gap-4">
        <SessionJobWatcher platform={PLATFORM} />
        <LocalProfileManager />
      </div>
    </div>
  )
}
