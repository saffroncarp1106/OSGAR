# Motion smoothing and deadzone filtering
from __future__ import annotations

import math
import time

from config.settings import MotionSettings
from motion.coordinate_converter import PrinterPoint


class MotionFilter:
    def __init__(self, motion: MotionSettings) -> None:
        self._motion = motion
        self._last_output: PrinterPoint | None = None
        self._last_emit_time: float = 0.0

    def update_settings(self, motion: MotionSettings) -> None:
        self._motion = motion

    def reset(self) -> None:
        self._last_output = None
        self._last_emit_time = 0.0

    def process(self, point: PrinterPoint, now: float | None = None) -> PrinterPoint | None:
        now = now if now is not None else time.monotonic()

        min_interval = 1.0 / max(self._motion.update_rate_hz, 1e-6)
        if self._last_output is not None and (now - self._last_emit_time) < min_interval:
            return None

        if self._last_output is None:
            smoothed = point
        else:
            alpha = min(max(self._motion.smoothing_factor, 0.0), 1.0)
            smoothed = PrinterPoint(
                x_mm=self._last_output.x_mm + alpha * (point.x_mm - self._last_output.x_mm),
                y_mm=self._last_output.y_mm + alpha * (point.y_mm - self._last_output.y_mm),
            )

        if self._last_output is not None:
            dist = math.hypot(
                smoothed.x_mm - self._last_output.x_mm,
                smoothed.y_mm - self._last_output.y_mm,
            )
            if dist < self._motion.deadzone_px:
                return None

        self._last_output = smoothed
        self._last_emit_time = now
        return smoothed
