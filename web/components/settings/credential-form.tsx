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
import { eraseCredentials, storeCookie, storeCredentials } from "@/lib/api/accounts"
import { ApiError } from "@/lib/api/client"
import { PLATFORM_NAMES, type Platform } from "@/lib/contract"

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
  onPlatformChange: _onPlatformChange,
}: CredentialFormProps) {
  const t = useTranslations("settings")
  const tc = useTranslations("common")
  const qc = useQueryClient()
  const currentPlatform = platformProp ?? "kktix"
  const [account, setAccount] = React.useState("")
  const [password, setPassword] = React.useState("")
  const [cookieValue, setCookieValue] = React.useState("")
  const [authType, setAuthType] = React.useState<"password" | "cookie">(
    currentPlatform === "tixcraft" ? "cookie" : "password"
  )
  const [revealed, setRevealed] = React.useState(false)

  const [prevPlatform, setPrevPlatform] = React.useState(currentPlatform)
  if (prevPlatform !== currentPlatform) {
    setPrevPlatform(currentPlatform)
    setAccount("")
    setPassword("")
    setCookieValue("")
    setAuthType(currentPlatform === "tixcraft" ? "cookie" : "password")
    setRevealed(false)
  }

  const clear = () => {
    setAccount("")
    setPassword("")
    setCookieValue("")
    setRevealed(false)
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

  const cookieKey = currentPlatform === "tixcraft" ? "TIXUISID" : "ibonqware"
  const storeCookieMut = useMutation({
    mutationFn: () =>
      storeCookie(currentPlatform, {
        cookies: { [cookieKey]: cookieValue.trim() },
      }),
    onSuccess: () => {
      clear()
      invalidate()
      toast.success("Cookie 已寫入後端保管庫")
    },
    onError: (e) => toast.error(messageFor(e)),
  })

  // 拓元主要採用社群登入，密碼可為選填；KKTIX 與 ibon 則帳號與密碼均為必填。
  const canSubmit =
    authType === "cookie"
      ? cookieValue.trim() !== "" && !storeCookieMut.isPending
      : account.trim() !== "" &&
        (currentPlatform === "tixcraft" || password !== "") &&
        !store.isPending

  const vaultLocked =
    store.error instanceof ApiError && store.error.code === "vault_locked"

  const accountLabel = t.has(`accountField_${currentPlatform}`)
    ? t(`accountField_${currentPlatform}`)
    : t("accountField")
  const accountPlaceholder = t.has(`accountPlaceholder_${currentPlatform}`)
    ? t(`accountPlaceholder_${currentPlatform}`)
    : t("accountPlaceholder", { platform: PLATFORM_NAMES[currentPlatform] })

  const passwordLabel = t.has(`passwordField_${currentPlatform}`)
    ? t(`passwordField_${currentPlatform}`)
    : t("passwordField")
  const passwordPlaceholder = t.has(`passwordPlaceholder_${currentPlatform}`)
    ? t(`passwordPlaceholder_${currentPlatform}`)
    : t("passwordPlaceholder", { platform: PLATFORM_NAMES[currentPlatform] })

  return (
    <Panel title={t("credentialHeading")}>
      {currentPlatform === "ibon" && (
        <div className="mb-3 flex items-center gap-2 border-b pb-2">
          <Button
            type="button"
            size="xs"
            variant={authType === "password" ? "default" : "outline"}
            onClick={() => setAuthType("password")}
          >
            帳號密碼
          </Button>
          <Button
            type="button"
            size="xs"
            variant={authType === "cookie" ? "default" : "outline"}
            onClick={() => setAuthType("cookie")}
          >
            Cookie (Session)
          </Button>
        </div>
      )}

      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault()
          if (!canSubmit) return
          if (authType === "cookie") {
            storeCookieMut.mutate()
          } else {
            store.mutate()
          }
        }}
      >
        {authType === "cookie" ? (
          <div className="flex flex-col gap-1">
            <Label htmlFor="cred-cookie">
              {currentPlatform === "tixcraft"
                ? "TIXUISID Cookie"
                : "ibonqware Cookie"}
            </Label>
            <Input
              id="cred-cookie"
              type="password"
              value={cookieValue}
              onChange={(e) => setCookieValue(e.target.value)}
              placeholder={
                currentPlatform === "tixcraft"
                  ? "請貼上 TIXUISID Cookie 值"
                  : "請貼上 ibonqware Cookie 值"
              }
              className="h-7"
              autoComplete="off"
            />
            <span className="text-[11px] text-muted-foreground">
              Cookie 僅儲存於本機加密保管庫，送出後前端立即清空且永不回填。
            </span>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <div className="flex flex-col gap-1">
              <Label htmlFor="cred-account">{accountLabel}</Label>
              <Input
                id="cred-account"
                value={account}
                onChange={(e) => setAccount(e.target.value)}
                placeholder={accountPlaceholder}
                className="h-7"
                autoComplete="off"
              />
            </div>

            <div className="flex flex-col gap-1">
              <Label htmlFor="cred-key">{passwordLabel}</Label>
              <div className="relative flex w-full items-center">
                <Input
                  id="cred-key"
                  type={revealed ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder={passwordPlaceholder}
                  className="h-7 w-full pr-8"
                  autoComplete="off"
                />
                <Button
                  type="button"
                  size="icon-xs"
                  variant="ghost"
                  className="absolute right-1 text-muted-foreground hover:text-foreground"
                  aria-label={revealed ? t("hidePassword") : t("showPassword")}
                  onClick={() => setRevealed((v) => !v)}
                >
                  {revealed ? (
                    <EyeOff className="size-3.5" />
                  ) : (
                    <Eye className="size-3.5" />
                  )}
                </Button>
              </div>
            </div>
          </div>
        )}

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
