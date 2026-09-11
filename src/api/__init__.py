"""HTTP / WebSocket 前台。

**零 Playwright 相依**（守門員 G32）：所有需要瀏覽器的動作一律轉成 broker job，
由獨佔 profile 的 Worker 行程執行。Chromium 的 `launch_persistent_context` 會對
`user_data_dir` 上鎖，兩個行程同時開同一個 profile 必定失敗。
"""
