# Main application window and wiring
from __future__ import annotations

import bisect
import math
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QPointF
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from config.settings import AppSettings
from gui import theme
from gui.canvas import DrawingCanvas
from gui.collapsible_box import CollapsibleBox
from gui.color_palette import LAYER_COLORS
from gui.export_panel import ExportPanel
from gui.layer_edit_panel import LayerEditPanel
from gui.phantom_box_panel import PhantomBoxPanel
from gui.layer_list import LayerList
from gui.line_tool_panel import LineToolPanel
from gui.position_indicator import PositionIndicator
from gui.osr_clicks_panel import OsrClicksPanel
from gui.settings_dialog import SettingsDialog
from gui.hid_panel import HidPanel
from gui.svg_placement_panel import SvgPlacementPanel
from gui.timeline import Timeline
from gui.transport_controls import TransportControls
from gui.triangle_bar import TriangleMenuBar
from motion.coordinate_converter import CoordinateConverter
from motion.filters import MotionFilter
from motion.speed_fit import describe_fit
from playback.player import Player
from recording.model import Recording
from recording.recorder import Recorder
from storage.autosave import AUTOSAVE_PATH
from storage.gcode_exporter import GcodeExportError, analyze_replay_speeds, export_gcode
from storage.project import Project
from storage.recent_files import RecentFiles
from storage.serializer import (
    LEGACY_PROJECT_EXTENSION, PROJECT_FILE_EXTENSION, JsonProjectSerializer,
)
from storage.svg_importer import SvgDocument, SvgImportError, parse_svg, place_on_bed
from storage.osr_importer import (
    OsrImportError, OsrImportParams, parse_osr, place_osr_on_bed, playfield_to_bed_xy,
    PLAYFIELD_W, PLAYFIELD_H, SCREEN_W, SCREEN_H,
)
from utils.app_info import APP_NAME
from utils.logging_setup import get_logger

logger = get_logger("gui")


