# G-code export settings and panel
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui import theme
from gui.collapsible_box import CollapsibleBox


class ExportPanel(QWidget):
    export_requested = Signal(float, float, float, bool, bool, bool)
    fit_to_speed_requested = Signal()
    scale_down_requested = Signal(float)
    locate_head_requested = Signal()
    calibrate_tablet_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        title = QLabel("Export G-code")
        title.setStyleSheet(theme.PANEL_TITLE_STYLE)
        layout.addWidget(title)

        settings_group = QGroupBox()
        form = QFormLayout(settings_group)

        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(1, 1000)
        self.speed_spin.setSuffix(" mm/s")
        self.speed_spin.setValue(150)
        form.addRow("Travel speed:", self.speed_spin)

        self.bed_temp_spin = QDoubleSpinBox()
        self.bed_temp_spin.setRange(0, 130)
        self.bed_temp_spin.setSuffix(" C")
        form.addRow("Bed temp (0 = skip):", self.bed_temp_spin)

        self.nozzle_temp_spin = QDoubleSpinBox()
        self.nozzle_temp_spin.setRange(0, 300)
        self.nozzle_temp_spin.setSuffix(" C")
        form.addRow("Nozzle temp (0 = skip):", self.nozzle_temp_spin)

        self.wait_checkbox = QCheckBox("Wait for temps before homing")
        form.addRow(self.wait_checkbox)

        self.exclude_spikes_checkbox = QCheckBox("Exclude flagged click spikes")
        self.exclude_spikes_checkbox.setToolTip(
            "Leaves flagged points out of the exported G-code entirely.\n"
            "Off by default — the export includes every raw recorded point unless you opt in here."
        )
        form.addRow(self.exclude_spikes_checkbox)

        self.sync_marks_checkbox = QCheckBox("Include key-bot sync marks")
        self.sync_marks_checkbox.setToolTip(
            "Interleaves SET_PIN sync ticks so the key bot can lock onto the\n"
            "printer's own motion clock.\n\n"
            "Needed if you print this file yourself rather than starting the run\n"
            "from the HID panel — without them the board never sees the preamble\n"
            "and never fires a key.\n\n"
            "Requires [output_pin osgar_sync] in printer.cfg. Leave it off for a\n"
            "file destined for any other machine: Klipper aborts the print on the\n"
            "first SET_PIN it does not recognise."
        )
        form.addRow(self.sync_marks_checkbox)

        self.simplify_spin = QDoubleSpinBox()
        self.simplify_spin.setRange(0.0, 1.0)
        self.simplify_spin.setSuffix(" mm")
        self.simplify_spin.setValue(0.0)
        self.simplify_spin.setDecimals(3)
        self.simplify_spin.setSingleStep(0.005)
        self.simplify_spin.setToolTip(
            "Path simplification tolerance, measured in SPACE-TIME:\n"
            "the value is a hard bound on how far the head can ever be\n"
            "from where the cursor actually was at that moment.\n"
            "Fewer G-code lines means less Klipper host drift.\n\n"
            "One microstep on this printer is 0.0125 mm — below that,\n"
            "the hardware cannot tell the difference.\n\n"
            "Figures below are for a ~52x31 mm tablet area:\n"
            "  0       = off\n"
            "  0.00625 = invisible, half a microstep (~0.3s saved)\n"
            "  0.0125  = lossless in hardware terms (~0.5s saved)\n"
            "  0.025   = 1-2 microsteps (~1.5s saved)\n"
            "  0.05    = 4 microsteps (~2.7s saved)\n\n"
            "Scale-dependent: this is an absolute bed distance, so a\n"
            "smaller tablet area needs a proportionally smaller value."
        )
        form.addRow("Path simplify:", self.simplify_spin)

        self.timing_checkbox = QCheckBox("Solve feed rates for 1:1 timing")
        self.timing_checkbox.setChecked(True)
        self.timing_checkbox.setToolTip(
            "Simulates your printer's actual Klipper motion planner and solves\n"
            "each segment's feed rate so the head arrives on the replay's clock.\n\n"
            "Commanding F = distance/time does NOT arrive on time: Klipper's\n"
            "corner limit is v^2 = 0.5 * d * tan(theta/2) * accel, which throttles\n"
            "the head at every one of the ~900 junctions osu!'s 17ms sampling\n"
            "creates. Left uncorrected that accumulates into ~0.5s of lateness.\n\n"
            "Uses max_accel and jerk (square_corner_velocity) from Settings —\n"
            "these MUST match your printer.cfg or the solve will be wrong.\n\n"
            "Pairs well with Path simplify: fewer, longer segments raise the\n"
            "corner speed limit, so 0.25-0.75mm helps timing as well as size.\n\n"
            "Takes a few seconds on long replays."
        )
        form.addRow(self.timing_checkbox)

        layout.addWidget(CollapsibleBox("Print Settings", settings_group, start_expanded=True))

        self.fit_speed_btn = QPushButton("Fit to printer speed")
        self.fit_speed_btn.setEnabled(False)
        self.fit_speed_btn.setToolTip(
            "Scale the replay down so the fastest segment exactly\n"
            "matches the printer's max speed. Shows you what %\n"
            "to set your tablet area to."
        )
        layout.addWidget(self.fit_speed_btn)

        scale_row = QHBoxLayout()
        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(1.0, 500.0)
        self.scale_spin.setSuffix("%")
        self.scale_spin.setValue(100.0)
        self.scale_spin.setDecimals(1)
        self.scale_spin.setSingleStep(5.0)
        self.scale_spin.setToolTip(
            "Scale the replay and tablet area by this percentage.\n"
            "< 100% shrinks (slower speeds, less accel overhead).\n"
            "> 100% grows (undo a previous shrink)."
        )
        scale_row.addWidget(QLabel("Scale down to:"))
        scale_row.addWidget(self.scale_spin)
        self.scale_down_btn = QPushButton("Apply")
        self.scale_down_btn.setEnabled(False)
        scale_row.addWidget(self.scale_down_btn)
        layout.addLayout(scale_row)

        self.speed_info_label = QLabel("")
        self.speed_info_label.setWordWrap(True)
        self.speed_info_label.setStyleSheet(theme.HINT_STYLE)
        layout.addWidget(self.speed_info_label)

        self.locate_head_btn = QPushButton("Show printer head")
        self.locate_head_btn.setToolTip(
            "Query the printer for its current X/Y position\n"
            "and show it as a green crosshair on the canvas.\n"
            "Useful for calibrating placement."
        )
        layout.addWidget(self.locate_head_btn)

        self.calibrate_btn = QPushButton("Calibrate tablet from printer")
        self.calibrate_btn.setToolTip(
            "Two-step: move the printer head to one corner of your\n"
            "tablet and click, then move to the opposite corner and\n"
            "click again. The tablet guide will be set from those points."
        )
        layout.addWidget(self.calibrate_btn)

        self.calibrate_label = QLabel("")
        self.calibrate_label.setWordWrap(True)
        self.calibrate_label.setStyleSheet(theme.HINT_STYLE)
        layout.addWidget(self.calibrate_label)

        self.export_btn = QPushButton("Export G-code...")
        self.export_btn.setEnabled(False)
        layout.addWidget(self.export_btn)

        self.timing_label = QLabel("")
        self.timing_label.setWordWrap(True)
        self.timing_label.setStyleSheet(theme.HINT_STYLE)
        layout.addWidget(self.timing_label)

        preview_group = QGroupBox()
        preview_layout = QVBoxLayout(preview_group)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("Exported G-code will be previewed here...")
        preview_layout.addWidget(self.preview)
        self._preview_box = CollapsibleBox("Last Export", preview_group, start_expanded=True)
        layout.addWidget(self._preview_box, 1)

        self.export_btn.clicked.connect(self._on_export_clicked)
        self.fit_speed_btn.clicked.connect(self.fit_to_speed_requested)
        self.scale_down_btn.clicked.connect(self._on_scale_down_clicked)
        self.locate_head_btn.clicked.connect(self.locate_head_requested)
        self.calibrate_btn.clicked.connect(self.calibrate_tablet_requested)

    def _on_export_clicked(self) -> None:
        self.export_requested.emit(
            self.speed_spin.value(),
            self.bed_temp_spin.value(),
            self.nozzle_temp_spin.value(),
            self.wait_checkbox.isChecked(),
            self.exclude_spikes_checkbox.isChecked(),
            self.sync_marks_checkbox.isChecked(),
        )

    def set_sync_marks(self, on: bool) -> None:
        self.sync_marks_checkbox.setChecked(on)

    def wants_sync_marks(self) -> bool:
        return self.sync_marks_checkbox.isChecked()

    def set_has_recording(self, has_recording: bool) -> None:
        self.export_btn.setEnabled(has_recording)

    def _on_scale_down_clicked(self) -> None:
        self.scale_down_requested.emit(self.scale_spin.value())

    def set_has_osr(self, has_osr: bool) -> None:
        self.fit_speed_btn.setEnabled(has_osr)
        self.scale_down_btn.setEnabled(has_osr)

    def set_speed_info(self, text: str) -> None:
        self.speed_info_label.setText(text)

    def show_preview(self, gcode_text: str) -> None:
        self.preview.setPlainText(gcode_text)
        self._preview_box.set_expanded(True)

    def current_speed(self) -> float:
        return self.speed_spin.value()

    def current_simplify_tolerance(self) -> float:
        return self.simplify_spin.value()

    def wants_timing_compensation(self) -> bool:
        return self.timing_checkbox.isChecked()

    def set_timing_info(self, text: str) -> None:
        self.timing_label.setText(text)
