# Mouse input recording and capture
from __future__ import annotations

import math
import time

from recording.model import Recording


class Recorder:
    def __init__(self) -> None:
        self._recording: Recording | None = None
        self._start_time: float | None = None
        self._last_point: tuple[float, float, float] | None = None

    @property
    def is_recording(self) -> bool:
        return self._recording is not None

    def start(self) -> None:
        self._recording = Recording()
        self._start_time = time.monotonic()
        self._last_point = None

    def add_point(self, x_mm: float, y_mm: float) -> float | None:
        if self._recording is None or self._start_time is None:
            return None
        t_s = time.monotonic() - self._start_time

        is_first_point = self._last_point is None
        velocity_mm_s = 0.0
        if self._last_point is not None:
            last_x, last_y, last_t = self._last_point
            dt = max(t_s - last_t, 1e-6)
            distance = math.hypot(x_mm - last_x, y_mm - last_y)
            velocity_mm_s = distance / dt

        self._recording.append(x_mm, y_mm, t_s, velocity_mm_s)
        self._last_point = (x_mm, y_mm, t_s)
        return None if is_first_point else velocity_mm_s

    def stop(self) -> Recording | None:
        recording = self._recording
        self._recording = None
        self._start_time = None
        self._last_point = None
        return recording
