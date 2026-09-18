from __future__ import annotations

from starlette.testclient import TestClient

from api.main import create_app
from storage.database import Database
from tests.netguard import netguard_autouse  # noqa: F401


def test_ws_endpoint_connect_and_control(tmp_path) -> None:
        db_path = tmp_path / "ws_test.db"
        db = Database(db_path)
        app = create_app(db=db)

        with TestClient(app) as client:
                task_id = "task_ws_test_01"
                with client.websocket_connect(f"/ws/tasks/{task_id}") as ws:
                        # 1. 應收到 snapshot 訊息
                        init_msg = ws.receive_json()
                        assert init_msg["type"] == "TASK_LOG"
                        assert init_msg["payload"]["phase"] == "snapshot"
                        assert init_msg["task_id"] == task_id

                        # 2. 緊接著是倒數快照，讓重連的前端知道目前階段
                        clock_msg = ws.receive_json()
                        assert clock_msg["type"] == "CLOCK_TICK"
                        assert clock_msg["payload"]["phase"] == "finished"
                        assert clock_msg["payload"]["time_to_sale_ms"] is None

                        # 3. 發送控制指令 PAUSE
                        ws.send_json({"action": "PAUSE", "task_id": task_id})
                        ack = ws.receive_json()
                        assert ack["type"] == "TASK_LOG"
                        assert ack["payload"]["action"] == "PAUSE"
                        assert ack["payload"]["accepted"] is True

                        # 4. 發送無效控制指令（task_id 不符）應回 ERROR frame 而不中斷
                        ws.send_json({"action": "RESUME", "task_id": "other_task"})
                        err_msg = ws.receive_json()
                        assert err_msg["type"] == "ERROR"
                        assert err_msg["payload"]["reason"] == "task_id_mismatch"
