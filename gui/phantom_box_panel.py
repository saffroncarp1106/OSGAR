# Tablet area guide control panel
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui import theme

TABLET_SIZES: list[tuple[float, float, str]] = [
    (101.6, 76.2, "XP-Pen G430 / G430S · Deco mini4"),
    (104.8, 65.4, "Huion 420 / osu!tablet"),
    (147.2, 92.0, "Wacom CTL-460 / CTL-470 / CTH-470 / CTE-450 (Bamboo)"),
    (152.0, 95.0, "Wacom Intuos S · CTL-471 / 472 / 480 / 490 / 4100 · CTH-480 / 490"),
    (157.5, 98.4, "Wacom Intuos4 S (PTK-440) · Intuos5 S (PTH-450) · Intuos Pro S (PTH-451)"),
    (160.0, 100.0, "XP-Pen G640 · Huion H640P / HS64 · VEIKK S640 · Wacom Intuos Pro S (PTH-460)"),
    (165.1, 101.6, "Gaomon S620 / S630"),
    (216.0, 135.0, "Wacom Intuos M · CTL-680 / CTH-680 / CTL-6100"),
    (224.0, 140.0, "Wacom Intuos Pro M 2013 (PTH-651)"),
    (224.0, 148.0, "Wacom Intuos Pro M 2017 (PTH-660)"),
    (254.0, 158.8, "XP-Pen Deco 01 V2 / Deco 02"),
    (294.0, 166.0, "Huion Kamvas 13"),
    (311.0, 216.0, "Wacom Intuos Pro L (PTH-860)"),
    (344.0, 194.0, "Wacom Cintiq 16 (DTK-1660)"),
]


def _preset_label(w: float, h: float, models: str) -> str:
    return f"{w:g} × {h:g} mm — {models}"


PRESETS: dict[str, tuple[float, float]] = {
    _preset_label(w, h, models): (w, h) for w, h, models in TABLET_SIZES
}


class PhantomBoxPanel(QWidget):
    enabled_changed = Signal(bool)
    editable_changed = Signal(bool)
    lock_aspect_changed = Signal(bool)
    values_changed = Signal(float, float, float, float)
    center_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._updating = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        self.enable_check = QCheckBox("Show tablet area")
        self.enable_check.setToolTip(
            "Draws a reference rectangle on the bed for your tablet's active area.\n"
            "It is only a guide - never exported, and it does not affect motion."
        )
        layout.addWidget(self.enable_check)

        self.preset_combo = QComboBox()
        self.preset_combo.addItem("Custom")
        for name in PRESETS:
            self.preset_combo.addItem(name)
        self.preset_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.preset_combo.view().setMinimumWidth(520)
        layout.addWidget(self.preset_combo)

        form = QFormLayout()

        def _spin(lo, hi, step):
            s = QDoubleSpinBox()
            s.setRange(lo, hi)
            s.setDecimals(1)
            s.setSingleStep(step)
            s.setSuffix(" mm")
            s.valueChanged.connect(self._emit_values)
            return s

        self.width_spin = _spin(1.0, 10000.0, 1.0)
        self.height_spin = _spin(1.0, 10000.0, 1.0)
        self.cx_spin = _spin(-10000.0, 10000.0, 1.0)
        self.cy_spin = _spin(-10000.0, 10000.0, 1.0)
        form.addRow("Width:", self.width_spin)
        form.addRow("Height:", self.height_spin)
        form.addRow("Center X:", self.cx_spin)
        form.addRow("Center Y:", self.cy_spin)
        layout.addLayout(form)

        self.lock_aspect = QCheckBox("Lock aspect ratio")
        self.lock_aspect.setChecked(True)
        layout.addWidget(self.lock_aspect)

        self.editable_check = QCheckBox("Adjust on canvas")
        self.editable_check.setToolTip(
            "Lets you drag the box and its corners directly on the bed.\n"
            "Off by default so it cannot intercept clicks meant for drawing."
        )
        layout.addWidget(self.editable_check)

        row = QHBoxLayout()
        self.center_btn = QPushButton("Center on bed")
        row.addWidget(self.center_btn)
        row.addStretch(1)
        layout.addLayout(row)

        self._note = QLabel(
            "Reference only — not exported and not part of any layer. "
            "Presets are grouped by active area: many models share one size, so "
            "every model listed on a line is the same box."
        )
        self._note.setWordWrap(True)
        self._note.setStyleSheet(theme.HINT_STYLE)
        layout.addWidget(self._note)

        self.enable_check.toggled.connect(self._on_enabled)
        self.editable_check.toggled.connect(self.editable_changed)
        self.lock_aspect.toggled.connect(self.lock_aspect_changed)
        self.center_btn.clicked.connect(self.center_requested)
        self.preset_combo.currentTextChanged.connect(self._on_preset)
        self._sync_enabled()

    def _on_enabled(self, on: bool) -> None:
        self._sync_enabled()
        self.enabled_changed.emit(on)

    def _sync_enabled(self) -> None:
        on = self.enable_check.isChecked()
        for w in (self.preset_combo, self.width_spin, self.height_spin,
                  self.cx_spin, self.cy_spin, self.lock_aspect,
                  self.editable_check, self.center_btn):
            w.setEnabled(on)

    def _on_preset(self, name: str) -> None:
        if self._updating or name not in PRESETS:
            return
        w, h = PRESETS[name]
        self._updating = True
        self.width_spin.setValue(w)
        self.height_spin.setValue(h)
        self._updating = False
        self._emit_values()

    def set_values(self, w: float, h: float, cx: float, cy: float) -> None:
        self._updating = True
        self.width_spin.setValue(w)
        self.height_spin.setValue(h)
        self.cx_spin.setValue(cx)
        self.cy_spin.setValue(cy)
        match = next((n for n, (pw, ph) in PRESETS.items()
                      if abs(pw - w) < 0.05 and abs(ph - h) < 0.05), "Custom")
        self.preset_combo.setCurrentText(match)
        self.preset_combo.setToolTip(
            match if match != "Custom" else "No standard tablet has this active area."
        )
        self._updating = False

    def set_state(self, enabled: bool, editable: bool, lock_aspect: bool) -> None:
        self._updating = True
        self.enable_check.setChecked(enabled)
        self.editable_check.setChecked(editable)
        self.lock_aspect.setChecked(lock_aspect)
        self._updating = False
        self._sync_enabled()

    def _emit_values(self) -> None:
        if self._updating:
            return
        self.values_changed.emit(
            self.width_spin.value(), self.height_spin.value(),
            self.cx_spin.value(), self.cy_spin.value(),
        )
