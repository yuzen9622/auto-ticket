"""獨立 Worker 行程：瀏覽器的唯一擁有者。

API Server 只派工與串流；所有 Playwright 操作、購票協調與登入都在這裡執行，
per-profile 互斥由 broker 的 `busy_profiles` 保證。
"""
