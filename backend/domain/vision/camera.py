"""The webcam, shared by everything that wants to look.

One physical device, several consumers (motion watch, "what do you see?"),
so capture runs in one background thread that keeps only the latest frame.
Consumers `acquire`/`release` it; the device is opened on first acquire and
closed a little after the last release. That linger is what makes a second
"look" instant instead of paying the 1-2 s it takes Windows to open a camera.

The camera never runs unless something asked for it -- the light coming on
should always mean Jarvis is actually looking.

OpenCV is optional (`pip install opencv-python`); without it the camera
tools simply are not offered to the model.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time

import numpy as np

log = logging.getLogger("jarvis.camera")

GRAY_SIZE = (160, 120)  # what motion detection sees


def opencv_missing() -> str | None:
    try:
        import cv2  # noqa: F401
    except ImportError:
        return "opencv-python is not installed"
    return None


class Camera:
    def __init__(self, index: int = 0, width: int = 1280, height: int = 720,
                 linger_s: float = 15.0) -> None:
        self.index = index
        self.width = width
        self.height = height
        self.linger_s = linger_s

        self._owners: set[str] = set()
        # One long-lived thread that opens the device when someone wants it
        # and closes it after the linger. Starting and stopping threads per
        # use races: an acquire landing while the old thread is on its way
        # out would find it "alive" and get no camera at all.
        self._cond = threading.Condition()
        self._frame: np.ndarray | None = None
        self._frame_ts = 0.0
        self._thread: threading.Thread | None = None
        self._closed = False
        self._open = False
        self._released_at = 0.0
        self.error: str | None = None

    # -- ownership ---------------------------------------------------------

    def acquire(self, owner: str) -> None:
        with self._cond:
            self._owners.add(owner)
            self.error = None
            if self._thread is None:
                self._thread = threading.Thread(target=self._capture_loop,
                                                name="camera", daemon=True)
                self._thread.start()
            self._cond.notify_all()

    def release(self, owner: str) -> None:
        with self._cond:
            self._owners.discard(owner)
            if not self._owners:
                self._released_at = time.monotonic()

    @property
    def active(self) -> bool:
        """True while the device is actually open (the light is on)."""
        return self._open

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=2)

    # -- frames ------------------------------------------------------------

    def latest(self, max_age_s: float = 1.0) -> np.ndarray | None:
        """Most recent BGR frame, or None if there is no fresh one."""
        if self._frame is None or time.monotonic() - self._frame_ts > max_age_s:
            return None
        return self._frame

    def latest_gray(self) -> np.ndarray | None:
        import cv2

        frame = self.latest()
        if frame is None:
            return None
        small = cv2.resize(frame, GRAY_SIZE, interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(gray, (5, 5), 0)

    async def snapshot_jpeg(self, max_width: int = 768, quality: int = 80,
                            timeout_s: float = 5.0) -> bytes:
        """Grab one fresh frame as JPEG, opening the camera if needed."""
        import cv2

        self.acquire("snapshot")
        try:
            deadline = time.monotonic() + timeout_s
            # Freshly opened webcams deliver a few black or overexposed
            # frames while auto-exposure settles; wait past them.
            settle_until = time.monotonic() + 0.6
            while True:
                if self.error:
                    raise RuntimeError(self.error)
                frame = self.latest(max_age_s=0.5)
                if frame is not None and time.monotonic() >= settle_until:
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError("camera produced no frame")
                await asyncio.sleep(0.05)
        finally:
            self.release("snapshot")

        h, w = frame.shape[:2]
        if w > max_width:
            frame = cv2.resize(frame, (max_width, int(h * max_width / w)),
                               interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            raise RuntimeError("jpeg encode failed")
        return buf.tobytes()

    # -- capture thread ----------------------------------------------------

    def _wanted(self) -> bool:
        """Caller holds the lock."""
        if self._closed:
            return False
        return bool(self._owners) or (
            time.monotonic() - self._released_at < self.linger_s)

    def _capture_loop(self) -> None:
        while True:
            with self._cond:
                while not self._closed and not self._owners:
                    self._cond.wait()
                if self._closed:
                    return
            self._run_device()
            if self.error:
                # Do not spin retrying a missing camera; the next acquire
                # (a new "look", a restarted watch) will try again.
                with self._cond:
                    self._owners.clear()

    def _run_device(self) -> None:
        import cv2

        # DirectShow opens in ~1 s on Windows; the default MSMF backend can
        # take 5+ s and sometimes never returns a frame.
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(self.index, backend)
        if not cap.isOpened():
            self.error = f"could not open camera {self.index}"
            log.error(self.error)
            return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self._open = True
        log.info("camera %d opened", self.index)
        try:
            while True:
                with self._cond:
                    if not self._wanted():
                        break
                ok, frame = cap.read()
                if not ok:
                    self.error = "camera read failed"
                    log.warning(self.error)
                    break
                self._frame, self._frame_ts = frame, time.monotonic()
        finally:
            cap.release()
            self._open = False
            self._frame = None
            log.info("camera %d closed", self.index)
