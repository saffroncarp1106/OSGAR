# Playback transport control button widget
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QPushButton, QWidget


class TransportControls(QWidget):
    play_clicked = Signal()
    pause_clicked = Signal()
    stop_clicked = Signal()
    restart_clicked = Signal()
    loop_toggled = Signal(bool)
    speed_changed = Signal(float)
    fade_trail_toggled = Signal(bool)
    fade_trail_duration_changed = Signal(float)
    hide_click_spikes_toggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.play_btn = QPushButton("Play")
        self.pause_btn = QPushButton("Pause")
        self.stop_btn = QPushButton("Stop")
        self.restart_btn = QPushButton("Restart")
        self.loop_btn = QPushButton("Loop")
        self.loop_btn.setCheckable(True)

        self.speed_box = QComboBox()
        self.speed_box.addItems(["0.25x", "0.5x", "1x", "1.5x", "2x"])
        self.speed_box.setCurrentText("1x")

        self.fade_trail_btn = QPushButton("Fade Trail")
        self.fade_trail_btn.setCheckable(True)
        self.fade_trail_btn.setToolTip(
            "Show only a fading recent trail while playing, instead of the full replay trace"
        )

        self.fade_trail_length_box = QComboBox()
        self._fade_lengths_s = [0.5, 1.0, 2.0, 4.0, 8.0]
        self.fade_trail_length_box.addItems(["0.5s", "1s", "2s", "4s", "8s"])
        self.fade_trail_length_box.setCurrentText("2s")
        self.fade_trail_length_box.setEnabled(False)
        self.fade_trail_length_box.setToolTip("How long the fading trail lasts")

        self.hide_spikes_btn = QPushButton("Hide Click Spikes")
        self.hide_spikes_btn.setCheckable(True)
        self.hide_spikes_btn.setToolTip(
            "Skip over points flagged as likely click-induced glitches when drawing/playing\n"
            "(the raw recorded data is never modified — this is display-only unless you also\n"
            "enable it for export in the Export panel)"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        for w in (self.restart_btn, self.play_btn, self.pause_btn, self.stop_btn, self.loop_btn):
            layout.addWidget(w)
        layout.addWidget(self.fade_trail_btn)
        layout.addWidget(self.fade_trail_length_box)
        layout.addWidget(self.hide_spikes_btn)
        layout.addStretch(1)
        layout.addWidget(self.speed_box)

        self._wire_signals()
        self.set_enabled_state(False)

    def _wire_signals(self) -> None:
        self.play_btn.clicked.connect(self.play_clicked.emit)
        self.pause_btn.clicked.connect(self.pause_clicked.emit)
        self.stop_btn.clicked.connect(self.stop_clicked.emit)
        self.restart_btn.clicked.connect(self.restart_clicked.emit)
        self.loop_btn.toggled.connect(self.loop_toggled.emit)
        self.speed_box.currentTextChanged.connect(self._on_speed_changed)
        self.fade_trail_btn.toggled.connect(self._on_fade_trail_toggled)
        self.fade_trail_length_box.currentIndexChanged.connect(self._on_fade_length_changed)
        self.hide_spikes_btn.toggled.connect(self.hide_click_spikes_toggled.emit)

    def _on_speed_changed(self, text: str) -> None:
        try:
            value = float(text.rstrip("x"))
        except ValueError:
            value = 1.0
        self.speed_changed.emit(value)

    def _on_fade_trail_toggled(self, checked: bool) -> None:
        self.fade_trail_length_box.setEnabled(checked)
        self.fade_trail_toggled.emit(checked)

    def _on_fade_length_changed(self, index: int) -> None:
        if 0 <= index < len(self._fade_lengths_s):
            self.fade_trail_duration_changed.emit(self._fade_lengths_s[index])

    def fade_trail_duration_s(self) -> float:
        idx = self.fade_trail_length_box.currentIndex()
        if 0 <= idx < len(self._fade_lengths_s):
            return self._fade_lengths_s[idx]
        return 2.0

    def set_enabled_state(self, enabled: bool) -> None:
        for w in (
            self.play_btn,
            self.pause_btn,
            self.stop_btn,
            self.restart_btn,
            self.loop_btn,
            self.speed_box,
            self.fade_trail_btn,
        ):
            w.setEnabled(enabled)
        self.hide_spikes_btn.setEnabled(enabled)
        self.fade_trail_length_box.setEnabled(enabled and self.fade_trail_btn.isChecked())
