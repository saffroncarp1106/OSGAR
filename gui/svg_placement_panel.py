# SVG placement and transform controls
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui import theme


class SvgPlacementPanel(QWidget):
    values_changed = Signal(float, float, float, float, float)
    lock_aspect_changed = Signal(bool)
    confirmed = Signal()
    cancelled = Signal()
    fit_to_tablet = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._updating = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        title = QLabel("Place SVG")
        title.setStyleSheet(theme.PANEL_TITLE_STYLE)
        layout.addWidget(title)

        hint = QLabel("Drag the box to move, corners to resize, top handle to rotate.")
        hint.setWordWrap(True)
        hint.setStyleSheet(theme.HINT_STYLE)
        layout.addWidget(hint)

        group = QGroupBox("Transform")
        form = QFormLayout(group)

        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(1.0, 5000.0)
        self.width_spin.setSuffix(" mm")

        self.height_spin = QDoubleSpinBox()
        self.height_spin.setRange(1.0, 5000.0)
        self.height_spin.setSuffix(" mm")

        self.lock_aspect = QCheckBox("Lock aspect ratio")
        self.lock_aspect.setChecked(True)

        self.rotation_spin = QDoubleSpinBox()
        self.rotation_spin.setRange(-3600.0, 3600.0)
        self.rotation_spin.setSuffix(" °")

        self.center_x_spin = QDoubleSpinBox()
        self.center_x_spin.setRange(-5000.0, 5000.0)
        self.center_x_spin.setSuffix(" mm")

        self.center_y_spin = QDoubleSpinBox()
        self.center_y_spin.setRange(-5000.0, 5000.0)
        self.center_y_spin.setSuffix(" mm")

        form.addRow("Width:", self.width_spin)
        form.addRow("Height:", self.height_spin)
        form.addRow("", self.lock_aspect)
        form.addRow("Rotation:", self.rotation_spin)
        form.addRow("Center X:", self.center_x_spin)
        form.addRow("Center Y:", self.center_y_spin)
        layout.addWidget(group)

        self.fit_tablet_btn = QPushButton("Fit to tablet area")
        self.fit_tablet_btn.setToolTip(
            "Snap this import to exactly the tablet area box.\n\n"
            "Sets width, height and centre to the box. If the import's aspect\n"
            "ratio differs from the tablet's, this stretches it to fill the box\n"
            "rather than leaving a margin - which is usually what you want for a\n"
            "replay, because the tablet was already mapped to the screen the same\n"
            "way. Hold the aspect lock instead if you would rather fit inside."
        )
        self.fit_tablet_btn.setVisible(False)
        layout.addWidget(self.fit_tablet_btn)

        btn_layout = QHBoxLayout()
        self.confirm_btn = QPushButton("Confirm Placement")
        self.cancel_btn = QPushButton("Cancel")
        btn_layout.addWidget(self.confirm_btn)
        btn_layout.addWidget(self.cancel_btn)
        layout.addLayout(btn_layout)
        layout.addStretch(1)

        self._aspect = 1.0
        self.width_spin.valueChanged.connect(self._on_width_changed)
        self.height_spin.valueChanged.connect(self._on_height_changed)
        self.rotation_spin.valueChanged.connect(self._emit_values)
        self.center_x_spin.valueChanged.connect(self._emit_values)
        self.center_y_spin.valueChanged.connect(self._emit_values)
        self.lock_aspect.toggled.connect(self.lock_aspect_changed.emit)
        self.confirm_btn.clicked.connect(self.confirmed.emit)
        self.cancel_btn.clicked.connect(self.cancelled.emit)
        self.fit_tablet_btn.clicked.connect(self.fit_to_tablet.emit)

    def set_values(self, width_mm: float, height_mm: float, rotation_deg: float, cx_mm: float, cy_mm: float) -> None:
        self._updating = True
        self.width_spin.setValue(width_mm)
        self.height_spin.setValue(height_mm)
        self.rotation_spin.setValue(rotation_deg)
        self.center_x_spin.setValue(cx_mm)
        self.center_y_spin.setValue(cy_mm)
        if height_mm:
            self._aspect = height_mm / max(width_mm, 1e-6)
        self._updating = False

    def _on_width_changed(self, w: float) -> None:
        if self._updating:
            return
        if self.lock_aspect.isChecked():
            self._updating = True
            self.height_spin.setValue(w * self._aspect)
            self._updating = False
        self._emit_values()

    def _on_height_changed(self, h: float) -> None:
        if self._updating:
            return
        if self.lock_aspect.isChecked() and self._aspect > 0:
            self._updating = True
            self.width_spin.setValue(h / self._aspect)
            self._updating = False
        self._emit_values()

    def _emit_values(self) -> None:
        if self._updating:
            return
        self.values_changed.emit(
            self.width_spin.value(), self.height_spin.value(), self.rotation_spin.value(),
            self.center_x_spin.value(), self.center_y_spin.value(),
        )
