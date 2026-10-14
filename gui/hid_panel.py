# HID key bot control panel
from __future__ import annotations


from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from config.settings import AppSettings
from gui import theme
from gui.collapsible_box import CollapsibleBox
from hardware import protocol as P
from hardware.hid_link import HidLink, HidLinkError, list_ports
from hardware.key_schedule import build_key_schedule
from hardware.moonraker_client import resolve_host
from hardware.run_controller import RunController, RunState
from storage.gcode_exporter import export_gcode
from recording.model import Recording
from utils.logging_setup import get_logger

logger = get_logger("gui")

_KEY_CHOICES = [
    (0x1D, "Z"), (0x1B, "X"), (0x06, "C"), (0x19, "V"),
    (0x04, "A"), (0x16, "S"), (0x07, "D"), (0x09, "F"),
    (0x2C, "Space"), (0x36, ","), (0x37, "."),
    (0xE1, "Left Shift"), (0xE0, "Left Ctrl"),
]

_WIRING = [
    ("D2", "Green LED — press emulation running", "via 470 Ω"),
    ("D3", "Red LED — armed", "via 470 Ω"),
    ("D4", "White LED — K1 click", "via 470 Ω"),
    ("D5", "White LED — K2 click", "via 470 Ω"),
    ("D6", "Button — reset", "to GND, internal pull-up"),
    ("D7", "Collector in — printer sync", "via 4N35 / PC817"),
]


