"use client"

import * as React from "react"
import { Plus, Trash2 } from "lucide-react"
import { useTranslations } from "next-intl"
import { toast } from "sonner"

import { Panel } from "@/components/terminal/panel"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  DEFAULT_PROFILE,
  isValidProfileName,
  loadBrowserProfiles,
  removeBrowserProfile,
  saveBrowserProfile,
  saveCurrentBrowserProfile,
} from "@/lib/browser-profile"

export interface BrowserProfileManagerProps {
  profile: string
  onProfileChange: (next: string) => void
}

export function BrowserProfileManager({
  profile,
  onProfileChange,
}: BrowserProfileManagerProps) {
  const t = useTranslations("settings")
  const [profiles, setProfiles] = React.useState<string[]>([DEFAULT_PROFILE])
  const [newProfileName, setNewProfileName] = React.useState("")
  const [isAdding, setIsAdding] = React.useState(false)

  React.useEffect(() => {
    queueMicrotask(() => {
      setProfiles(loadBrowserProfiles())
    })
  }, [])

  const handleSelect = (val: string) => {
    onProfileChange(val)
    saveCurrentBrowserProfile(val)
  }

  const handleAdd = (e: React.FormEvent) => {
    e.preventDefault()
    const trimmed = newProfileName.trim()
    if (!trimmed) return

    if (!isValidProfileName(trimmed)) {
      toast.error(t("profileNameInvalid"))
      return
    }

    if (profiles.includes(trimmed)) {
      toast.error(t("profileAlreadyExists"))
      return
    }

    try {
      const next = saveBrowserProfile(trimmed)
      setProfiles(next)
      setNewProfileName("")
      setIsAdding(false)
      onProfileChange(trimmed)
      saveCurrentBrowserProfile(trimmed)
      toast.success(t("profileAdded", { name: trimmed }))
    } catch {
      toast.error(t("profileAddFailed"))
    }
  }

  const handleDelete = (target: string) => {
    if (target === DEFAULT_PROFILE) {
      toast.error(t("cannotDeleteDefaultProfile"))
      return
    }

    const next = removeBrowserProfile(target)
    setProfiles(next)
    if (profile === target) {
      onProfileChange(DEFAULT_PROFILE)
      saveCurrentBrowserProfile(DEFAULT_PROFILE)
    }
    toast.success(t("profileDeleted", { name: target }))
  }

  return (
    <Panel title={t("browserProfileHeading")}>
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <label
            htmlFor="current-browser-profile-select"
            className="text-xs font-semibold tracking-wider text-muted-foreground uppercase"
          >
            {t("currentBrowserProfile")}
          </label>
          <div className="flex items-center gap-2">
            <Select value={profile} onValueChange={handleSelect}>
              <SelectTrigger
                id="current-browser-profile-select"
                className="h-8 flex-1 text-xs"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {profiles.map((p) => (
                  <SelectItem key={p} value={p} className="text-xs">
                    {p === DEFAULT_PROFILE ? `${p} (${t("default")})` : p}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            {profile !== DEFAULT_PROFILE && (
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label={t("deleteProfile", { name: profile })}
                onClick={() => handleDelete(profile)}
              >
                <Trash2 className="size-3.5 text-muted-foreground hover:text-destructive" />
              </Button>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            {t("browserProfileDescription")}
          </p>
        </div>

        {isAdding ? (
          <form onSubmit={handleAdd} className="flex flex-col gap-2 rounded-md border border-border/50 p-2.5">
            <div className="flex items-center gap-2">
              <Input
                value={newProfileName}
                onChange={(e) => setNewProfileName(e.target.value)}
                placeholder={t("newProfilePlaceholder")}
                className="h-7 text-xs"
                autoFocus
              />
              <Button type="submit" size="sm" className="h-7 px-2.5 text-xs">
                {t("add")}
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-xs"
                onClick={() => {
                  setIsAdding(false)
                  setNewProfileName("")
                }}
              >
                {t("cancel")}
              </Button>
            </div>
            <span className="text-[11px] text-muted-foreground">
              {t("profileNameHint")}
            </span>
          </form>
        ) : (
          <div className="flex justify-start">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 gap-1 text-xs"
              onClick={() => setIsAdding(true)}
            >
              <Plus className="size-3" />
              {t("createProfile")}
            </Button>
          </div>
        )}
      </div>
    </Panel>
  )
}
