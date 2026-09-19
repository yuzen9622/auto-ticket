"use client"

import * as React from "react"

import { AccountCard } from "@/components/settings/account-card"
import { CredentialForm } from "@/components/settings/credential-form"
import { LocalProfileManager } from "@/components/settings/local-profile-manager"
import { SessionJobWatcher } from "@/components/settings/session-job-watcher"
import { type Platform } from "@/lib/contract"

export default function SettingsPage() {
  const [platform, setPlatform] = React.useState<Platform>("kktix")

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <div className="flex flex-col gap-4">
        <AccountCard platform={platform} onPlatformChange={setPlatform} />
        <CredentialForm platform={platform} onPlatformChange={setPlatform} />
      </div>
      <div className="flex flex-col gap-4">
        <SessionJobWatcher key={platform} platform={platform} />
        <LocalProfileManager />
      </div>
    </div>
  )
}

