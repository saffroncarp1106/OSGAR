# Line drawing tool options panel
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui import theme


class LineToolPanel(QWidget):
    line_mode_toggled = Signal(bool)
    duration_changed = Signal(float)
    speed_mode_toggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        title = QLabel("Line Tool")
        title.setStyleSheet(theme.PANEL_TITLE_STYLE)
        layout.addWidget(title)

        group = QGroupBox("Point-to-Point Line")
        form = QFormLayout(group)

        self.line_mode_btn = QPushButton("Place Line")
        self.line_mode_btn.setCheckable(True)
        form.addRow(self.line_mode_btn)

        hint = QLabel("Click a start point, then an end point, on the canvas.")
        hint.setWordWrap(True)
        hint.setStyleSheet(theme.HINT_STYLE)
        form.addRow(hint)

        self.speed_mode_check = QCheckBox("Use travel speed (speed‑based mode)")
        self.speed_mode_check.setChecked(False)
        self.speed_mode_check.toggled.connect(self._on_speed_mode_toggled)
        form.addRow(self.speed_mode_check)

        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(0.1, 600.0)
        self.duration_spin.setSuffix(" s")
        self.duration_spin.setValue(2.0)
        form.addRow("Playback duration:", self.duration_spin)

        duration_hint = QLabel(
            "In duration‑based mode: set the desired time; speed is computed.\n"
            "In speed‑based mode: the travel speed (from Export panel) is used, and duration is automatic."
        )
        duration_hint.setWordWrap(True)
        duration_hint.setStyleSheet(theme.HINT_STYLE)
        form.addRow(duration_hint)

        layout.addWidget(group)
        layout.addStretch(1)

        self.line_mode_btn.toggled.connect(self._on_toggled)
        self.duration_spin.valueChanged.connect(self.duration_changed.emit)

    def _on_toggled(self, checked: bool) -> None:
        self.line_mode_btn.setText("Placing Line... (click canvas)" if checked else "Place Line")
        self.line_mode_toggled.emit(checked)

    def _on_speed_mode_toggled(self, checked: bool) -> None:
        self.duration_spin.setEnabled(not checked)
        self.speed_mode_toggled.emit(checked)

    def set_duration_silently(self, seconds: float) -> None:
        blocked = self.duration_spin.blockSignals(True)
        self.duration_spin.setValue(max(seconds, self.duration_spin.minimum()))
        self.duration_spin.blockSignals(blocked)

    def uncheck_line_mode(self) -> None:
        blocked = self.line_mode_btn.blockSignals(True)
        self.line_mode_btn.setChecked(False)
        self.line_mode_btn.setText("Place Line")
        self.line_mode_btn.blockSignals(blocked)

    def is_speed_mode(self) -> bool:
        return self.speed_mode_check.isChecked()
