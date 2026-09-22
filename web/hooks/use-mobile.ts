import * as React from "react"

const MOBILE_BREAKPOINT = 768

export function useIsMobile() {
  // 首次 render 必須與伺服器端一致（伺服器沒有 window，只能當成桌面），
  // 實際寬度在掛載後才量測，否則窄視窗會在 hydration 時對不上 DOM。
  const [isMobile, setIsMobile] = React.useState(false)

  React.useEffect(() => {
    const mql = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`)
    const onChange = () => {
      setIsMobile(window.innerWidth < MOBILE_BREAKPOINT)
    }
    onChange()
    mql.addEventListener("change", onChange)
    return () => mql.removeEventListener("change", onChange)
  }, [])

  return isMobile
}