class HidPanel(QWidget):

    status_message = Signal(str)
    place_trigger = Signal(float, float)

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings

        self.link = HidLink()
        self.controller = RunController(settings)

        self._recordings: list[Recording] = []
        self._speed_mm_s = settings.printer.max_speed_mm_s
        self._preview_on = False
        self._preview_seen = 0

        self._build_ui()
        self._wire()
        self._refresh_ports()
        self._update_enabled()

        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(200)
        self._ui_timer.timeout.connect(self._refresh_live)
        self._ui_timer.start()


    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        self._board_group = self._build_board_group()
        self._board_group.hide()
        layout.addWidget(self._board_group)

        layout.addWidget(CollapsibleBox("Preview", self._build_preview_group(), start_expanded=True))
        layout.addWidget(CollapsibleBox("Replay Playback", self._build_replay_group(), start_expanded=True))

        self._wiring_group = CollapsibleBox("Wiring", self._build_wiring_group(), start_expanded=False)
        self._wiring_group.hide()
        layout.addWidget(self._wiring_group)
        layout.addStretch(1)

    def _build_board_group(self) -> QWidget:
        group = QGroupBox("Board")
        box = QVBoxLayout(group)

        row = QHBoxLayout()
        self.port_box = QComboBox()
        self.port_box.setMinimumWidth(140)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setFixedWidth(70)
        row.addWidget(self.port_box, 1)
        row.addWidget(self.refresh_btn)
        box.addLayout(row)

        self.connect_btn = QPushButton("Connect")
        box.addWidget(self.connect_btn)

        box.addStretch()

        row2 = QHBoxLayout()
        self.tap1_btn = QPushButton("Tap K1")
        self.tap2_btn = QPushButton("Tap K2")
        row2.addWidget(self.tap1_btn)
        row2.addWidget(self.tap2_btn)
        box.addLayout(row2)

        self.board_label = QLabel("Not connected")
        self.board_label.setWordWrap(True)
        self.board_label.setStyleSheet(theme.HINT_STYLE)
        box.addWidget(self.board_label)
        return group

    def _build_preview_group(self) -> QWidget:
        inner = QWidget()
        box = QVBoxLayout(inner)
        box.setContentsMargins(0, 0, 0, 0)

        self.preview_btn = QPushButton("Enable buttons for preview")
        self.preview_btn.setCheckable(True)
        box.addWidget(self.preview_btn)

        self.preview_keys_box = QCheckBox("...and actually type into the desktop")
        box.addWidget(self.preview_keys_box)

        self.preview_label = QLabel("")
        self.preview_label.setWordWrap(True)
        box.addWidget(self.preview_label)

        self.led_label = QLabel("")
        self.led_label.setWordWrap(True)
        self.led_label.setStyleSheet(theme.HINT_STYLE)
        box.addWidget(self.led_label)
        return inner

    def _build_replay_group(self) -> QWidget:
        inner = QWidget()
        box = QVBoxLayout(inner)
        box.setContentsMargins(0, 0, 0, 0)

        form = QFormLayout()
        c = self.settings.communication
        self.host_edit = QLineEdit(c.host if c.port == 80 else f"{c.host}:{c.port}")
        self.host_edit.setPlaceholderText("192.168.0.137  (or host:port)")
        form.addRow("Printer host:", self.host_edit)

        h = self.settings.hid
        self.key1_box = QComboBox()
        self.key2_box = QComboBox()
        for usage, name in _KEY_CHOICES:
            self.key1_box.addItem(name, usage)
            self.key2_box.addItem(name, usage)
        self._select_usage(self.key1_box, h.key1_usage)
        self._select_usage(self.key2_box, h.key2_usage)
        form.addRow("K1 key:", self.key1_box)
        form.addRow("K2 key:", self.key2_box)

        self.trigger_delay_spin = QSpinBox()
        self.trigger_delay_spin.setRange(0, 10000)
        self.trigger_delay_spin.setValue(h.trigger_delay_ms)
        self.trigger_delay_spin.setSuffix(" ms")
        self.trigger_delay_spin.setToolTip(
            "Wait this long after the trigger point is reached\n"
            "before starting key playback. Compensates for\n"
            "travel/polling lag."
        )
        form.addRow("Trigger delay:", self.trigger_delay_spin)

        self.map_offset_spin = QSpinBox()
        self.map_offset_spin.setRange(-10000, 10000)
        self.map_offset_spin.setValue(h.map_offset_ms)
        self.map_offset_spin.setSuffix(" ms")
        self.map_offset_spin.setToolTip(
            "Shifts all key presses relative to the map start.\n"
            "Positive = keys fire earlier, negative = later."
        )
        form.addRow("Map offset:", self.map_offset_spin)
        box.addLayout(form)

        self.test_printer_btn = QPushButton("Check setup")
        box.addWidget(self.test_printer_btn)

        self.trigger_btn = QPushButton("Place trigger point")
        self.trigger_btn.setToolTip(
            "Place a marker on the canvas. When the printer head reaches\n"
            "this point, key playback begins. Drag to reposition."
        )
        self.trigger_label = QLabel("No trigger point set.")
        self.trigger_label.setStyleSheet(theme.HINT_STYLE)
        box.addWidget(self.trigger_btn)
        box.addWidget(self.trigger_label)

        self.schedule_label = QLabel("")
        self.schedule_label.setWordWrap(True)
        self.schedule_label.setStyleSheet(theme.HINT_STYLE)
        box.addWidget(self.schedule_label)

        self.start_btn = QPushButton("Start")
        self.start_btn.setToolTip(
            "Homes the printer, uploads G-code, starts the print,\n"
            "and begins key playback when the head reaches the trigger."
        )
        box.addWidget(self.start_btn)

        self.abort_btn = QPushButton("ABORT")
        self.abort_btn.setStyleSheet(
            f"QPushButton {{ background: {theme.DANGER}; color: {theme.ON_ACCENT}; "
            f"font-weight: 700; }}"
        )
        box.addWidget(self.abort_btn)

        self.run_progress = QProgressBar()
        self.run_progress.setRange(0, 1000)
        box.addWidget(self.run_progress)

        self.run_label = QLabel("Idle")
        self.run_label.setWordWrap(True)
        box.addWidget(self.run_label)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(100)
        box.addWidget(self.log_view)
        return inner

    def _build_wiring_group(self) -> QWidget:
        inner = QWidget()
        grid = QGridLayout(inner)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setVerticalSpacing(2)
        for r, (pin, what, note) in enumerate(_WIRING):
            lbl = QLabel(f"<b>{pin}</b>")
            lbl.setFixedWidth(34)
            grid.addWidget(lbl, r, 0)
            grid.addWidget(QLabel(what), r, 1)
            n = QLabel(note)
            n.setStyleSheet(theme.HINT_STYLE)
            grid.addWidget(n, r, 2)
        return inner

    @staticmethod
    def _select_usage(box: QComboBox, usage: int) -> None:
        i = box.findData(usage)
        if i >= 0:
            box.setCurrentIndex(i)


    def _wire(self) -> None:
        self.refresh_btn.clicked.connect(self._refresh_ports)
        self.connect_btn.clicked.connect(self._toggle_connect)
        self.tap1_btn.clicked.connect(lambda: self._tap(1))
        self.tap2_btn.clicked.connect(lambda: self._tap(2))
        self.key1_box.currentIndexChanged.connect(self._pull_settings)
        self.key2_box.currentIndexChanged.connect(self._pull_settings)
        self.trigger_delay_spin.valueChanged.connect(self._pull_settings)
        self.map_offset_spin.valueChanged.connect(self._pull_settings)
        self.host_edit.editingFinished.connect(self._pull_settings)

        self.preview_btn.toggled.connect(self._toggle_preview)
        self.preview_keys_box.toggled.connect(self._on_preview_keys_toggled)

        self.test_printer_btn.clicked.connect(self._test_printer)
        self.trigger_btn.clicked.connect(self._place_trigger)
        self.start_btn.clicked.connect(self._start)
        self.abort_btn.clicked.connect(self._abort)

        self.link.connected.connect(self._on_connected)
        self.link.disconnected.connect(self._on_disconnected)
        self.link.button_pressed.connect(self._on_play_button)
        self.link.board_error.connect(self._on_board_error)
        self.link.state_changed.connect(self._on_board_state)

        self.controller.state_changed.connect(lambda s: self._update_enabled())
        self.controller.message.connect(self._log)
        self.controller.progress.connect(self._on_progress)
        self.controller.failed.connect(self._on_failed)
        self.controller.finished.connect(self._on_finished)


    def set_project(self, recordings: list[Recording], speed_mm_s: float) -> None:
        self._recordings = [r for r in recordings if not r.is_empty()]
        self._speed_mm_s = speed_mm_s
        self._update_enabled()


    def _pull_settings(self) -> None:
        h = self.settings.hid
        h.key1_usage = self.key1_box.currentData()
        h.key2_usage = self.key2_box.currentData()
        h.trigger_delay_ms = self.trigger_delay_spin.value()
        h.map_offset_ms = self.map_offset_spin.value()

        c = self.settings.communication
        entered = self.host_edit.text().strip()
        if entered:
            if ":" in entered:
                host, _, port = entered.rpartition(":")
                c.host = host
                if port.isdigit():
                    c.port = int(port)
            else:
                c.host = entered

        self._update_enabled()
        if self.link.is_open:
            self.link.configure(key1=h.key1_usage, key2=h.key2_usage, lead=h.lead_ms)


    def _refresh_ports(self) -> None:
        self.port_box.clear()
        self.port_box.addItem("Auto-detect", "")
        try:
            for c in list_ports():
                self.port_box.addItem(c.label(), c.device)
        except HidLinkError as exc:
            self.board_label.setText(str(exc))
            self.board_label.setStyleSheet(f"color: {theme.WARN};")
            return
        saved = self.settings.hid.port
        if saved:
            i = self.port_box.findData(saved)
            if i >= 0:
                self.port_box.setCurrentIndex(i)

    def _toggle_connect(self) -> None:
        if self.link.is_open:
            self.link.close("disconnected from the panel")
            return
        port = self.port_box.currentData() or ""
        try:
            self.link.open(port)
        except HidLinkError as exc:
            self.board_label.setText(str(exc))
            self.board_label.setStyleSheet(f"color: {theme.DANGER};")
            self._log(f"Connect failed: {exc}")
            return
        self.settings.hid.port = port
        self._pull_settings()

    def _on_connected(self, ident: str) -> None:
        self.board_label.setText(ident)
        self.board_label.setStyleSheet(f"color: {theme.OK};")
        self.connect_btn.setText("Disconnect")
        self._log(f"Connected: {ident}")
        self._update_enabled()

    def _on_disconnected(self, reason: str) -> None:
        self._preview_on = False
        self.preview_btn.setChecked(False)
        self.board_label.setText(f"Not connected{f' — {reason}' if reason else ''}")
        self.board_label.setStyleSheet(theme.HINT_STYLE)
        self.connect_btn.setText("Connect")
        self._update_enabled()

    def _tap(self, key: int) -> None:
        if not self.link.is_open:
            return
        self.link.tap(key, 40)
        label = P.usage_label(
            self.settings.hid.key1_usage if key == 1 else self.settings.hid.key2_usage
        )
        self._log(f"Tapped K{key} ({label}) into the focused window.")


    def _on_board_error(self, detail: str) -> None:
        self._log(f"Board: {detail}")
        self.preview_label.setText(f"Board error: {detail}")
        self.preview_label.setStyleSheet(f"color: {theme.DANGER};")

    def _toggle_preview(self, on: bool) -> None:
        if not self.link.is_open:
            self.preview_btn.setChecked(False)
            self._log("Connect the board first.")
            return
        self._preview_on = on
        self._preview_seen = 0
        self.link.set_preview(self._preview_mode() if on else 0)
        if not on:
            self.link.set_leds(False, False)
        self.preview_label.setStyleSheet("")
        self.preview_label.setText(
            "Preview LEDs on — press Play and watch K1/K2 on the board."
            if on else "Preview LEDs off."
        )
        self._update_enabled()

    def _preview_mode(self) -> int:
        return 2 if self.preview_keys_box.isChecked() else 1

    def _on_preview_keys_toggled(self, on: bool) -> None:
        if self._preview_on and self.link.is_open:
            self.link.set_preview(self._preview_mode())

    def on_preview_keys(self, k1: bool, k2: bool) -> None:
        if not self._preview_on or not self.link.is_open:
            return
        self.link.set_leds(k1, k2)
        self._preview_seen += 1
        held = " ".join(n for n, v in (("K1", k1), ("K2", k2)) if v) or "none"
        self.preview_label.setText(f"Preview: {held} held  ({self._preview_seen} sent)")

    def _on_board_state(self, state) -> None:
        bits = [
            f"RED {'on' if state.armed else 'off'}",
            f"GREEN {'on' if state.running else 'off'}",
        ]
        if state.key_down.get(1) or state.key_down.get(2):
            held = " ".join(n for n, k in (("K1", 1), ("K2", 2)) if state.key_down.get(k))
            bits.append(f"holding {held}")
        if state.error:
            bits.append("ERROR")
        self.led_label.setText("   ".join(bits))


    def _test_printer(self) -> None:
        self._pull_settings()
        c = self.settings.communication
        problem = resolve_host(c.host, c.port)
        if problem:
            self._log(problem)
            return
        ok, detail = self.controller.check_printer()
        self._log(detail)

    def _place_trigger(self) -> None:
        bed_w = self.settings.printer.bed_width_mm
        bed_h = self.settings.printer.bed_height_mm
        h = self.settings.hid
        if h.trigger_x_mm == 0.0 and h.trigger_y_mm == 0.0:
            h.trigger_x_mm = bed_w / 2
            h.trigger_y_mm = bed_h / 2
        self.place_trigger.emit(h.trigger_x_mm, h.trigger_y_mm)
        self._update_trigger_label()
        self._log(f"Trigger at ({h.trigger_x_mm:.1f}, {h.trigger_y_mm:.1f}) mm — drag to move.")

    def on_trigger_moved(self, x_mm: float, y_mm: float) -> None:
        self.settings.hid.trigger_x_mm = x_mm
        self.settings.hid.trigger_y_mm = y_mm
        self._update_trigger_label()

    def _update_trigger_label(self) -> None:
        h = self.settings.hid
        if h.trigger_x_mm == 0.0 and h.trigger_y_mm == 0.0:
            self.trigger_label.setText("No trigger point set.")
        else:
            self.trigger_label.setText(
                f"Trigger: ({h.trigger_x_mm:.1f}, {h.trigger_y_mm:.1f}) mm"
            )

    def _prepare(self) -> None:
        self._pull_settings()
        if not self._recordings:
            QMessageBox.information(self, "Prepare", "There are no layers to run.")
            return

        h = self.settings.hid
        limit = self.settings.printer.max_speed_mm_s
        schedule = build_key_schedule(self._recordings, limit, 1.0, h, limit)

        trigger = None
        if h.trigger_x_mm != 0.0 or h.trigger_y_mm != 0.0:
            trigger = (h.trigger_x_mm, h.trigger_y_mm)
        gcode = export_gcode(
            self._recordings, self.settings.printer, limit,
            skip_home=True, trigger_xy=trigger,
        )
        notes = self.controller.prepare(schedule, gcode)

        self.schedule_label.setText(schedule.stats.summary())
        for note in notes:
            self._log(note)
        self._update_enabled()

    def _start(self) -> None:
        if self.controller.state is not RunState.READY:
            self._prepare()
            if self.controller.state is not RunState.READY:
                return
        try:
            self.controller.start()
        except RuntimeError as exc:
            QMessageBox.warning(self, "Start", str(exc))

    def _abort(self) -> None:
        self.controller.abort()

    def _on_play_button(self) -> None:
        if self.controller.is_active:
            self.controller.abort("reset from the board button")
        self._log("Board button pressed — reset.")


    def _refresh_live(self) -> None:
        if not self.link.is_open:
            return
        if self.controller.is_active or self._preview_on:
            self.link.heartbeat()
        self._status_tick = getattr(self, "_status_tick", 0) + 1
        if self._status_tick % 3 == 0:
            self.link.request_status()

    def _on_progress(self, report) -> None:
        self.run_progress.setValue(int(report.progress_pct * 10))
        bits = [
            f"{report.path_ms / 1000:.1f}s / {report.duration_ms / 1000:.1f}s",
            f"{report.events_sent}/{report.events_total} events",
            f"printer {report.printer_state}",
        ]
        self.run_label.setText("   ".join(bits))

    def _on_failed(self, why: str) -> None:
        self._log(f"FAILED: {why}")
        self.run_label.setText(why)
        self._update_enabled()

    def _on_finished(self, report) -> None:
        self.run_label.setText("Finished.")
        self.run_progress.setValue(1000)
        self._update_enabled()

    def _log(self, text: str) -> None:
        self.log_view.appendPlainText(text)
        self.status_message.emit(text)

    def _update_enabled(self) -> None:
        connected = self.link.is_open
        running = self.controller.is_active
        can_prepare = bool(self._recordings)
        ready = self.controller.state is RunState.READY

        self.connect_btn.setEnabled(not running)
        self.port_box.setEnabled(not connected and not running)
        self.refresh_btn.setEnabled(not connected and not running)
        self.tap1_btn.setEnabled(connected and not running)
        self.tap2_btn.setEnabled(connected and not running)
        self.key1_box.setEnabled(not running)
        self.key2_box.setEnabled(not running)
        self.preview_btn.setEnabled(connected and not running)
        self.host_edit.setEnabled(not running)
        self.test_printer_btn.setEnabled(not running)
        self.trigger_btn.setEnabled(not running)
        self.start_btn.setEnabled(can_prepare and not running)
        self.abort_btn.setEnabled(running)


    def shutdown(self) -> None:
        self._pull_settings()
        try:
            if self._preview_on and self.link.is_open:
                self.link.set_preview(0)
            if self.controller.is_active:
                self.controller.abort("window closed")
            if self.link.is_open:
                self.link.close("window closed")
        except Exception as exc:
            logger.warning("HID shutdown was not clean: %s", exc)
