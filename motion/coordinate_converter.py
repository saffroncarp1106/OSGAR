# Canvas to bed coordinate conversion
from __future__ import annotations

from dataclasses import dataclass

from config.settings import MotionSettings, PrinterSettings


@dataclass(frozen=True)
class PrinterPoint:
    x_mm: float
    y_mm: float


class CoordinateConverter:

    def __init__(self, printer: PrinterSettings, motion: MotionSettings) -> None:
        self._printer = printer
        self._motion = motion

    def update_settings(self, printer: PrinterSettings, motion: MotionSettings) -> None:
        self._printer = printer
        self._motion = motion

    def convert(self, nx: float, ny: float) -> PrinterPoint:
        usable_w = self._printer.bed_width_mm * self._motion.scale
        usable_h = self._printer.bed_height_mm * self._motion.scale

        x_mm = self._printer.origin_x_mm + nx * usable_w
        y_mm = self._printer.origin_y_mm + (1.0 - ny) * usable_h

        x_mm = min(max(x_mm, 0.0), self._printer.bed_width_mm)
        y_mm = min(max(y_mm, 0.0), self._printer.bed_height_mm)

        return PrinterPoint(x_mm=x_mm, y_mm=y_mm)

    def to_normalized(self, x_mm: float, y_mm: float) -> tuple[float, float]:
        usable_w = self._printer.bed_width_mm * self._motion.scale
        usable_h = self._printer.bed_height_mm * self._motion.scale

        nx = (x_mm - self._printer.origin_x_mm) / max(usable_w, 1e-6)
        ny = 1.0 - (y_mm - self._printer.origin_y_mm) / max(usable_h, 1e-6)

        nx = min(max(nx, 0.0), 1.0)
        ny = min(max(ny, 0.0), 1.0)
        return nx, ny
