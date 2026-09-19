"use client"

import * as React from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Eye, EyeOff } from "lucide-react"
import { useTranslations } from "next-intl"
import { toast } from "sonner"

import { Panel } from "@/components/terminal/panel"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { eraseCredentials, storeCredentials } from "@/lib/api/accounts"
import { ApiError } from "@/lib/api/client"
import { PLATFORM, PLATFORM_NAMES, type Platform } from "@/lib/contract"

export interface CredentialFormProps {
  platform?: Platform
  onPlatformChange?: (platform: Platform) => void
}

/**
 * 帳號與密碼只往後端加密 vault 送：不進 localStorage、不寫日誌，
 * 送出後立即清空 React state（計畫 §3.5 第 3 點）。
 */
export function CredentialForm({
  platform: platformProp,
  onPlatformChange,
}: CredentialFormProps) {
  const t = useTranslations("settings")
  const tc = useTranslations("common")
  const qc = useQueryClient()
  const [account, setAccount] = React.useState("")
  const [password, setPassword] = React.useState("")
  const [revealed, setRevealed] = React.useState(false)

  const [internalPlatform, setInternalPlatform] =
    React.useState<Platform>("kktix")
  const currentPlatform = platformProp ?? internalPlatform

  const clear = () => {
    setAccount("")
    setPassword("")
    setRevealed(false)
  }

  const handlePlatformChange = (next: Platform) => {
    clear()
    if (platformProp === undefined) {
      setInternalPlatform(next)
    }
    onPlatformChange?.(next)
  }

  const messageFor = (err: unknown): string => {
    if (err instanceof ApiError) {
      return err.code === "vault_locked" ? t("vaultLocked") : err.message
    }
    return t("actionFailed")
  }

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["account-status", currentPlatform] })
  }

  const store = useMutation({
    // 後端 schema 的欄位名仍是 access_key；UI 上只以「密碼」稱呼。
    mutationFn: () =>
      storeCredentials(currentPlatform, { account, access_key: password }),
    onSuccess: () => {
      clear()
      invalidate()
      toast.success(t("credentialSaved"))
    },
    onError: (e) => toast.error(messageFor(e)),
  })

  const erase = useMutation({
    mutationFn: () => eraseCredentials(currentPlatform),
    onSuccess: () => {
      clear()
      invalidate()
      toast.success(t("credentialErased"))
    },
    onError: (e) => toast.error(messageFor(e)),
  })

  const canSubmit = account.trim() !== "" && password !== "" && !store.isPending

  const vaultLocked =
    store.error instanceof ApiError && store.error.code === "vault_locked"

  return (
    <Panel title={t("credentialHeading")}>
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault()
          if (canSubmit) store.mutate()
        }}
      >
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">

          <div className="flex flex-col gap-1">
            <Label htmlFor="cred-account">{t("accountField")}</Label>
            <Input
              id="cred-account"
              value={account}
              onChange={(e) => setAccount(e.target.value)}
              placeholder={t("accountPlaceholder", {
                platform: PLATFORM_NAMES[currentPlatform],
              })}
              className="h-7"
              autoComplete="off"
            />
          </div>

          <div className="flex flex-col gap-1">
            <Label htmlFor="cred-key">{t("passwordField")}</Label>
            <div className="flex min-w-0 gap-1">
              <Input
                id="cred-key"
                type={revealed ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={t("passwordPlaceholder", {
                  platform: PLATFORM_NAMES[currentPlatform],
                })}
                className="h-7 min-w-0"
                autoComplete="off"
              />
              <Button
                type="button"
                size="icon-sm"
                variant="ghost"
                aria-label={revealed ? t("hidePassword") : t("showPassword")}
                onClick={() => setRevealed((v) => !v)}
              >
                {revealed ? <EyeOff /> : <Eye />}
              </Button>
            </div>
          </div>
        </div>

        {vaultLocked && (
          <p className="rounded-md border border-amber-500/50 bg-amber-500/10 px-2.5 py-1.5 text-xs text-amber-600 dark:text-amber-400">
            {t("vaultLocked")}
          </p>
        )}

        <div className="flex items-center gap-2">
          <Button
            type="submit"
            variant="default"
            size="sm"
            disabled={!canSubmit}
          >
            {t("saveCredential")}
          </Button>

          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button
                type="button"
                variant="destructive"
                size="sm"
                disabled={erase.isPending}
              >
                {t("eraseCredential")}
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>
                  {t("eraseHeading", {
                    platform: PLATFORM_NAMES[currentPlatform],
                  })}
                </AlertDialogTitle>
                <AlertDialogDescription>
                  {t("eraseBody")}
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
                <AlertDialogAction onClick={() => erase.mutate()}>
                  {t("eraseConfirm")}
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </form>
    </Panel>
  )
}
