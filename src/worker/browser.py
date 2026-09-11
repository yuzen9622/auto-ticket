from __future__ import annotations

from pathlib import Path
from typing import Any

from api.schemas.ws import ServerMessageType
from broker.outbox import OutboxWriter
from browser.context_factory import BrowserProfile
from browser.manager import PlaywrightManager
from telemetry.timeline import TimelineRecorder


class StreamingPlaywrightManager(PlaywrightManager):
    """PlaywrightManager 的串流子類別。截圖成功時發送 SCREENSHOT_CAPTURED。"""

    def __init__(
        self,
        profile: BrowserProfile,
        telemetry: TimelineRecorder,
        *,
        outbox: OutboxWriter,
        task_id: str,
        experiment_id: str | None = None,
        screenshot_dir: Path | None = None,
        **kwargs: Any,
    ) -> None:
        init_kwargs = dict(kwargs)
        if screenshot_dir is not None:
            init_kwargs["screenshot_dir"] = screenshot_dir
        super().__init__(profile, telemetry, **init_kwargs)
        self._outbox = outbox
        self._task_id = task_id
        self._experiment_id = experiment_id

    async def capture_screenshot(
        self,
        sequence: int,
        state: str,
        page: Any | None = None,
        *,
        experiment_id: str | None = None,
    ) -> Path | None:
        dest = await super().capture_screenshot(
            sequence,
            state,
            page,
            experiment_id=experiment_id,
        )
        if dest is not None:
            url = f"/static/screenshots/{Path(dest).name}"
            exp_id = experiment_id or self._experiment_id
            self._outbox.publish(
                task_id=self._task_id,
                experiment_id=exp_id,
                type=ServerMessageType.SCREENSHOT_CAPTURED.value,
                payload={
                    "state": state,
                    "sequence": sequence,
                    "url": url,
                },
                ephemeral=False,
            )
        return dest
