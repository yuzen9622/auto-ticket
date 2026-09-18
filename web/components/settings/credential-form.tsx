"use client"

import * as React from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Eye, EyeOff } from "lucide-react"
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
import { eraseCredentials, storeCredentials } from "@/lib/api/accounts"
import { ApiError } from "@/lib/api/client"

const VAULT_LOCKED_HINT =
  "vault 未解鎖：請設定環境變數 AUTO_TICKET_VAULT_KEY 後重啟 API Server，再重新儲存帳號與密碼。"

function messageFor(err: unknown): string {
  if (err instanceof ApiError) {
    return err.code === "vault_locked" ? VAULT_LOCKED_HINT : err.message
  }
  return "操作失敗"
}

/**
 * 帳號與密碼只往後端加密 vault 送：不進 localStorage、不寫日誌，
 * 送出後立即清空 React state（計畫 §3.5 第 3 點）。
 */
export function CredentialForm({ platform }: { platform: string }) {
  const qc = useQueryClient()
  const [account, setAccount] = React.useState("")
  const [password, setPassword] = React.useState("")
  const [revealed, setRevealed] = React.useState(false)

  const clear = () => {
    setAccount("")
    setPassword("")
    setRevealed(false)
  }

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["account-status", platform] })
  }

  const store = useMutation({
    // 後端 schema 的欄位名仍是 access_key；UI 上只以「密碼」稱呼。
    mutationFn: () =>
      storeCredentials(platform, { account, access_key: password }),
    onSuccess: () => {
      clear()
      invalidate()
      toast.success("帳號與密碼已寫入後端 vault")
    },
    onError: (e) => toast.error(messageFor(e)),
  })

  const erase = useMutation({
    mutationFn: () => eraseCredentials(platform),
    onSuccess: () => {
      clear()
      invalidate()
      toast.success("帳號與密碼已清除")
    },
    onError: (e) => toast.error(messageFor(e)),
  })

  const canSubmit = account.trim() !== "" && password !== "" && !store.isPending

  const vaultLocked =
    store.error instanceof ApiError && store.error.code === "vault_locked"

  return (
    <Panel title="帳號與密碼">
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault()
          if (canSubmit) store.mutate()
        }}
      >
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <div className="flex flex-col gap-1">
            <Label htmlFor="cred-account">帳號 (Email)</Label>
            <Input
              id="cred-account"
              value={account}
              onChange={(e) => setAccount(e.target.value)}
              placeholder="請輸入 KKTIX 會員帳號"
              className="h-7"
              autoComplete="off"
            />
          </div>

          <div className="flex flex-col gap-1">
            <Label htmlFor="cred-key">密碼 (Password)</Label>
            <div className="flex min-w-0 gap-1">
              <Input
                id="cred-key"
                type={revealed ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="請輸入 KKTIX 會員密碼"
                className="h-7 min-w-0"
                autoComplete="off"
              />
              <Button
                type="button"
                size="icon-sm"
                variant="ghost"
                aria-label={revealed ? "隱藏密碼" : "顯示密碼"}
                onClick={() => setRevealed((v) => !v)}
              >
                {revealed ? <EyeOff /> : <Eye />}
              </Button>
            </div>
          </div>
        </div>

        <p className="text-[10px] text-[var(--oc-muted)]">
          帳號與密碼只會送往本機後端的加密
          vault，不會存進瀏覽器，也不會出現在任何日誌。
        </p>

        {vaultLocked && (
          <p className="rounded-[4px] border border-[var(--oc-warning)] px-2 py-1 text-[11px] text-[var(--oc-warning)]">
            {VAULT_LOCKED_HINT}
          </p>
        )}

        <div className="flex items-center gap-2">
          <Button
            type="submit"
            variant="accent"
            size="sm"
            disabled={!canSubmit}
          >
            儲存帳號密碼
          </Button>

          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button
                type="button"
                variant="destructive"
                size="sm"
                disabled={erase.isPending}
              >
                清除帳號密碼
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>
                  清除 {platform} 帳號與密碼？
                </AlertDialogTitle>
                <AlertDialogDescription>
                  將從後端 vault
                  刪除此平台的帳號與密碼，之後自動登入會失敗。此操作無法復原。
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>取消</AlertDialogCancel>
                <AlertDialogAction onClick={() => erase.mutate()}>
                  確認清除
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </form>
    </Panel>
  )
}
