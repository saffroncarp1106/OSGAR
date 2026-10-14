# Replay click event list display
from __future__ import annotations

import bisect

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QListWidget, QPushButton, QVBoxLayout, QWidget

from gui import theme
from gui.key_overlay import KeyOverlay
from recording.model import Recording

_FLASH_S = 0.15

_KEY1_COLOUR = QColor(theme.KEY1)
_KEY2_COLOUR = QColor(theme.KEY2)


class OsrClicksPanel(QWidget):
    keys_active = Signal(bool, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        self._title = QLabel("Clicks (from replay)")
        self._title.setStyleSheet(theme.PANEL_TITLE_STYLE)
        layout.addWidget(self._title)

        self.overlay = KeyOverlay()
        layout.addWidget(self.overlay)

        self.test_keys_btn = QPushButton("Test Keys")
        self.test_keys_btn.setCheckable(True)
        self.test_keys_btn.setToolTip(
            "When on, K1/K2 type into the focused window\n"
            "as the timeline plays."
        )
        self.test_keys_btn.setEnabled(False)
        self.test_keys_btn.toggled.connect(self._on_test_toggled)
        layout.addWidget(self.test_keys_btn)

        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)

        self._key1_intervals: list[tuple[float, float]] = []
        self._key2_intervals: list[tuple[float, float]] = []
        self._last_active: tuple[bool, bool] = (False, False)
        self._key1_starts: list[float] = []
        self._key2_starts: list[float] = []
        self._vk1: int = 0
        self._vk2: int = 0


    def set_key_codes(self, vk1: int, vk2: int) -> None:
        self._vk1 = vk1
        self._vk2 = vk2

    def set_recording(self, recording: Recording | None) -> None:
        self.list_widget.clear()
        clicks = recording.click_points if recording is not None else []
        releases = recording.key_release_points if recording is not None else []
        has_clicks = bool(clicks)
        if not has_clicks and self.test_keys_btn.isChecked():
            self.test_keys_btn.setChecked(False)
        self.test_keys_btn.setEnabled(has_clicks)
        self._key1_intervals = self._build_intervals(clicks, releases, key=1)
        self._key2_intervals = self._build_intervals(clicks, releases, key=2)
        self._key1_starts = [iv[0] for iv in self._key1_intervals]
        self._key2_starts = [iv[0] for iv in self._key2_intervals]
        self._last_active = (False, False)
        self.overlay.set_keys([
            ("K1", self._key1_intervals, _KEY1_COLOUR),
            ("K2", self._key2_intervals, _KEY2_COLOUR),
        ])

        if not clicks:
            self._title.setText("Clicks (from replay)")
        else:
            self._title.setText(f"Clicks (from replay) — {len(clicks)}")
            ordered = sorted(clicks, key=lambda p: p.t_s)
            for i, p in enumerate(ordered, start=1):
                label = "K1" if p.key == 1 else ("K2" if p.key == 2 else "?")
                self.list_widget.addItem(
                    f"{i:>3}.  {p.t_s:7.3f}s  [{label}]  ({p.x_mm:.1f}, {p.y_mm:.1f}) mm"
                )

        self.update_time(0.0)

    def update_time(self, elapsed_s: float) -> None:
        self.overlay.update_time(elapsed_s)

        active = (self._is_held(1, elapsed_s), self._is_held(2, elapsed_s))
        if active != self._last_active:
            old = self._last_active
            self._last_active = active
            self.keys_active.emit(*active)
            if self.test_keys_btn.isChecked():
                self._fire_keys(old, active)

    def release_keys(self) -> None:
        if not self.test_keys_btn.isChecked():
            return
        from hardware.local_keys import release_key
        if self._vk1:
            release_key(self._vk1)
        if self._vk2:
            release_key(self._vk2)


    def _on_test_toggled(self, on: bool) -> None:
        if on and (self._last_active[0] or self._last_active[1]):
            self._fire_keys((False, False), self._last_active)
        elif not on:
            self.release_keys()

    def _fire_keys(self, old: tuple[bool, bool], new: tuple[bool, bool]) -> None:
        from hardware.local_keys import press_key, release_key
        if new[0] != old[0] and self._vk1:
            (press_key if new[0] else release_key)(self._vk1)
        if new[1] != old[1] and self._vk2:
            (press_key if new[1] else release_key)(self._vk2)

    def _is_held(self, key: int, t_s: float) -> bool:
        intervals = self._key1_intervals if key == 1 else self._key2_intervals
        starts = self._key1_starts if key == 1 else self._key2_starts
        if not intervals:
            return False
        i = bisect.bisect_right(starts, t_s) - 1
        return i >= 0 and t_s <= intervals[i][1]

    @staticmethod
    def _build_intervals(clicks, releases, key: int) -> list[tuple[float, float]]:
        press_times = sorted(p.t_s for p in clicks if p.key == key)
        release_times = sorted(p.t_s for p in releases if p.key == key)
        intervals: list[tuple[float, float]] = []
        ri = 0
        for pt in press_times:
            while ri < len(release_times) and release_times[ri] < pt:
                ri += 1
            if ri < len(release_times):
                intervals.append((pt, release_times[ri]))
                ri += 1
            else:
                intervals.append((pt, pt + _FLASH_S))
        return intervals
