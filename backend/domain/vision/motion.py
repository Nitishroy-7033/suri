"""Motion detection by background subtraction, in plain numpy.

Frames arrive already small and grey (the camera does the resize), so this
is a few array ops per frame -- well under a millisecond at 160x120, cheap
enough to run all day on the CPU that is also doing wake word and VAD.

The detector keeps a slowly-updating background. A pixel counts as changed
when it differs from the background by more than `pixel_delta`; motion is
declared when enough of the frame has changed for several frames in a row.
The "several frames" rule is what ignores auto-exposure flicker and a single
noisy frame; the slow background is what lets a lamp being switched on fade
into the new normal instead of alarming forever.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from ...core.events import Event, EventBus

if TYPE_CHECKING:
    from .camera import Camera

log = logging.getLogger("jarvis.motion")


@dataclass
class MotionConfig:
    pixel_delta: float = 25.0  # grey levels (0-255) a pixel must move
    min_area: float = 0.02  # fraction of the frame that must change
    consecutive: int = 3  # frames in a row above min_area
    learn_rate: float = 0.05  # how fast the background adapts
    warmup_frames: int = 10  # webcams hunt exposure for the first second


class MotionDetector:
    def __init__(self, cfg: MotionConfig | None = None) -> None:
        self.cfg = cfg or MotionConfig()
        self.reset()

    def reset(self) -> None:
        self._bg: np.ndarray | None = None
        self._streak = 0
        self._frames = 0
        self.last_area = 0.0

    def update(self, gray: np.ndarray) -> bool:
        """Feed one uint8 greyscale frame. True on the frame motion is confirmed
        (and on every following frame while it continues)."""
        frame = gray.astype(np.float32)
        self._frames += 1
        if self._bg is None or self._bg.shape != frame.shape:
            self._bg = frame
            return False

        diff = np.abs(frame - self._bg)
        area = float((diff > self.cfg.pixel_delta).mean())
        self.last_area = area
        # Adapt everywhere, but more slowly where things are moving, so a
        # person standing still does not become "background" in two seconds.
        rate = np.where(diff > self.cfg.pixel_delta,
                        self.cfg.learn_rate * 0.25, self.cfg.learn_rate)
        self._bg += (frame - self._bg) * rate

        if self._frames <= self.cfg.warmup_frames:
            return False
        self._streak = self._streak + 1 if area >= self.cfg.min_area else 0
        return self._streak >= self.cfg.consecutive


class MotionWatcher:
    """Polls the camera and publishes a "motion" event, rate-limited."""

    def __init__(self, camera: "Camera", bus: EventBus, *, fps: float = 5.0,
                 cooldown_s: float = 30.0, cfg: MotionConfig | None = None) -> None:
        self.camera = camera
        self.bus = bus
        self.fps = fps
        self.cooldown_s = cooldown_s
        self.detector = MotionDetector(cfg)
        self._task: asyncio.Task | None = None
        self._last_alert = 0.0
        self.alerts = 0

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.running:
            return
        self.detector.reset()
        self.camera.acquire("motion")
        self._task = asyncio.create_task(self._run(), name="motion-watch")
        log.info("motion watch started (%.0f fps, cooldown %.0fs)",
                 self.fps, self.cooldown_s)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self.camera.release("motion")
        log.info("motion watch stopped")

    async def _run(self) -> None:
        period = 1.0 / self.fps
        while True:
            await asyncio.sleep(period)
            gray = self.camera.latest_gray()
            if gray is None:
                continue
            if not self.detector.update(gray):
                continue
            now = time.monotonic()
            if now - self._last_alert < self.cooldown_s:
                continue
            self._last_alert = now
            self.alerts += 1
            self.bus.publish(Event(
                kind="motion",
                say="The camera just detected movement. Tell the user briefly.",
                data={"area": round(self.detector.last_area, 3)},
            ))