class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings | None = None) -> None:
        super().__init__()
        self.settings = settings or AppSettings.load()

        self.setWindowTitle(APP_NAME)
        self._apply_startup_geometry()

        self._converter = CoordinateConverter(self.settings.printer, self.settings.motion)
        self._motion_filter = MotionFilter(self.settings.motion)
        self._recorder = Recorder()
        self._player = Player()
        self._player.set_tick_interval_ms(self.settings.osr.playback_tick_ms)
        self._stroke_peak_velocity_mm_s = 0.0

        self._serializer = JsonProjectSerializer()
        self._recent_files = RecentFiles()
        self._current_project_path: Path | None = None

        self._recordings: list[Recording] = []
        self._layer_colors: list[QColor] = []
        self._selected_layer_index: int = 0
        self._play_all_active = False

        self._line_start: tuple[float, float] | None = None
        self._last_marker_mm: tuple[float, float] | None = None
        self._import_kind: str = "svg"
        self._pending_osr_doc = None
        self._last_osr: dict | None = None
        self._calibrate_corner1: tuple[float, float] | None = None
        self._editing_layer_index: int | None = None

        self._fade_trail_enabled = False
        self._fade_trail_duration_s = 2.0
        self._hide_click_spikes = False

        self._build_ui()
        self._apply_theme()
        self._apply_phantom_settings()
        self._maybe_offer_autosave_recovery()

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(60_000)
        self._autosave_timer.timeout.connect(self._autosave)
        self._autosave_timer.start()

        self._backdrop_timer = QTimer(self)
        self._backdrop_timer.setInterval(1000)
        self._backdrop_timer.timeout.connect(self._refresh_backdrop)
        self._backdrop_timer.start()

        self.canvas.clear_playback_marker()
        logger.info("Main window initialized")

    def _apply_startup_geometry(self) -> None:
        self.setMinimumSize(760, 520)

        screen = QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            margin_w, margin_h = 60, 80
            width = min(1200, max(avail.width() - margin_w, self.minimumWidth()))
            height = min(800, max(avail.height() - margin_h, self.minimumHeight()))
            self.resize(width, height)
            x = avail.x() + (avail.width() - width) // 2
            y = avail.y() + max((avail.height() - height) // 2, 0)
            self.move(x, y)
        else:
            self.resize(1000, 700)

    def _build_ui(self) -> None:
        menu_bar = TriangleMenuBar(self)
        self.setMenuBar(menu_bar)
        file_menu = menu_bar.addMenu("&File")

        new_action = file_menu.addAction("New Project")
        new_action.setShortcut("Ctrl+N")
        new_action.triggered.connect(self._new_project)

        open_action = file_menu.addAction("Open Project...")
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._open_project)

        save_action = file_menu.addAction("Save")
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self._save_project)

        save_as_action = file_menu.addAction("Save As...")
        save_as_action.setShortcut("Ctrl+Shift+S")
        save_as_action.triggered.connect(self._save_project_as)

        self._recent_menu = file_menu.addMenu("Recent Files")
        self._refresh_recent_menu()

        file_menu.addSeparator()
        import_svg_action = file_menu.addAction("Import SVG...")
        import_svg_action.triggered.connect(self._on_import_svg)
        import_osr_action = file_menu.addAction("Import OSR (.osr)...")
        import_osr_action.triggered.connect(self._on_import_osr)

        file_menu.addSeparator()
        settings_action = file_menu.addAction("Settings...")
        settings_action.triggered.connect(self._open_settings_dialog)

        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        top_bar = QHBoxLayout()
        self.position_indicator = PositionIndicator()
        top_bar.addWidget(self.position_indicator, 1)
        root_layout.addLayout(top_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(8)
        splitter.setChildrenCollapsible(False)
        self.canvas = DrawingCanvas()
        self.canvas.set_bed_size(self.settings.printer.bed_width_mm, self.settings.printer.bed_height_mm)
        h = self.settings.hid
        if h.trigger_x_mm != 0.0 or h.trigger_y_mm != 0.0:
            self.canvas.set_trigger_point(h.trigger_x_mm, h.trigger_y_mm)

        right_splitter = QSplitter(Qt.Orientation.Vertical)
        right_splitter.setHandleWidth(8)
        right_splitter.setChildrenCollapsible(True)

        self.line_tool_panel = LineToolPanel()
        right_splitter.addWidget(CollapsibleBox("Line Tool", self.line_tool_panel, start_expanded=True))

        self.svg_placement_panel = SvgPlacementPanel()
        self._svg_placement_box = CollapsibleBox("Place SVG", self.svg_placement_panel, start_expanded=True)
        self._svg_placement_box.setVisible(False)
        right_splitter.addWidget(self._svg_placement_box)

        self.layer_edit_panel = LayerEditPanel()
        self._layer_edit_box = CollapsibleBox("Edit Layer", self.layer_edit_panel, start_expanded=True)
        self._layer_edit_box.setVisible(False)
        right_splitter.addWidget(self._layer_edit_box)

        self.phantom_box_panel = PhantomBoxPanel()
        right_splitter.addWidget(
            CollapsibleBox("Tablet Area", self.phantom_box_panel, start_expanded=False)
        )

        self.osr_clicks_panel = OsrClicksPanel()
        right_splitter.addWidget(CollapsibleBox("Clicks (from replay)", self.osr_clicks_panel, start_expanded=True))

        self.layer_list = LayerList()
        self.export_panel = ExportPanel()
        self.export_panel.speed_spin.setValue(self.settings.printer.max_speed_mm_s)
        self.export_panel.set_sync_marks(False)
        right_splitter.addWidget(CollapsibleBox("Layers", self.layer_list, start_expanded=True))
        right_splitter.addWidget(CollapsibleBox("Tools / Export", self.export_panel, start_expanded=True))

        self.hid_panel = HidPanel(self.settings)
        right_splitter.addWidget(
            CollapsibleBox("HID Key Bot", self.hid_panel, start_expanded=False)
        )
        right_splitter.setStretchFactor(0, 0)
        right_splitter.setStretchFactor(1, 0)
        right_splitter.setStretchFactor(2, 1)
        right_splitter.setStretchFactor(3, 1)
        right_splitter.setStretchFactor(4, 2)

        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_scroll.setMinimumWidth(320)
        right_scroll.setWidget(right_splitter)

        splitter.addWidget(self.canvas)
        splitter.addWidget(right_scroll)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([740, 540])
        root_layout.addWidget(splitter, 1)

        self.timeline = Timeline()
        root_layout.addWidget(self.timeline)

        self.transport_controls = TransportControls()
        root_layout.addWidget(self.transport_controls)

        status_bar = QStatusBar()
        self.setStatusBar(status_bar)
        self._debug_label = QLabel("Ready")
        status_bar.addWidget(self._debug_label)
        self._speed_warning_label = QLabel("")
        self._speed_warning_label.setStyleSheet(f"color: {theme.DANGER}; font-weight: 600;")
        status_bar.addPermanentWidget(self._speed_warning_label)

        self.canvas.mouse_pressed.connect(self._on_canvas_pressed)
        self.canvas.mouse_released.connect(self._on_canvas_released)
        self.canvas.mouse_moved.connect(self._on_mouse_moved)
        self.canvas.resized.connect(self._on_canvas_resized)

        self.export_panel.speed_spin.valueChanged.connect(self._on_export_speed_changed)
        self.export_panel.export_requested.connect(self._on_export_requested)
        self.export_panel.fit_to_speed_requested.connect(self._on_fit_to_printer_speed)
        self.export_panel.scale_down_requested.connect(self._on_scale_down_tablet)
        self.export_panel.locate_head_requested.connect(self._on_locate_printer_head)
        self.export_panel.calibrate_tablet_requested.connect(self._on_calibrate_tablet)

        self.transport_controls.play_clicked.connect(self._on_play_clicked)
        self.transport_controls.pause_clicked.connect(self._on_pause_clicked)
        self.transport_controls.stop_clicked.connect(self._on_stop_clicked)
        self.transport_controls.restart_clicked.connect(self._on_restart_clicked)
        self.transport_controls.loop_toggled.connect(self._player.set_loop)
        self.transport_controls.speed_changed.connect(self._player.set_speed)
        self.transport_controls.fade_trail_toggled.connect(self._on_fade_trail_toggled)
        self.transport_controls.fade_trail_duration_changed.connect(self._on_fade_trail_duration_changed)
        self.transport_controls.hide_click_spikes_toggled.connect(self._on_hide_click_spikes_toggled)

        self.layer_list.layer_selected.connect(self._on_layer_selected)
        self.layer_list.play_selected.connect(self._on_play_selected_layer)
        self.layer_list.play_all.connect(self._on_play_all)
        self.layer_list.delete_selected.connect(self._on_delete_layer)
        self.layer_list.delete_all.connect(self._on_delete_all_layers)
        self.layer_list.color_changed.connect(self._on_layer_color_changed)
        self.layer_list.move_layer_up.connect(self._on_move_layer_up)
        self.layer_list.move_layer_down.connect(self._on_move_layer_down)
        self.layer_list.edit_selected.connect(self._on_edit_layer)

        self.layer_edit_panel.mode_changed.connect(self.canvas.set_edit_mode)
        self.layer_edit_panel.values_changed.connect(self._on_layer_edit_values)
        self.layer_edit_panel.lock_aspect_changed.connect(self.canvas.set_svg_lock_aspect)
        self.layer_edit_panel.select_all.connect(self.canvas.select_all_edit_points)
        self.layer_edit_panel.clear_selection.connect(self.canvas.clear_edit_selection)
        self.layer_edit_panel.delete_selected.connect(self._on_layer_edit_delete)
        self.layer_edit_panel.applied.connect(self._on_layer_edit_apply)
        self.layer_edit_panel.cancelled.connect(self._on_layer_edit_cancel)
        self.canvas.edit_changed.connect(self._on_canvas_edit_changed)

        self.phantom_box_panel.enabled_changed.connect(self._on_phantom_enabled)
        self.phantom_box_panel.editable_changed.connect(self._on_phantom_editable)
        self.phantom_box_panel.lock_aspect_changed.connect(self._on_phantom_lock_aspect)
        self.phantom_box_panel.values_changed.connect(self._on_phantom_values)
        self.phantom_box_panel.center_requested.connect(self._on_phantom_center)
        self.canvas.phantom_box_changed.connect(self._on_canvas_phantom_changed)

        self.timeline.position_changed.connect(self._player.seek_ms)
        self._player.progress_changed.connect(self.timeline.set_position_ms)
        self._player.progress_changed.connect(self._on_player_progress)
        self._player.position_changed.connect(self._on_playback_position)
        self._player.playback_finished.connect(self._on_playback_finished)

        self.line_tool_panel.line_mode_toggled.connect(self._on_line_mode_toggled)

        self.hid_panel.status_message.connect(self._debug_label.setText)
        self.osr_clicks_panel.keys_active.connect(self.hid_panel.on_preview_keys)
        self.hid_panel.key1_box.currentIndexChanged.connect(self._sync_test_key_codes)
        self.hid_panel.key2_box.currentIndexChanged.connect(self._sync_test_key_codes)
        self._sync_test_key_codes()

        self.hid_panel.place_trigger.connect(self.canvas.set_trigger_point)
        self.canvas.trigger_point_changed.connect(self._on_trigger_point_changed)
        self.canvas.trigger_point_changed.connect(self.hid_panel.on_trigger_moved)

        self.canvas.svg_transform_changed.connect(self._on_canvas_svg_transform_changed)
        self.svg_placement_panel.values_changed.connect(self._on_svg_panel_values_changed)
        self.svg_placement_panel.lock_aspect_changed.connect(self.canvas.set_svg_lock_aspect)
        self.svg_placement_panel.confirmed.connect(self._on_svg_placement_confirmed)
        self.svg_placement_panel.cancelled.connect(self._on_svg_placement_cancelled)
        self.svg_placement_panel.fit_to_tablet.connect(self._on_fit_to_tablet)

    def _apply_theme(self) -> None:
        if self.settings.ui.theme == "dark":
            font = QFont()
            font.setFamilies(["Segoe UI", "Inter", "Ubuntu", "Helvetica Neue", "Arial", "sans-serif"])
            font.setPointSize(10)
            QApplication.instance().setFont(font)

            self.setStyleSheet(theme.stylesheet())

    def closeEvent(self, event) -> None:
        self.hid_panel.shutdown()
        self.osr_clicks_panel.release_keys()
        self._autosave()
        self.settings.save()
        super().closeEvent(event)

    def _sync_test_key_codes(self, _=None) -> None:
        from hardware.local_keys import hid_to_vk
        h = self.settings.hid
        self.osr_clicks_panel.set_key_codes(
            hid_to_vk(h.key1_usage), hid_to_vk(h.key2_usage),
        )

    def _on_trigger_point_changed(self, x_mm: float, y_mm: float) -> None:
        self.settings.hid.trigger_x_mm = x_mm
        self.settings.hid.trigger_y_mm = y_mm

    def _on_line_mode_toggled(self, enabled: bool) -> None:
        mode = "line" if enabled else "freehand"
        self.canvas.set_drawing_mode(mode)
        if not enabled:
            self._line_start = None
        self._debug_label.setText(f"Mode: {mode.capitalize()}")

    def _on_export_speed_changed(self, speed_mm_s: float) -> None:
        self.settings.printer.max_speed_mm_s = speed_mm_s
        self._sync_hid_project()

    def _sync_hid_project(self) -> None:
        self.hid_panel.set_project(self._recordings, self.export_panel.current_speed())

    def _on_export_requested(
        self, speed_mm_s: float, bed_temp_c: float, nozzle_temp_c: float, wait_for_temps: bool,
        exclude_spikes: bool = False, sync_marks: bool = False,
    ) -> None:
        if not self._recordings:
            QMessageBox.warning(self, "Export G-code", "No layers to export.")
            return

        recordings_to_export = self._recordings
        if exclude_spikes:
            import copy
            recordings_to_export = []
            for rec in self._recordings:
                filtered = copy.copy(rec)
                filtered.points = [p for p in rec.points if not p.is_click_spike]
                recordings_to_export.append(filtered)

        timing_results: list = []
        want_timing = self.export_panel.wants_timing_compensation()
        if want_timing:
            QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            gcode_text = export_gcode(
                recordings_to_export,
                self.settings.printer,
                max_speed_mm_s=speed_mm_s,
                bed_temp_c=bed_temp_c,
                nozzle_temp_c=nozzle_temp_c,
                wait_for_temps=wait_for_temps,
                simplify_tolerance_mm=self.export_panel.current_simplify_tolerance(),
                timing_compensation=want_timing,
                timing_out=timing_results,
            )
        except GcodeExportError as exc:
            QMessageBox.warning(self, "Export G-code", str(exc))
            return
        finally:
            if want_timing:
                QApplication.restoreOverrideCursor()

        if timing_results:
            worst = max(timing_results, key=lambda r: r.rms_error_s)
            self.export_panel.set_timing_info(
                f"Timing solved against the Klipper planner: {worst.summary()}. "
                f"Predictions assume max_accel and jerk in Settings match your "
                f"printer.cfg."
            )
        elif want_timing:
            self.export_panel.set_timing_info(
                "Timing compensation skipped — needs max_accel set in Settings.")
        else:
            self.export_panel.set_timing_info("")

        filename, _ = QFileDialog.getSaveFileName(self, "Export G-code", "", "G-code (*.gcode *.g)")
        if not filename:
            return
        path = Path(filename)
        if path.suffix.lower() not in (".gcode", ".g"):
            path = path.with_suffix(".gcode")

        try:
            path.write_text(gcode_text, encoding="utf-8")
        except OSError as exc:
            logger.error("Failed to write G-code file %s: %s", path, exc)
            QMessageBox.critical(self, "Export G-code", f"Could not write file:\n{exc}")
            return

        self.export_panel.show_preview(gcode_text)
        self._debug_label.setText(f"Exported G-code to {path.name}")
        logger.info("Exported G-code to %s", path)

        self._update_speed_fit_info()

    def _update_speed_fit_info(self) -> None:
        info = self._last_osr
        if not info or info["rec"] not in self._recordings:
            self.export_panel.set_speed_info("")
            return
        rec = info["rec"]
        printer = self.settings.printer
        analysis = analyze_replay_speeds(
            [rec], printer.max_speed_mm_s, accel_mm_s2=printer.max_accel_mm_s2,
        )
        if not analysis:
            self.export_panel.set_speed_info("")
            return

        parts = [f"Peak: {analysis.peak_speed_mm_s:.0f} mm/s"]
        if analysis.fits:
            parts.append(f"within printer max ({analysis.printer_max_mm_s:.0f} mm/s)")
        else:
            lost_ms = analysis.capped_time_lost_s * 1000
            parts.append(
                f"{analysis.capped_segments}/{analysis.total_segments} segments "
                f"capped ({lost_ms:.0f}ms drift)"
            )
        if analysis.accel_overhead_s > 0.001:
            parts.append(
                f"~{analysis.accel_overhead_s*1000:.0f}ms accel overhead "
                f"({analysis.accel_affected_segments} segments)"
            )
        self.export_panel.set_speed_info("\n".join(parts))

    def _on_fit_to_printer_speed(self) -> None:
        info = self._last_osr
        if not info or info["rec"] not in self._recordings:
            QMessageBox.warning(self, "Fit to speed", "No OSR replay loaded.")
            return

        rec = info["rec"]
        analysis = analyze_replay_speeds([rec], self.settings.printer.max_speed_mm_s)
        if not analysis:
            return

        if analysis.fits:
            QMessageBox.information(
                self, "Fit to speed",
                f"Peak speed is {analysis.peak_speed_mm_s:.0f} mm/s — "
                f"already within printer max ({analysis.printer_max_mm_s:.0f} mm/s).\n"
                f"No scaling needed.",
            )
            return

        scale = analysis.scale_percent / 100.0
        old_w, old_h, rot, cx, cy = info["transform"]
        new_w = old_w * scale
        new_h = old_h * scale

        try:
            doc = parse_osr(info["path"], self._osr_params())
            fresh = place_osr_on_bed(doc, new_w, new_h, rot, cx, cy)
        except (OsrImportError, OSError) as exc:
            QMessageBox.warning(self, "Fit to speed", f"Could not re-place replay:\n{exc}")
            return
        if fresh.is_empty():
            return

        fresh.points = [p for p in fresh.points if not p.is_initial_outlier]
        if fresh.points:
            fresh.duration_s = fresh.points[-1].t_s

        target = rec
        idx = self._recordings.index(target)
        target.points = fresh.points
        target.click_points = fresh.click_points
        target.key_release_points = fresh.key_release_points
        target.duration_s = fresh.duration_s
        target.peak_velocity_mm_s = fresh.peak_velocity_mm_s

        info["transform"] = (new_w, new_h, rot, cx, cy)

        pb = self.settings.phantom_box
        old_tablet_w = pb.width_mm
        old_tablet_h = pb.height_mm
        pb.width_mm = old_tablet_w * scale
        pb.height_mm = old_tablet_h * scale
        self._apply_phantom_settings()

        self.layer_list.set_recordings(self._recordings, self._layer_colors)
        self.layer_list.list_widget.setCurrentRow(self._selected_layer_index)
        if idx == self._selected_layer_index:
            pos = self._player.position_ms()
            self._player.load(target)
            self.timeline.set_duration_ms(self._player.duration_ms())
            self._player.seek_ms(min(pos, self._player.duration_ms()))
            self.osr_clicks_panel.set_recording(target)
        self._update_canvas_layers()
        self._sync_hid_project()
        self._update_speed_fit_info()

        QMessageBox.information(
            self, "Fit to speed",
            f"Scaled to {analysis.scale_percent:.1f}%\n\n"
            f"Tablet area: {pb.width_mm:.1f} x {pb.height_mm:.1f} mm "
            f"(was {old_tablet_w:.1f} x {old_tablet_h:.1f} mm)\n"
            f"Replay: {new_w:.1f} x {new_h:.1f} mm\n\n"
            f"Set your tablet area in your tablet software to match\n"
            f"the new dimensions shown on the bed.",
        )
        self._debug_label.setText(
            f"Fitted to printer speed — tablet {pb.width_mm:.1f} x {pb.height_mm:.1f} mm "
            f"({analysis.scale_percent:.1f}%)"
        )

    def _on_scale_down_tablet(self, scale_percent: float) -> None:
        info = self._last_osr
        if not info or info["rec"] not in self._recordings:
            QMessageBox.warning(self, "Scale", "No OSR replay loaded.")
            return

        if abs(scale_percent - 100.0) < 0.05:
            QMessageBox.information(self, "Scale", "100% means no change.")
            return

        scale = scale_percent / 100.0
        rec = info["rec"]
        old_w, old_h, rot, cx, cy = info["transform"]
        new_w = old_w * scale
        new_h = old_h * scale

        try:
            doc = parse_osr(info["path"], self._osr_params())
            fresh = place_osr_on_bed(doc, new_w, new_h, rot, cx, cy)
        except (OsrImportError, OSError) as exc:
            QMessageBox.warning(self, "Scale", f"Could not re-place replay:\n{exc}")
            return
        if fresh.is_empty():
            return

        fresh.points = [p for p in fresh.points if not p.is_initial_outlier]
        if fresh.points:
            fresh.duration_s = fresh.points[-1].t_s

        target = rec
        idx = self._recordings.index(target)
        target.points = fresh.points
        target.click_points = fresh.click_points
        target.key_release_points = fresh.key_release_points
        target.duration_s = fresh.duration_s
        target.peak_velocity_mm_s = fresh.peak_velocity_mm_s

        info["transform"] = (new_w, new_h, rot, cx, cy)

        pb = self.settings.phantom_box
        old_tablet_w = pb.width_mm
        old_tablet_h = pb.height_mm
        pb.width_mm = old_tablet_w * scale
        pb.height_mm = old_tablet_h * scale
        self._apply_phantom_settings()

        self.layer_list.set_recordings(self._recordings, self._layer_colors)
        self.layer_list.list_widget.setCurrentRow(self._selected_layer_index)
        if idx == self._selected_layer_index:
            pos = self._player.position_ms()
            self._player.load(target)
            self.timeline.set_duration_ms(self._player.duration_ms())
            self._player.seek_ms(min(pos, self._player.duration_ms()))
            self.osr_clicks_panel.set_recording(target)
        self._update_canvas_layers()
        self._sync_hid_project()
        self._update_speed_fit_info()

        self.export_panel.scale_spin.setValue(100.0)

        QMessageBox.information(
            self, "Scale",
            f"Scaled to {scale_percent:.1f}%\n\n"
            f"Tablet area: {pb.width_mm:.1f} x {pb.height_mm:.1f} mm "
            f"(was {old_tablet_w:.1f} x {old_tablet_h:.1f} mm)\n"
            f"Replay: {new_w:.1f} x {new_h:.1f} mm\n\n"
            f"Set your tablet area in your tablet software to match\n"
            f"the new dimensions shown on the bed.",
        )
        self._debug_label.setText(
            f"Scaled to {scale_percent:.1f}% — tablet {pb.width_mm:.1f} x {pb.height_mm:.1f} mm"
        )

    def _on_locate_printer_head(self) -> None:
        from hardware.moonraker_client import MoonrakerClient, MoonrakerError
        comms = self.settings.communication
        try:
            client = MoonrakerClient(comms.host, comms.port, comms.api_key)
            status = client.status()
            client.close()
        except MoonrakerError as exc:
            QMessageBox.warning(self, "Printer head", f"Could not reach printer:\n{exc}")
            return
        self.canvas.set_printer_head(status.x_mm, status.y_mm)
        self._debug_label.setText(
            f"Printer head at X{status.x_mm:.1f} Y{status.y_mm:.1f} Z{status.z_mm:.1f}"
        )

    def _on_calibrate_tablet(self) -> None:
        from hardware.moonraker_client import MoonrakerClient, MoonrakerError
        comms = self.settings.communication
        try:
            client = MoonrakerClient(comms.host, comms.port, comms.api_key)
            status = client.status()
            client.close()
        except MoonrakerError as exc:
            QMessageBox.warning(self, "Calibrate tablet", f"Could not reach printer:\n{exc}")
            self._calibrate_corner1 = None
            self.export_panel.calibrate_btn.setText("Calibrate tablet from printer")
            self.export_panel.calibrate_label.setText("")
            return

        pos = (status.x_mm, status.y_mm)
        self.canvas.set_printer_head(pos[0], pos[1])

        if self._calibrate_corner1 is None:
            self._calibrate_corner1 = pos
            self.export_panel.calibrate_btn.setText("Set corner 2 (click again)")
            self.export_panel.calibrate_label.setText(
                f"Corner 1: X{pos[0]:.1f} Y{pos[1]:.1f}\n"
                f"Move printer head to the opposite corner, then click again."
            )
            self._debug_label.setText(
                f"Tablet corner 1 set at X{pos[0]:.1f} Y{pos[1]:.1f} — move to opposite corner"
            )
        else:
            c1 = self._calibrate_corner1
            c2 = pos
            self._calibrate_corner1 = None
            self.export_panel.calibrate_btn.setText("Calibrate tablet from printer")

            w = abs(c2[0] - c1[0])
            h = abs(c2[1] - c1[1])
            cx = (c1[0] + c2[0]) / 2.0
            cy = (c1[1] + c2[1]) / 2.0

            if w < 5.0 or h < 5.0:
                QMessageBox.warning(
                    self, "Calibrate tablet",
                    f"Points are too close together ({w:.1f} x {h:.1f} mm).\n"
                    f"Make sure you pick opposite corners.",
                )
                self.export_panel.calibrate_label.setText("")
                return

            pb = self.settings.phantom_box
            pb.enabled = True
            pb.width_mm = w
            pb.height_mm = h
            pb.center_x_mm = cx
            pb.center_y_mm = cy
            self._apply_phantom_settings()
            self._sync_fit_tablet_button()
            self.canvas.clear_printer_head()

            self.export_panel.calibrate_label.setText(
                f"Tablet set: {w:.1f} x {h:.1f} mm, center ({cx:.1f}, {cy:.1f})"
            )
            self._debug_label.setText(
                f"Tablet calibrated — {w:.1f} x {h:.1f} mm at ({cx:.1f}, {cy:.1f})"
            )

    def _build_project(self) -> Project:
        return Project(
            settings=self.settings,
            recordings=self._recordings,
            active_recording_index=self._selected_layer_index if self._recordings else None,
            playback_position_ms=self._player.position_ms(),
        )

    def _apply_project(self, project: Project) -> None:
        self.settings = project.settings
        self._converter.update_settings(self.settings.printer, self.settings.motion)
        self._motion_filter.update_settings(self.settings.motion)
        self.canvas.set_bed_size(self.settings.printer.bed_width_mm, self.settings.printer.bed_height_mm)
        self.canvas.clear()
        self.canvas.clear_playback_marker()

        self._player.stop()
        self._recordings = project.recordings
        self._layer_colors = []
        for i, rec in enumerate(self._recordings):
            color = LAYER_COLORS[i % len(LAYER_COLORS)]
            self._layer_colors.append(color)
        self.layer_list.set_recordings(self._recordings, self._layer_colors)

        if self._recordings:
            active_idx = project.active_recording_index
            if active_idx is None or active_idx >= len(self._recordings):
                active_idx = 0
            self._selected_layer_index = active_idx
            self.layer_list.list_widget.setCurrentRow(active_idx)
            self._load_recording_at(active_idx)
            self._player.seek_ms(project.playback_position_ms)
            self.transport_controls.set_enabled_state(True)
            self.export_panel.set_has_recording(True)
            self._update_canvas_layers()
        else:
            self._selected_layer_index = 0
            self._player.load(Recording())
            self.timeline.set_duration_ms(0)
            self.transport_controls.set_enabled_state(False)
            self.export_panel.set_has_recording(False)
            self.canvas.clear_layers()
            self._sync_hid_project()

    def _new_project(self) -> None:
        self._current_project_path = None
        self._recordings = []
        self._layer_colors = []
        self._last_osr = None
        self.layer_list.clear()
        self._player.load(Recording())
        self.timeline.set_duration_ms(0)
        self.transport_controls.set_enabled_state(False)
        self.export_panel.set_has_recording(False)
        self.export_panel.set_has_osr(False)
        self.export_panel.set_speed_info("")
        self.canvas.clear()
        self.canvas.clear_playback_marker()
        self.canvas.clear_layers()
        self._sync_hid_project()
        self.setWindowTitle(f"{APP_NAME} — Untitled")
        logger.info("New project started")

    def _open_project_path(self, path: Path) -> None:
        try:
            project = self._serializer.load(path)
        except (OSError, ValueError, KeyError) as exc:
            logger.error("Failed to open project %s: %s", path, exc)
            QMessageBox.critical(self, "Open Project", f"Could not open project:\n{exc}")
            return
        self._apply_project(project)
        self._current_project_path = path
        self.setWindowTitle(f"{APP_NAME} — {path.name}")
        self._recent_files.add(path)
        self._refresh_recent_menu()
        logger.info("Opened project %s", path)

    def _open_project(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Open Project",
            "",
            f"{APP_NAME} Project (*{PROJECT_FILE_EXTENSION} *{LEGACY_PROJECT_EXTENSION})",
        )
        if filename:
            self._open_project_path(Path(filename))

    def _save_project(self) -> None:
        if self._current_project_path is None:
            self._save_project_as()
            return
        self._write_project(self._current_project_path)

    def _save_project_as(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save Project As", "", f"{APP_NAME} Project (*{PROJECT_FILE_EXTENSION})"
        )
        if not filename:
            return
        path = Path(filename)
        if path.suffix != PROJECT_FILE_EXTENSION:
            path = path.with_suffix(PROJECT_FILE_EXTENSION)
        self._write_project(path)

    def _write_project(self, path: Path) -> None:
        try:
            self._serializer.save(self._build_project(), path)
        except OSError as exc:
            logger.error("Failed to save project %s: %s", path, exc)
            QMessageBox.critical(self, "Save Project", f"Could not save project:\n{exc}")
            return
        self._current_project_path = path
        self.setWindowTitle(f"{APP_NAME} — {path.name}")
        self._recent_files.add(path)
        self._refresh_recent_menu()
        logger.info("Saved project to %s", path)

    def _refresh_recent_menu(self) -> None:
        self._recent_menu.clear()
        recent = self._recent_files.get_all()
        if not recent:
            empty_action = self._recent_menu.addAction("(no recent files)")
            empty_action.setEnabled(False)
            return
        for path_str in recent:
            action = self._recent_menu.addAction(path_str)
            action.triggered.connect(lambda checked=False, p=path_str: self._open_project_path(Path(p)))

    def _autosave(self) -> None:
        if not self._recordings:
            return
        try:
            self._serializer.save(self._build_project(), AUTOSAVE_PATH)
            logger.debug("Autosaved session to %s", AUTOSAVE_PATH)
        except OSError as exc:
            logger.warning("Autosave failed: %s", exc)

    def _maybe_offer_autosave_recovery(self) -> None:
        if not AUTOSAVE_PATH.exists():
            return
        reply = QMessageBox.question(
            self,
            "Restore session?",
            "An autosaved session was found. Restore it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._open_project_path(AUTOSAVE_PATH)
            self._current_project_path = None
            self.setWindowTitle(f"{APP_NAME} — Untitled (recovered)")

    def _open_settings_dialog(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        dialog.settings_applied.connect(self._on_settings_applied)
        dialog.exec()

    def _on_settings_applied(self) -> None:
        self._converter.update_settings(self.settings.printer, self.settings.motion)
        self._motion_filter.update_settings(self.settings.motion)
        self.canvas.set_bed_size(self.settings.printer.bed_width_mm, self.settings.printer.bed_height_mm)
        self._apply_phantom_settings()
        self._player.set_tick_interval_ms(self.settings.osr.playback_tick_ms)
        self._retune_last_osr()
        self._update_speed_label()
        logger.info("Settings applied")

    def _retune_last_osr(self) -> None:
        info = self._last_osr
        if not info or info["rec"] not in self._recordings:
            return
        try:
            doc = parse_osr(info["path"], self._osr_params())
            fresh = place_osr_on_bed(doc, *info["transform"])
        except (OsrImportError, OSError) as exc:
            logger.warning("Could not re-tune replay: %s", exc)
            return
        if fresh.is_empty():
            return

        target = info["rec"]
        idx = self._recordings.index(target)
        target.points = fresh.points
        target.click_points = fresh.click_points
        target.key_release_points = fresh.key_release_points
        target.duration_s = fresh.duration_s
        target.peak_velocity_mm_s = fresh.peak_velocity_mm_s

        self.layer_list.set_recordings(self._recordings, self._layer_colors)
        self.layer_list.list_widget.setCurrentRow(self._selected_layer_index)
        if idx == self._selected_layer_index:
            pos = self._player.position_ms()
            self._player.load(target)
            self.timeline.set_duration_ms(self._player.duration_ms())
            self._player.seek_ms(min(pos, self._player.duration_ms()))
            self.osr_clicks_panel.set_recording(target)
        self._update_canvas_layers()
        logger.info("Re-tuned replay: %s", target.name)

    def _on_canvas_resized(self) -> None:
        self._update_canvas_layers()
        if self._last_marker_mm is not None:
            nx, ny = self._converter.to_normalized(*self._last_marker_mm)
            self.canvas.set_playback_position(nx, ny)

    def _refresh_backdrop(self) -> None:
        self._on_canvas_resized()
        self.canvas.update()

    def _on_canvas_pressed(self, nx: float, ny: float) -> None:
        if self.line_tool_panel.line_mode_btn.isChecked():
            if self._line_start is None:
                self._line_start = (nx, ny)
                self._debug_label.setText("Line: start set – click again to finish")
            else:
                x1, y1 = self._line_start
                x2, y2 = nx, ny
                dist_norm = math.hypot(x2 - x1, y2 - y1)
                if dist_norm < 0.001:
                    self._line_start = None
                    self._debug_label.setText("Line: too short – cancelled")
                    return

                start_mm = self._converter.convert(x1, y1)
                end_mm = self._converter.convert(x2, y2)
                dist_mm = math.hypot(end_mm.x_mm - start_mm.x_mm, end_mm.y_mm - start_mm.y_mm)

                user_limit = self.settings.printer.max_speed_mm_s
                printer_max = self.settings.printer.max_speed_mm_s

                print("===== LINE PLACEMENT DEBUG =====")
                print(f"Speed mode enabled? {self.line_tool_panel.is_speed_mode()}")
                print(f"User travel speed limit: {user_limit} mm/s")
                print(f"Printer max speed: {printer_max} mm/s")
                print(f"Distance: {dist_mm:.2f} mm")

                if self.line_tool_panel.is_speed_mode():
                    actual_speed = min(user_limit, printer_max)
                    actual_speed = max(actual_speed, 1.0)
                    actual_duration = dist_mm / actual_speed
                    cap_msg = ""
                    print(f"Speed-based mode: speed = {actual_speed} mm/s, duration = {actual_duration:.3f} s")
                else:
                    desired_duration = self.line_tool_panel.duration_spin.value()
                    desired_duration = max(desired_duration, 0.05)
                    required_speed = dist_mm / desired_duration
                    actual_speed = min(required_speed, printer_max, user_limit)
                    actual_speed = max(actual_speed, 1.0)
                    actual_duration = dist_mm / actual_speed

                    cap_reason = []
                    if actual_speed == printer_max and required_speed > printer_max:
                        cap_reason.append("printer max")
                    if actual_speed == user_limit and required_speed > user_limit:
                        cap_reason.append("travel speed")
                    cap_msg = f" (capped by {', '.join(cap_reason)})" if cap_reason else ""
                    print(f"Duration-based: desired duration = {desired_duration} s, required speed = {required_speed:.1f} mm/s")
                    print(f"Actual speed = {actual_speed} mm/s, actual duration = {actual_duration:.3f} s")

                num_points = max(2, int(dist_norm * 200))
                rec = Recording()
                rec.name = f"Line {len(self._recordings)+1}"
                dt = actual_duration / (num_points - 1) if num_points > 1 else 0.0
                t_s = 0.0
                for i in range(num_points):
                    frac = i / (num_points - 1)
                    nx_i = x1 + frac * (x2 - x1)
                    ny_i = y1 + frac * (y2 - y1)
                    rec.append(nx_i, ny_i, t_s, 0.0)
                    t_s += dt
                rec.duration_s = actual_duration

                self._add_recording(rec, normalized=True)
                self._line_start = None
                mode_label = "speed‑based" if self.line_tool_panel.is_speed_mode() else "duration‑based"
                status_msg = (
                    f"Line added ({mode_label}, duration {actual_duration:.2f}s, speed {actual_speed:.0f} mm/s)"
                    f" [travel limit={user_limit}, printer max={printer_max}]{cap_msg}"
                )
                self._debug_label.setText(status_msg)
                print(f"Status: {status_msg}")
                print("===================================")
        else:
            self._recorder.start()
            self._stroke_peak_velocity_mm_s = 0.0
            self._speed_warning_label.setText("")
            logger.debug("Freehand recording started")

    def _on_canvas_released(self, nx: float, ny: float) -> None:
        if self.line_tool_panel.line_mode_btn.isChecked():
            return

        recording = self._recorder.stop()
        if recording is None or recording.is_empty():
            self.canvas.clear()
            logger.debug("Freehand recording empty")
            return

        logger.info(f"Freehand recorded {len(recording.points)} points")
        self._add_recording(recording, normalized=False)
        self.canvas.clear()

    def _add_recording(self, recording: Recording, normalized: bool = False) -> None:
        if recording.is_empty():
            return

        new_rec = Recording()
        new_rec.name = recording.name if recording.name else f"Layer {len(self._recordings)+1}"

        for p in recording.points:
            if normalized:
                printer_pt = self._converter.convert(p.x_mm, p.y_mm)
                new_rec.append(printer_pt.x_mm, printer_pt.y_mm, p.t_s, p.velocity_mm_s)
            else:
                new_rec.append(p.x_mm, p.y_mm, p.t_s, p.velocity_mm_s)
            new_rec.points[-1].is_click_spike = p.is_click_spike
            new_rec.points[-1].is_synthetic = p.is_synthetic

        new_rec.peak_velocity_mm_s = recording.peak_velocity_mm_s
        new_rec.duration_s = recording.duration_s
        new_rec.click_points = list(recording.click_points)
        new_rec.key_release_points = list(recording.key_release_points)

        color = LAYER_COLORS[len(self._recordings) % len(LAYER_COLORS)]
        self._recordings.append(new_rec)
        self._layer_colors.append(color)
        self.layer_list.add_recording(new_rec, color)

        self._selected_layer_index = len(self._recordings) - 1
        self._load_recording_at(self._selected_layer_index)
        self._update_canvas_layers()

        self.transport_controls.set_enabled_state(True)
        self.export_panel.set_has_recording(True)

        logger.info("Added layer: %s (%d points)", new_rec.name, len(new_rec.points))

        self._update_speed_label()

    def _update_speed_label(self) -> None:
        if not self._recordings:
            self._speed_warning_label.setText("")
            return

        limit = min(self.settings.printer.max_speed_mm_s,
                    self.settings.printer.max_speed_mm_s)
        fit = describe_fit(self._recordings, limit)

        if fit["stretch"] <= 1.0:
            self._speed_warning_label.setStyleSheet(f"color: {theme.OK}; font-weight: 600;")
            self._speed_warning_label.setText(
                f"Prints at true recorded speed — {fit['duration_s']:.0f} s"
            )
            return

        msg = (
            f"Print ≈ {fit['print_s'] / 60:.1f} min for {fit['duration_s']:.0f} s of motion "
            f"({fit['stretch']:.1f}× slower, timing kept in proportion). "
            f"Resize to {fit['size_scale'] * 100:.0f}% for a real-time print."
        )
        style = f"color: {theme.WARN}; font-weight: 600;"
        if fit["capped"]:
            msg += " Path is faster than the stretch ceiling; some moves will lag."
            style = f"color: {theme.DANGER}; font-weight: 600;"
        self._speed_warning_label.setStyleSheet(style)
        self._speed_warning_label.setText(msg)

    def _on_import_svg(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Import SVG", "", "SVG files (*.svg)")
        if not filename:
            return
        try:
            doc = parse_svg(filename)
        except SvgImportError as exc:
            QMessageBox.warning(self, "Import SVG", str(exc))
            return

        bed_w = self.settings.printer.bed_width_mm
        bed_h = self.settings.printer.bed_height_mm
        aspect = doc.height / doc.width if doc.width else 1.0
        fit_w = bed_w * 0.7
        fit_h = fit_w * aspect
        if fit_h > bed_h * 0.7:
            fit_h = bed_h * 0.7
            fit_w = fit_h / aspect if aspect else fit_h

        self._svg_import_filename = Path(filename).name
        self._import_kind = "svg"
        self.canvas.start_svg_preview(doc.subpaths, fit_w, fit_h, 0.0, bed_w / 2, bed_h / 2)
        self.svg_placement_panel.set_values(fit_w, fit_h, 0.0, bed_w / 2, bed_h / 2)
        self._svg_placement_box.setVisible(True)
        self.svg_placement_panel.fit_tablet_btn.setText("Fit to tablet area")
        self._sync_fit_tablet_button()
        self._debug_label.setText(f"Placing {self._svg_import_filename} — drag to position, resize, rotate")

    def _osr_params(self) -> OsrImportParams:
        o = self.settings.osr
        return OsrImportParams(
            osu_faithful=o.osu_faithful,
            align_to_map_start=o.align_to_map_start,
            retime_clicks=o.retime_clicks,
            recover_gap_time=o.recover_gap_time,
            gap_speed_pct=o.gap_speed_pct,
            gap_detect_ratio=o.gap_detect_ratio,
        )

    def _on_import_osr(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Import OSR", "", "osu! replay files (*.osr)")
        if not filename:
            return
        try:
            doc = parse_osr(filename, self._osr_params())
        except OsrImportError as exc:
            QMessageBox.warning(self, "Import OSR", str(exc))
            return
        self._pending_osr_path = filename

        bed_w = self.settings.printer.bed_width_mm
        bed_h = self.settings.printer.bed_height_mm
        xs = [p[1] for p in doc.points]
        ys = [p[2] for p in doc.points]
        src_w = max(max(xs) - min(xs), 1e-6)
        src_h = max(max(ys) - min(ys), 1e-6)
        aspect = src_h / src_w
        fit_w = bed_w * 0.7
        fit_h = fit_w * aspect
        if fit_h > bed_h * 0.7:
            fit_h = bed_h * 0.7
            fit_w = fit_h / aspect if aspect else fit_h

        self._svg_import_filename = Path(filename).name
        self._import_kind = "osr"
        self._pending_osr_doc = doc
        subpaths = [[playfield_to_bed_xy(x, y) for _, x, y in doc.points]]
        self.canvas.start_svg_preview(subpaths, fit_w, fit_h, 0.0, bed_w / 2, bed_h / 2)
        self.svg_placement_panel.set_values(fit_w, fit_h, 0.0, bed_w / 2, bed_h / 2)
        self._svg_placement_box.setVisible(True)
        self.svg_placement_panel.fit_tablet_btn.setText("Fit to screen (16:9)")
        self._sync_fit_tablet_button()
        self._debug_label.setText(f"Placing {self._svg_import_filename} — drag to position, resize, rotate")

    def _on_canvas_svg_transform_changed(self) -> None:
        transform = self.canvas.get_svg_transform()
        if transform is not None:
            self.svg_placement_panel.set_values(*transform)

    def _on_svg_panel_values_changed(self, w: float, h: float, rot: float, cx: float, cy: float) -> None:
        self.canvas.update_svg_transform(w, h, rot, cx, cy)

    def _on_svg_placement_cancelled(self) -> None:
        self.canvas.clear_svg_preview()
        self._svg_placement_box.setVisible(False)
        self._pending_osr_doc = None
        self._debug_label.setText("Import cancelled")

    def _on_svg_placement_confirmed(self) -> None:
        transform = self.canvas.get_svg_transform()
        subpaths = self.canvas.get_svg_preview_subpaths()
        if transform is None or subpaths is None:
            return
        width_mm, height_mm, rotation_deg, center_x, center_y = transform
        name = getattr(self, "_svg_import_filename", "import")

        if self._import_kind == "osr":
            if self._pending_osr_doc is None:
                return
            rec = place_osr_on_bed(self._pending_osr_doc, width_mm, height_mm, rotation_deg, center_x, center_y)
            self.canvas.clear_svg_preview()
            self._svg_placement_box.setVisible(False)
            self._pending_osr_doc = None
            if rec.is_empty():
                QMessageBox.warning(self, "Import OSR", "No usable cursor data in this replay.")
                return
            self._add_recording(rec, normalized=False)
            path = getattr(self, "_pending_osr_path", None)
            if path:
                self._last_osr = {
                    "path": path,
                    "transform": (width_mm, height_mm, rotation_deg, center_x, center_y),
                    "rec": self._recordings[-1],
                }
            outlier_count = sum(1 for p in rec.points if p.is_initial_outlier)
            if outlier_count:
                rec.points = [p for p in rec.points if not p.is_initial_outlier]
                if rec.points:
                    rec.duration_s = rec.points[-1].t_s
                logger.info("Auto-deleted %d initial outlier point(s)", outlier_count)
            spike_count = sum(1 for p in rec.points if p.is_click_spike)
            parts = [f"Imported replay path from {name} ({len(rec.points)} points)"]
            if outlier_count:
                parts.append(f"removed {outlier_count} initial outlier(s)")
            if spike_count:
                parts.append(f"{spike_count} click-glitch point(s)")
            self._debug_label.setText(" — ".join(parts))
            logger.info(
                "Imported OSR %s (%d points, %d click spikes, %d initial outliers removed)",
                name, len(rec.points), spike_count, outlier_count,
            )
            self.export_panel.set_has_osr(True)
            self._update_speed_fit_info()
            return

        doc = SvgDocument(subpaths=subpaths, width=width_mm, height=height_mm)
        travel_speed = min(self.settings.printer.max_speed_mm_s, self.settings.printer.max_speed_mm_s)
        new_recordings = place_on_bed(doc, width_mm, height_mm, rotation_deg, center_x, center_y, travel_speed)

        self.canvas.clear_svg_preview()
        self._svg_placement_box.setVisible(False)

        if not new_recordings:
            QMessageBox.warning(self, "Import SVG", "No usable paths found in this SVG.")
            return

        for rec in new_recordings:
            self._add_recording(rec, normalized=False)
        self._debug_label.setText(f"Imported {len(new_recordings)} path(s) from {name}")
        logger.info("Imported SVG %s (%d paths)", name, len(new_recordings))

    def _update_canvas_layers(self) -> None:
        rect = self.canvas._print_area_rect()
        layer_paths = []
        for rec in self._recordings:
            pts = []
            for p in rec.points:
                if self._hide_click_spikes and p.is_click_spike:
                    continue
                nx, ny = self._converter.to_normalized(p.x_mm, p.y_mm)
                px = rect.x() + nx * rect.width()
                py = rect.y() + ny * rect.height()
                pts.append(QPointF(px, py))
            layer_paths.append(pts)
        self.canvas.set_layers(layer_paths, self._layer_colors, self._selected_layer_index)
        self.canvas.set_fade_trail_layer_index(
            self._selected_layer_index if self._fade_trail_enabled else None
        )
        self._sync_hid_project()

    def _load_recording_at(self, index: int) -> None:
        if index < 0 or index >= len(self._recordings):
            return
        rec = self._recordings[index]
        self._player.load(rec)
        self.timeline.set_duration_ms(self._player.duration_ms())
        self._player.seek_ms(0)
        self.canvas.clear_playback_marker()
        self.canvas.set_fade_trail([])
        self.osr_clicks_panel.set_recording(rec)
        self._debug_label.setText(f"Loaded layer {index+1}: {rec.name}")

    def _on_layer_selected(self, index: int) -> None:
        self._selected_layer_index = index
        if index < len(self._recordings):
            self._load_recording_at(index)
            self._update_canvas_layers()

    def _on_layer_color_changed(self, index: int, color: QColor) -> None:
        if 0 <= index < len(self._layer_colors):
            self._layer_colors[index] = color
            self._update_canvas_layers()

    def _on_play_selected_layer(self, index: int) -> None:
        self._selected_layer_index = index
        self._load_recording_at(index)
        self._update_canvas_layers()
        self._play_all_active = False
        self._player.play()

    def _on_play_all(self) -> None:
        if not self._recordings:
            return
        self._play_all_active = True
        self._selected_layer_index = 0
        self.layer_list.list_widget.setCurrentRow(0)
        self._load_recording_at(0)
        self._update_canvas_layers()
        self._player.play()


    def _apply_phantom_settings(self) -> None:
        pb = self.settings.phantom_box
        self.phantom_box_panel.set_state(pb.enabled, pb.editable, pb.lock_aspect)
        self.phantom_box_panel.set_values(
            pb.width_mm, pb.height_mm, pb.center_x_mm, pb.center_y_mm
        )
        if pb.enabled:
            self.canvas.set_phantom_box(
                pb.width_mm, pb.height_mm, pb.center_x_mm, pb.center_y_mm
            )
        else:
            self.canvas.clear_phantom_box()
        self.canvas.set_phantom_box_editable(pb.enabled and pb.editable)

    def _on_phantom_enabled(self, on: bool) -> None:
        self.settings.phantom_box.enabled = on
        self._apply_phantom_settings()
        self._sync_fit_tablet_button()
        self._debug_label.setText(
            "Tablet area shown (reference only — not exported)" if on
            else "Tablet area hidden"
        )

    def _on_phantom_editable(self, on: bool) -> None:
        self.settings.phantom_box.editable = on
        self.canvas.set_phantom_box_editable(self.settings.phantom_box.enabled and on)
        if on:
            self._debug_label.setText("Drag the tablet area or its corners to adjust")

    def _on_phantom_lock_aspect(self, on: bool) -> None:
        self.settings.phantom_box.lock_aspect = on
        self.canvas.set_svg_lock_aspect(on)

    def _on_phantom_values(self, w: float, h: float, cx: float, cy: float) -> None:
        pb = self.settings.phantom_box
        pb.width_mm, pb.height_mm, pb.center_x_mm, pb.center_y_mm = w, h, cx, cy
        if pb.enabled:
            self.canvas.set_phantom_box(w, h, cx, cy)

    def _on_phantom_center(self) -> None:
        pb = self.settings.phantom_box
        pb.center_x_mm = self.settings.printer.bed_width_mm / 2
        pb.center_y_mm = self.settings.printer.bed_height_mm / 2
        self._apply_phantom_settings()

    def _on_fit_to_tablet(self) -> None:
        pb = self.settings.phantom_box
        if not pb.enabled or not self.canvas.has_svg_preview():
            return

        if self._import_kind == "osr":
            pf_w = pb.width_mm * (PLAYFIELD_W / SCREEN_W)
            pf_h = pb.height_mm * (PLAYFIELD_H / SCREEN_H)
            self.canvas.update_svg_transform(
                width_mm=pf_w, height_mm=pf_h,
                center_x_mm=pb.center_x_mm, center_y_mm=pb.center_y_mm,
            )
            t = self.canvas.get_svg_transform()
            if t:
                self.svg_placement_panel.set_values(*t)
            self._debug_label.setText(
                f"Fitted to screen — playfield {pf_w:.1f} × {pf_h:.1f} mm "
                f"inside tablet {pb.width_mm:g} × {pb.height_mm:g} mm"
            )
        else:
            self.canvas.update_svg_transform(
                width_mm=pb.width_mm, height_mm=pb.height_mm,
                center_x_mm=pb.center_x_mm, center_y_mm=pb.center_y_mm,
            )
            t = self.canvas.get_svg_transform()
            if t:
                self.svg_placement_panel.set_values(*t)
            self._debug_label.setText(
                f"Fitted to tablet area — {pb.width_mm:g} × {pb.height_mm:g} mm"
            )

    def _sync_fit_tablet_button(self) -> None:
        self.svg_placement_panel.fit_tablet_btn.setVisible(
            self.settings.phantom_box.enabled
        )

    def _on_canvas_phantom_changed(self) -> None:
        box = self.canvas.get_phantom_box()
        if box is None:
            return
        w, h, cx, cy = box
        pb = self.settings.phantom_box
        pb.width_mm, pb.height_mm, pb.center_x_mm, pb.center_y_mm = w, h, cx, cy
        self.phantom_box_panel.set_values(w, h, cx, cy)


    def _on_edit_layer(self, index: int) -> None:
        if index < 0 or index >= len(self._recordings):
            return
        if self.canvas.has_svg_preview():
            QMessageBox.information(
                self, "Edit layer", "Finish placing the current import first."
            )
            return
        self._player.pause()
        rec = self._recordings[index]
        self._editing_layer_index = index
        outlier_indices = {i for i, p in enumerate(rec.points) if p.is_initial_outlier}
        self.canvas.start_edit(
            [(p.x_mm, p.y_mm) for p in rec.points], mode="transform",
            layer_index=index, outlier_indices=outlier_indices,
        )
        self.canvas.set_svg_lock_aspect(self.layer_edit_panel.lock_aspect.isChecked())
        self.layer_edit_panel.set_layer_name(rec.name)
        self.layer_edit_panel.transform_radio.setChecked(True)
        t = self.canvas.get_edit_transform()
        if t:
            self.layer_edit_panel.set_values(*t)
        self._layer_edit_box.setVisible(True)
        self._on_canvas_edit_changed()
        self._debug_label.setText(
            f"Editing {rec.name} — drag to move, corners to resize, green handle to rotate"
        )

    def _on_canvas_edit_changed(self) -> None:
        if not self.canvas.has_edit():
            return
        if self.canvas.edit_mode() == "transform":
            t = self.canvas.get_edit_transform()
            if t:
                self.layer_edit_panel.set_values(*t)
            self.layer_edit_panel.set_status(
                f"{self.canvas.edit_point_count()} points. "
                "Timestamps are preserved, so only the shape moves — not the timing."
            )
        else:
            sel = self.canvas.edit_selection_count()
            outlier_n = len(self.canvas._edit.get("outliers", set())) if self.canvas._edit else 0
            hint = f" ({outlier_n} red outlier(s) — select and delete them.)" if outlier_n else ""
            self.layer_edit_panel.set_status(
                f"{sel} of {self.canvas.edit_point_count()} points selected.{hint} "
                "Shift-click to add, drag empty space to rubber-band, Delete to remove."
            )
        self.canvas.update()

    def _on_layer_edit_values(self, w: float, h: float, rot: float, cx: float, cy: float) -> None:
        self.canvas.update_edit_transform(w, h, rot, cx, cy)

    def _on_layer_edit_delete(self) -> None:
        removed = self.canvas.delete_selected_points()
        if removed == 0 and self.canvas.edit_selection_count():
            QMessageBox.information(
                self, "Delete points",
                "A layer needs at least two points to remain a path.",
            )
        elif removed:
            self._debug_label.setText(f"Deleted {removed} point(s)")

    def _on_layer_edit_apply(self) -> None:
        idx = getattr(self, "_editing_layer_index", None)
        pts = self.canvas.get_edit_points()
        ids = self.canvas.get_edit_ids()
        if idx is None or pts is None or ids is None or idx >= len(self._recordings):
            self._on_layer_edit_cancel()
            return

        rec = self._recordings[idx]
        kept = []
        for (x_mm, y_mm), src_i in zip(pts, ids):
            src = rec.points[src_i]
            src.x_mm, src.y_mm = x_mm, y_mm
            kept.append(src)
        rec.points = kept

        peak = 0.0
        for i, p in enumerate(rec.points):
            if i == 0:
                p.velocity_mm_s = 0.0
                continue
            prev = rec.points[i - 1]
            dt = p.t_s - prev.t_s
            p.velocity_mm_s = (
                math.hypot(p.x_mm - prev.x_mm, p.y_mm - prev.y_mm) / dt if dt > 0 else 0.0
            )
            peak = max(peak, p.velocity_mm_s)
        rec.peak_velocity_mm_s = peak
        rec.duration_s = rec.points[-1].t_s if rec.points else 0.0

        self._reanchor_markers(rec)

        if self._last_osr and self._last_osr.get("rec") is rec:
            self._last_osr = None
            self.export_panel.set_has_osr(False)
            self.export_panel.set_speed_info("")

        self._end_layer_edit()
        self.layer_list.set_recordings(self._recordings, self._layer_colors)
        self.layer_list.list_widget.setCurrentRow(idx)
        if idx == self._selected_layer_index:
            pos = self._player.position_ms()
            self._player.load(rec)
            self.timeline.set_duration_ms(self._player.duration_ms())
            self._player.seek_ms(min(pos, self._player.duration_ms()))
            self.osr_clicks_panel.set_recording(rec)
        self._update_canvas_layers()
        self._update_speed_label()
        self._debug_label.setText(f"Applied edits to {rec.name} ({len(rec.points)} points)")

    @staticmethod
    def _reanchor_markers(rec: Recording) -> None:
        if not rec.points:
            return
        times = [p.t_s for p in rec.points]
        for marker in (*rec.click_points, *rec.key_release_points):
            i = bisect.bisect_left(times, marker.t_s)
            if i >= len(times):
                i = len(times) - 1
            elif i > 0 and abs(times[i - 1] - marker.t_s) <= abs(times[i] - marker.t_s):
                i -= 1
            marker.x_mm = rec.points[i].x_mm
            marker.y_mm = rec.points[i].y_mm

    def _on_layer_edit_cancel(self) -> None:
        self._end_layer_edit()
        self._update_canvas_layers()
        self._debug_label.setText("Edit cancelled")

    def _end_layer_edit(self) -> None:
        self.canvas.end_edit()
        self._layer_edit_box.setVisible(False)
        self._editing_layer_index = None

    def _on_delete_layer(self, index: int) -> None:
        if index < 0 or index >= len(self._recordings):
            return
        if self._last_osr and self._last_osr.get("rec") is self._recordings[index]:
            self._last_osr = None
            self.export_panel.set_has_osr(False)
            self.export_panel.set_speed_info("")
        del self._recordings[index]
        del self._layer_colors[index]
        self.layer_list.set_recordings(self._recordings, self._layer_colors)
        if not self._recordings:
            self._player.load(Recording())
            self.timeline.set_duration_ms(0)
            self.transport_controls.set_enabled_state(False)
            self.export_panel.set_has_recording(False)
            self.canvas.clear_playback_marker()
            self.canvas.clear_layers()
            self._sync_hid_project()
            self._selected_layer_index = 0
        else:
            new_idx = min(index, len(self._recordings)-1)
            self._selected_layer_index = new_idx
            self.layer_list.list_widget.setCurrentRow(new_idx)
            self._load_recording_at(new_idx)
            self._update_canvas_layers()
        self._update_speed_label()

    def _swap_layers(self, i: int, j: int) -> None:
        self._recordings[i], self._recordings[j] = self._recordings[j], self._recordings[i]
        self._layer_colors[i], self._layer_colors[j] = self._layer_colors[j], self._layer_colors[i]
        self.layer_list.set_recordings(self._recordings, self._layer_colors)
        self._selected_layer_index = j
        self.layer_list.list_widget.setCurrentRow(j)
        self._update_canvas_layers()

    def _on_move_layer_up(self, index: int) -> None:
        if 0 < index < len(self._recordings):
            self._swap_layers(index, index - 1)

    def _on_move_layer_down(self, index: int) -> None:
        if 0 <= index < len(self._recordings) - 1:
            self._swap_layers(index, index + 1)

    def _on_delete_all_layers(self) -> None:
        self._recordings = []
        self._layer_colors = []
        self._last_osr = None
        self.layer_list.clear()
        self._player.load(Recording())
        self.timeline.set_duration_ms(0)
        self.transport_controls.set_enabled_state(False)
        self.export_panel.set_has_recording(False)
        self.export_panel.set_has_osr(False)
        self.export_panel.set_speed_info("")
        self.canvas.clear_playback_marker()
        self.canvas.clear_layers()
        self._sync_hid_project()
        self._selected_layer_index = 0
        self.osr_clicks_panel.set_recording(None)
        self._update_speed_label()
        self._debug_label.setText("All layers deleted")
        logger.info("All layers deleted")

    def _on_play_clicked(self) -> None:
        self._play_all_active = False
        if self._player.has_recording():
            self._player.play()

    def _on_pause_clicked(self) -> None:
        self.osr_clicks_panel.release_keys()
        self._player.pause()

    def _on_stop_clicked(self) -> None:
        self._play_all_active = False
        self.osr_clicks_panel.release_keys()
        self._player.stop()
        self.canvas.clear_playback_marker()
        self.canvas.set_fade_trail([])
        if self._recordings and self._selected_layer_index < len(self._recordings):
            self._load_recording_at(self._selected_layer_index)
        else:
            self.timeline.set_duration_ms(0)
            self.transport_controls.set_enabled_state(False)

    def _on_restart_clicked(self) -> None:
        self._play_all_active = False
        self._player.restart()

    def _on_playback_finished(self) -> None:
        self.osr_clicks_panel.release_keys()
        self.canvas.clear_playback_marker()
        if self._play_all_active:
            next_idx = self._selected_layer_index + 1
            if next_idx < len(self._recordings):
                self._selected_layer_index = next_idx
                self.layer_list.list_widget.setCurrentRow(next_idx)
                self._load_recording_at(next_idx)
                self._update_canvas_layers()
                self._player.play()
            else:
                self._play_all_active = False
                logger.info("Play All finished")

    def _on_playback_position(self, x_mm: float, y_mm: float) -> None:
        self._last_marker_mm = (x_mm, y_mm)
        nx, ny = self._converter.to_normalized(x_mm, y_mm)
        self.canvas.set_playback_position(nx, ny)
        self.position_indicator.set_position(x_mm, y_mm)
        if self._fade_trail_enabled:
            self._update_fade_trail()

    def _update_fade_trail(self) -> None:
        rec = self._player.recording
        if rec is None or not rec.points:
            self.canvas.set_fade_trail([])
            return
        t_now = self._player.position_ms() / 1000.0
        t_min = t_now - self._fade_trail_duration_s
        rect = self.canvas._print_area_rect()
        pts_list = rec.points
        lo = bisect.bisect_left(pts_list, t_min, key=lambda p: p.t_s)
        hi = bisect.bisect_right(pts_list, t_now, key=lambda p: p.t_s)
        pts = []
        for p in pts_list[lo:hi]:
            if self._hide_click_spikes and p.is_click_spike:
                continue
            nx, ny = self._converter.to_normalized(p.x_mm, p.y_mm)
            pts.append(QPointF(rect.x() + nx * rect.width(), rect.y() + ny * rect.height()))
        self.canvas.set_fade_trail(pts)

    def _on_fade_trail_toggled(self, enabled: bool) -> None:
        self._fade_trail_enabled = enabled
        self.canvas.set_fade_trail_enabled(enabled)
        self.canvas.set_fade_trail_layer_index(self._selected_layer_index if enabled else None)
        if enabled:
            self._update_fade_trail()

    def _on_fade_trail_duration_changed(self, seconds: float) -> None:
        self._fade_trail_duration_s = seconds
        if self._fade_trail_enabled:
            self._update_fade_trail()

    def _on_hide_click_spikes_toggled(self, enabled: bool) -> None:
        self._hide_click_spikes = enabled
        self._player.set_skip_click_spikes(enabled)
        self._update_canvas_layers()
        if self._fade_trail_enabled:
            self._update_fade_trail()

    def _on_player_progress(self, elapsed_ms: int) -> None:
        self.osr_clicks_panel.update_time(elapsed_ms / 1000.0)

    def _on_mouse_moved(self, nx: float, ny: float) -> None:
        printer_point = self._converter.convert(nx, ny)
        filtered = self._motion_filter.process(printer_point)

        self._debug_label.setText(
            f"cursor: {nx:.3f}, {ny:.3f}  ->  bed: {printer_point.x_mm:.2f}, {printer_point.y_mm:.2f} mm"
        )

        if filtered is not None:
            self.position_indicator.set_position(filtered.x_mm, filtered.y_mm)
            if self._recorder.is_recording:
                velocity_mm_s = self._recorder.add_point(filtered.x_mm, filtered.y_mm)
                if velocity_mm_s is not None:
                    limit = self.settings.printer.max_speed_mm_s
                    self._stroke_peak_velocity_mm_s = max(self._stroke_peak_velocity_mm_s, velocity_mm_s)
                    if velocity_mm_s > limit:
                        self.canvas.mark_last_segment_overspeed()
                        self._speed_warning_label.setText(
                            f"Drawing at {velocity_mm_s:.0f} mm/s — exceeds travel "
                            f"speed limit ({limit:.0f} mm/s)"
                        )
