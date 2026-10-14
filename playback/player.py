# Real time recording playback engine
from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, Signal

from recording.model import Recording

_TICK_INTERVAL_MS = 16


class Player(QObject):
    position_changed = Signal(float, float)
    progress_changed = Signal(int)
    playback_finished = Signal()
    playing_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self._recording: Recording | None = None
        self._elapsed_s = 0.0
        self._speed = 1.0
        self._loop = False
        self._playing = False
        self._last_tick_time: float | None = None

        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)

        self._skip_click_spikes = False
        self._filtered_points_cache = None

    def set_skip_click_spikes(self, skip: bool) -> None:
        self._skip_click_spikes = skip
        self._filtered_points_cache = None

    def set_tick_interval_ms(self, ms: int) -> None:
        ms = max(4, min(int(ms), 100))
        self._timer.setInterval(ms)

    def _points_for_playback(self) -> list:
        if not self._skip_click_spikes:
            return self._recording.points if self._recording else []
        if self._filtered_points_cache is None:
            pts = self._recording.points if self._recording else []
            filtered = [p for p in pts if not p.is_click_spike]
            self._filtered_points_cache = filtered if filtered else pts
        return self._filtered_points_cache

    def load(self, recording: Recording) -> None:
        self.stop()
        self._recording = recording
        self._filtered_points_cache = None
        self.progress_changed.emit(0)

    def has_recording(self) -> bool:
        return self._recording is not None and not self._recording.is_empty()

    @property
    def recording(self) -> Recording | None:
        return self._recording

    def position_ms(self) -> int:
        return int(self._elapsed_s * 1000)

    def duration_ms(self) -> int:
        return int((self._recording.duration_s if self._recording else 0.0) * 1000)

    def play(self) -> None:
        if not self.has_recording():
            return
        if self._elapsed_s >= self._recording.duration_s:
            self._elapsed_s = 0.0
        self._playing = True
        self._last_tick_time = time.monotonic()
        self._timer.start()
        self.playing_changed.emit(True)

    def pause(self) -> None:
        self._playing = False
        self._timer.stop()
        self.playing_changed.emit(False)

    def stop(self) -> None:
        self._playing = False
        self._timer.stop()
        self._elapsed_s = 0.0
        self.playing_changed.emit(False)
        self.progress_changed.emit(0)
        self._emit_position_at(0.0)

    def restart(self) -> None:
        self._elapsed_s = 0.0
        self._emit_position_at(0.0)
        self.progress_changed.emit(0)
        self.play()

    def seek_ms(self, ms: int) -> None:
        if self._recording is None:
            return
        self._elapsed_s = min(max(ms / 1000.0, 0.0), self._recording.duration_s)
        self._emit_position_at(self._elapsed_s)
        self.progress_changed.emit(int(self._elapsed_s * 1000))

    def set_loop(self, loop: bool) -> None:
        self._loop = loop

    def set_speed(self, speed: float) -> None:
        self._speed = max(speed, 0.01)

    def _tick(self) -> None:
        now = time.monotonic()
        dt = now - (self._last_tick_time or now)
        self._last_tick_time = now
        self._elapsed_s += dt * self._speed

        assert self._recording is not None
        if self._elapsed_s >= self._recording.duration_s:
            if self._loop:
                self._elapsed_s = 0.0
            else:
                self._elapsed_s = self._recording.duration_s
                self._emit_position_at(self._elapsed_s)
                self.progress_changed.emit(self.duration_ms())
                self.pause()
                self.playback_finished.emit()
                return

        self._emit_position_at(self._elapsed_s)
        self.progress_changed.emit(int(self._elapsed_s * 1000))

    def _emit_position_at(self, t_s: float) -> None:
        point = self._interpolate(t_s)
        if point is not None:
            self.position_changed.emit(point[0], point[1])

    def _interpolate(self, t_s: float) -> tuple[float, float] | None:
        pts = self._points_for_playback()
        if not pts:
            return None
        if t_s <= pts[0].t_s:
            return pts[0].x_mm, pts[0].y_mm
        if t_s >= pts[-1].t_s:
            return pts[-1].x_mm, pts[-1].y_mm

        lo, hi = 0, len(pts) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if pts[mid].t_s < t_s:
                lo = mid + 1
            else:
                hi = mid
        p1 = pts[lo]
        p0 = pts[lo - 1]
        span = max(p1.t_s - p0.t_s, 1e-6)
        f = (t_s - p0.t_s) / span
        return (p0.x_mm + f * (p1.x_mm - p0.x_mm), p0.y_mm + f * (p1.y_mm - p0.y_mm))
