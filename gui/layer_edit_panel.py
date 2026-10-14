# Layer editing transform controls panel
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from gui import theme


class LayerEditPanel(QWidget):
    mode_changed = Signal(str)
    values_changed = Signal(float, float, float, float, float)
    lock_aspect_changed = Signal(bool)
    delete_selected = Signal()
    select_all = Signal()
    clear_selection = Signal()
    applied = Signal()
    cancelled = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._updating = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        self._title = QLabel("Editing layer")
        self._title.setStyleSheet(f"font-weight: 600; color: {theme.EDIT_BOX};")
        layout.addWidget(self._title)

        mode_row = QHBoxLayout()
        self.transform_radio = QRadioButton("Whole layer")
        self.transform_radio.setToolTip(
            "Drag the box to move it, the corners to resize, the green handle to rotate.\n"
            "Applies to every point at once."
        )
        self.points_radio = QRadioButton("Points")
        self.points_radio.setToolTip(
            "Click a point to select it, drag to move it.\n"
            "Shift-click to add to the selection, or drag on empty space to rubber-band.\n"
            "Delete removes the selected points; Ctrl+A selects all."
        )
        self.transform_radio.setChecked(True)
        mode_row.addWidget(self.transform_radio)
        mode_row.addWidget(self.points_radio)
        mode_row.addStretch(1)
        layout.addLayout(mode_row)

        form = QFormLayout()

        def _spin(lo, hi, step, suffix):
            s = QDoubleSpinBox()
            s.setRange(lo, hi)
            s.setDecimals(2)
            s.setSingleStep(step)
            s.setSuffix(suffix)
            s.valueChanged.connect(self._emit_values)
            return s

        self.width_spin = _spin(0.1, 10000, 1.0, " mm")
        self.height_spin = _spin(0.1, 10000, 1.0, " mm")
        self.rot_spin = _spin(-360, 360, 1.0, "°")
        self.cx_spin = _spin(-10000, 10000, 1.0, " mm")
        self.cy_spin = _spin(-10000, 10000, 1.0, " mm")
        form.addRow("Width:", self.width_spin)
        form.addRow("Height:", self.height_spin)
        form.addRow("Rotation:", self.rot_spin)
        form.addRow("Center X:", self.cx_spin)
        form.addRow("Center Y:", self.cy_spin)
        layout.addLayout(form)

        self.lock_aspect = QCheckBox("Lock aspect ratio")
        self.lock_aspect.setChecked(True)
        self.lock_aspect.toggled.connect(self.lock_aspect_changed)
        layout.addWidget(self.lock_aspect)

        self._point_row = QWidget()
        prow = QHBoxLayout(self._point_row)
        prow.setContentsMargins(0, 0, 0, 0)
        self.select_all_btn = QPushButton("Select all")
        self.clear_sel_btn = QPushButton("Deselect")
        self.delete_btn = QPushButton("Delete selected")
        self.delete_btn.setStyleSheet(f"color: {theme.DANGER_SOFT};")
        prow.addWidget(self.select_all_btn)
        prow.addWidget(self.clear_sel_btn)
        prow.addWidget(self.delete_btn)
        layout.addWidget(self._point_row)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(theme.HINT_STYLE)
        layout.addWidget(self._status)

        btn_row = QHBoxLayout()
        self.apply_btn = QPushButton("Apply")
        self.cancel_btn = QPushButton("Cancel")
        btn_row.addWidget(self.apply_btn)
        btn_row.addWidget(self.cancel_btn)
        layout.addLayout(btn_row)

        self.transform_radio.toggled.connect(self._on_mode)
        self.select_all_btn.clicked.connect(self.select_all)
        self.clear_sel_btn.clicked.connect(self.clear_selection)
        self.delete_btn.clicked.connect(self.delete_selected)
        self.apply_btn.clicked.connect(self.applied)
        self.cancel_btn.clicked.connect(self.cancelled)
        self._sync_mode_widgets()

    def set_layer_name(self, name: str) -> None:
        self._title.setText(f"Editing: {name}")

    def _on_mode(self) -> None:
        self._sync_mode_widgets()
        self.mode_changed.emit("transform" if self.transform_radio.isChecked() else "points")

    def _sync_mode_widgets(self) -> None:
        transform = self.transform_radio.isChecked()
        for w in (self.width_spin, self.height_spin, self.rot_spin,
                  self.cx_spin, self.cy_spin, self.lock_aspect):
            w.setEnabled(transform)
        self._point_row.setVisible(not transform)

    def set_values(self, w: float, h: float, rot: float, cx: float, cy: float) -> None:
        self._updating = True
        self.width_spin.setValue(w)
        self.height_spin.setValue(h)
        self.rot_spin.setValue(rot)
        self.cx_spin.setValue(cx)
        self.cy_spin.setValue(cy)
        self._updating = False

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    def _emit_values(self) -> None:
        if self._updating:
            return
        self.values_changed.emit(
            self.width_spin.value(), self.height_spin.value(), self.rot_spin.value(),
            self.cx_spin.value(), self.cy_spin.value(),
        )
