# Cursor position readout indicator display
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget


class PositionIndicator(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._pos_label = QLabel("X: 0.00  Y: 0.00")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.addWidget(QLabel("Bed position:"))
        layout.addWidget(self._pos_label)
        layout.addStretch(1)

    def set_position(self, x_mm: float, y_mm: float) -> None:
        self._pos_label.setText(f"X: {x_mm:6.2f}  Y: {y_mm:6.2f}")
