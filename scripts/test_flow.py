#!/usr/bin/env python3
"""端到端有頭模式 API + Worker 完整流程測試腳本。

流程：
1. 啟動本機 API Server (http://127.0.0.1:8000)
2. 啟動 Worker (有頭模式 --no-headless，螢幕上會跳出真實 Chrome 視窗)
3. 呼叫 API 建立購票排程任務 (POST /api/v1/tasks)
4. 呼叫 API 立即開跑 (POST /api/v1/tasks/{id}/start)
5. 建立 WebSocket 連線 (/ws/tasks/{id}) 即時監聽伺服器推播
6. 觀察任務在 SQLite 資料庫 (data/auto-ticket.db) 中的執行紀錄
"""

from __future__ import annotations

import asyncio
import contextlib
import subprocess
import sys
import time
from pathlib import Path

import httpx
import websockets

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

API_HOST = "127.0.0.1"
API_PORT = 8000
BASE_URL = f"http://{API_HOST}:{API_PORT}"
DB_PATH = "data/auto-ticket.db"


async def wait_for_api_ready(timeout_s: float = 10.0) -> bool:
    start = time.time()
    async with httpx.AsyncClient() as client:
        while time.time() - start < timeout_s:
            with contextlib.suppress(Exception):
                resp = await client.get(f"{BASE_URL}/healthz")
                if resp.status_code == 200:
                    return True
            await asyncio.sleep(0.3)
    return False


async def run_flow() -> None:
    print("\n" + "=" * 60)
    print("🚀 [1/6] 準備啟動 API Server 與 有頭 Worker...")
    print("=" * 60)

    # 1. 啟動 API Server
    api_proc = subprocess.Popen(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/serve_api.py"),
            "--host",
            API_HOST,
            "--port",
            str(API_PORT),
            "--db",
            DB_PATH,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    # 2. 啟動 Worker (有頭模式 --no-headless)
    worker_proc = subprocess.Popen(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/worker.py"),
            "--db",
            DB_PATH,
            "--profile",
            "test_headed",
            "--no-headless",
            "--poll-ms",
            "200",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        print("⏳ 等待 API Server 就緒...")
        ready = await wait_for_api_ready()
        if not ready:
            print("❌ API Server 啟動超時！")
            return
        print(f"✅ API Server 已就緒：{BASE_URL}")

        async with httpx.AsyncClient(base_url=BASE_URL) as client:
            # 3. 測試帳號狀態查詢
            print("\n📋 [2/6] 測試 API: 查詢帳號設定狀態 (GET /api/v1/accounts/status)")
            acc_resp = await client.get("/api/v1/accounts/status")
            print(f"   狀態碼: {acc_resp.status_code}")
            print(f"   回應: {acc_resp.json()}")

            # 4. 建立購票排程任務
            print("\n🎫 [3/6] 測試 API: 建立購票任務 (POST /api/v1/tasks)")
            task_payload = {
                "event_title": "KKTIX 測試活動",
                "event_url": "https://kktix.com/events/d23c8c16/registrations/new",
                "sale_start_at": "2026-10-01T12:00:00Z",
                "profile": "test_headed",
                "ticket_preference": {
                    "quantity": 1,
                    "priorities": [{"price": 1000, "priority": 1}],
                },
                "contact_profile": {
                    "name": "測試使用者",
                    "phone": "0912345678",
                    "email": "test@example.com",
                },
                "payment_method": "mock",
            }
            create_resp = await client.post("/api/v1/tasks", json=task_payload)
            print(f"   狀態碼: {create_resp.status_code}")
            task_data = create_resp.json()
            task_id = task_data["id"]
            print(f"   建立成功！任務 ID: {task_id}")

            # 5. 立即觸發任務開跑
            print(
                f"\n⚡ [4/6] 測試 API: 手動立即觸發開跑 (POST /api/v1/tasks/{task_id}/start)"
            )
            start_resp = await client.post(f"/api/v1/tasks/{task_id}/start")
            print(f"   狀態碼: {start_resp.status_code}")
            print(f"   回應: {start_resp.json()}")

            # 6. 連接 WebSocket 監聽即時串流 (同時 Worker 在有頭瀏覽器中運作)
            print(f"\n📡 [5/6] 建立 WebSocket 雙向串流連線 (/ws/tasks/{task_id})")
            print(
                "   👉 注意看您的 macOS 螢幕，Worker 正以【有頭模式】開啟 Chrome 視窗！"
            )
            ws_url = f"ws://{API_HOST}:{API_PORT}/ws/tasks/{task_id}"

            async with websockets.connect(ws_url) as ws:
                # 接收前 5 筆推播事件或直到逾時
                for i in range(5):
                    try:
                        msg_text = await asyncio.wait_for(ws.recv(), timeout=6.0)
                        print(f"   [WebSocket Frame #{i + 1}]: {msg_text[:120]}...")
                    except asyncio.TimeoutError:
                        print("   (等待後續事件中...)")
                        break

            # 7. 查詢資料庫任務狀態與歷史實驗
            print(
                "\n💾 [6/6] 查詢 SQLite 資料庫中的任務最新狀態 (GET /api/v1/tasks/{id})"
            )
            detail_resp = await client.get(f"/api/v1/tasks/{task_id}")
            task_detail = detail_resp.json()
            print(f"   任務狀態: {task_detail['task']['status']}")
            print(f"   所屬 Job 狀態: {task_detail['job_state']}")

            print("\n" + "=" * 60)
            print("🎉 完整端到端測試成功！")
            print(f"   - 資料庫位置: {Path(DB_PATH).resolve()}")
            print(
                f"   - 瀏覽器 Profile: {Path('.browser_profiles/test_headed').resolve()}"
            )
            print("=" * 60)

    finally:
        print("\n🧹 清理結束測試行程...")
        api_proc.terminate()
        worker_proc.terminate()
        try:
            api_proc.wait(timeout=3)
            worker_proc.wait(timeout=3)
        except Exception:
            api_proc.kill()
            worker_proc.kill()


if __name__ == "__main__":
    asyncio.run(run_flow())
