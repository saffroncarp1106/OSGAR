# Position based playback clock synchronization
from __future__ import annotations

import time
from dataclasses import dataclass

from hardware.print_path import PrintPath
from utils.logging_setup import get_logger

logger = get_logger("hardware")

START_TRAVEL_MM = 0.75

SEARCH_HALF_WIDTH_S = 1.0

SEARCH_HALF_WIDTH_MAX_S = 8.0

MAX_CORRECTION_S = 0.30

REANCHOR_AFTER_CONSECUTIVE = 12


@dataclass
class Correction:

    accepted: bool
    measured_s: float = 0.0
    estimated_s: float = 0.0
    error_s: float = 0.0
    distance_mm: float = 0.0
    reason: str = ""


class PathClock:

    def __init__(
        self,
        path: PrintPath,
        gain: float = 0.25,
        reanchor_s: float = 0.25,
        tolerance_mm: float = 5.0,
    ) -> None:
        self._path = path
        self._gain = min(max(gain, 0.01), 1.0)
        self._reanchor_s = max(reanchor_s, 0.02)
        self._tolerance_mm = max(tolerance_mm, 0.01)

        self._anchor_path_s = 0.0
        self._anchor_host_s = 0.0
        self._running = False
        self._hint = 0
        self._half_width_s = SEARCH_HALF_WIDTH_S

        self._accepted = 0
        self._rejected = 0
        self._last_error_s = 0.0
        self._worst_error_s = 0.0
        self._streak = 0
        self._streak_sign = 0
        self._reanchors = 0


    def start(self, at_path_s: float = 0.0, host_now: float | None = None) -> None:
        self._anchor_path_s = at_path_s
        self._anchor_host_s = host_now if host_now is not None else time.monotonic()
        self._running = True
        self._hint = self._path.move_at_time(at_path_s)
        logger.info("Path clock started at path time %.3f s", at_path_s)

    def reanchor(self, at_path_s: float, host_now: float | None = None) -> None:
        self._anchor_path_s = at_path_s
        self._anchor_host_s = host_now if host_now is not None else time.monotonic()
        self._running = True
        self._hint = self._path.move_at_time(at_path_s, self._hint)

    def stop(self) -> None:
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def now(self, host_now: float | None = None) -> float:
        if not self._running:
            return self._anchor_path_s
        t = host_now if host_now is not None else time.monotonic()
        return self._anchor_path_s + (t - self._anchor_host_s)


    def looks_started(self, x: float, y: float) -> bool:
        if self._path.is_empty():
            return False
        first = self._path.moves[0]
        dist = ((x - first.x0) ** 2 + (y - first.y0) ** 2) ** 0.5
        return dist >= START_TRAVEL_MM


    def observe(
        self, x: float, y: float, latency_s: float, host_now: float | None = None
    ) -> Correction:
        if not self._running or self._path.is_empty():
            return Correction(False, reason="not running")

        now = host_now if host_now is not None else time.monotonic()
        estimated = self.now(now)

        centre = estimated - latency_s
        measured, distance, index = self._path.nearest_time(
            x, y, centre - self._half_width_s, centre + self._half_width_s
        )

        if distance > self._tolerance_mm:
            self._rejected += 1
            self._widen()
            return Correction(
                False, measured, estimated, 0.0, distance,
                f"{distance:.2f} mm off the path, over the {self._tolerance_mm:.2f} mm limit",
            )

        measured_now = measured + latency_s
        error = measured_now - estimated

        if abs(error) > MAX_CORRECTION_S:
            sign = 1 if error > 0 else -1
            self._streak = self._streak + 1 if sign == self._streak_sign else 1
            self._streak_sign = sign
            self._rejected += 1
            self._widen()
            if self._streak < REANCHOR_AFTER_CONSECUTIVE:
                return Correction(
                    False, measured, estimated, error, distance,
                    f"implies a {error:+.3f} s jump, beyond the "
                    f"{MAX_CORRECTION_S:.2f} s correction limit",
                )
            self._anchor_path_s = measured_now
            self._anchor_host_s = now
            self._hint = index
            self._streak = 0
            self._streak_sign = 0
            self._half_width_s = SEARCH_HALF_WIDTH_S
            self._reanchors += 1
            logger.warning(
                "Path clock re-anchored after %d consecutive samples agreeing it "
                "was %.0f ms out: %.3f s -> %.3f s",
                REANCHOR_AFTER_CONSECUTIVE, error * 1000.0, estimated, measured_now,
            )
            return Correction(True, measured, estimated, error, distance, "re-anchored")

        self._streak = 0
        self._streak_sign = 0
        self._hint = index
        self._half_width_s = SEARCH_HALF_WIDTH_S
        self._accepted += 1
        self._last_error_s = error
        self._worst_error_s = max(self._worst_error_s, abs(error))

        if abs(error) > self._reanchor_s:
            self._anchor_path_s = measured_now
        else:
            self._anchor_path_s = estimated + error * self._gain
        self._anchor_host_s = now

        return Correction(True, measured, estimated, error, distance)

    def _widen(self) -> None:
        self._half_width_s = min(self._half_width_s * 1.5, SEARCH_HALF_WIDTH_MAX_S)


    @property
    def last_error_ms(self) -> float:
        return self._last_error_s * 1000.0

    @property
    def worst_error_ms(self) -> float:
        return self._worst_error_s * 1000.0

    @property
    def accepted(self) -> int:
        return self._accepted

    @property
    def rejected(self) -> int:
        return self._rejected

    @property
    def reanchors(self) -> int:
        return self._reanchors

    def summary(self) -> str:
        total = self._accepted + self._rejected
        pct = 100.0 * self._accepted / total if total else 0.0
        extra = f", {self._reanchors} re-anchor(s)" if self._reanchors else ""
        return (
            f"{self._accepted}/{total} position sample(s) usable ({pct:.0f}%); "
            f"last correction {self.last_error_ms:+.0f} ms, "
            f"worst {self.worst_error_ms:.0f} ms{extra}"
        )
