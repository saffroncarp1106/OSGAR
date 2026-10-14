# Playback scrubber timeline control widget
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QWidget
from PySide6.QtCore import Qt


class Timeline(QWidget):
    position_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.setEnabled(False)

        self.time_label = QLabel("00:00 / 00:00")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.time_label)

        self.slider.valueChanged.connect(self.position_changed.emit)

    def set_duration_ms(self, duration_ms: int) -> None:
        self.slider.setRange(0, max(duration_ms, 0))
        self.slider.setEnabled(duration_ms > 0)
        self._update_label(self.slider.value(), duration_ms)

    def set_position_ms(self, position_ms: int) -> None:
        blocked = self.slider.blockSignals(True)
        self.slider.setValue(position_ms)
        self.slider.blockSignals(blocked)
        self._update_label(position_ms, self.slider.maximum())

    def _update_label(self, pos_ms: int, dur_ms: int) -> None:
        def fmt(ms: int) -> str:
            total_s = ms // 1000
            return f"{total_s // 60:02d}:{total_s % 60:02d}"

        self.time_label.setText(f"{fmt(pos_ms)} / {fmt(dur_ms)}")
