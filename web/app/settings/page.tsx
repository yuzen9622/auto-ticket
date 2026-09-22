"use client"

import * as React from "react"
import { useSearchParams } from "next/navigation"

import { AccountCard } from "@/components/settings/account-card"
import { BrowserProfileManager } from "@/components/settings/browser-profile-manager"
import { CredentialForm } from "@/components/settings/credential-form"
import { LocalProfileManager } from "@/components/settings/local-profile-manager"
import { SessionJobWatcher } from "@/components/settings/session-job-watcher"
import { DEFAULT_PROFILE, loadCurrentBrowserProfile } from "@/lib/browser-profile"
import { PLATFORM, type Platform } from "@/lib/contract"

function SettingsPanels() {
  const searchParams = useSearchParams()
  const qPlatform = searchParams.get("platform")
  const validPlatform = PLATFORM.includes(qPlatform as Platform)
    ? (qPlatform as Platform)
    : null

  const [platform, setPlatform] = React.useState<Platform>(
    validPlatform ?? "kktix"
  )
  const [prevValidPlatform, setPrevValidPlatform] =
    React.useState(validPlatform)
  if (validPlatform !== prevValidPlatform) {
    setPrevValidPlatform(validPlatform)
    if (validPlatform) {
      setPlatform(validPlatform)
    }
  }

  const [profile, setProfile] = React.useState<string>(DEFAULT_PROFILE)

  React.useEffect(() => {
    queueMicrotask(() => {
      setProfile(loadCurrentBrowserProfile())
    })
  }, [])

  return (
    <>
      <div className="flex flex-col gap-4">
        <AccountCard platform={platform} onPlatformChange={setPlatform} />
        <CredentialForm platform={platform} onPlatformChange={setPlatform} />
      </div>
      <div className="flex flex-col gap-4">
        <BrowserProfileManager profile={profile} onProfileChange={setProfile} />
        <SessionJobWatcher
          key={`${platform}-${profile}`}
          platform={platform}
          profile={profile}
        />
        <LocalProfileManager />
      </div>
    </>
  )
}

export default function SettingsPage() {
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      {/* useSearchParams 會讓整個 page 退回 client render，沒有 Suspense 邊界
          時 `next build` 直接在預渲染階段失敗。邊界擺在讀取 query 的那一層，
          外層的版面配置仍然能靜態產出。 */}
      <React.Suspense fallback={null}>
        <SettingsPanels />
      </React.Suspense>
    </div>
  )
}
