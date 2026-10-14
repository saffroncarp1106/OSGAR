# Settings editing dialog window form
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from gui import theme
from config.settings import AppSettings
from utils.logging_setup import get_logger

logger = get_logger("gui")


def _spin(minimum: float, maximum: float, value: float, decimals: int = 2, step: float = 1.0) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(minimum, maximum)
    box.setDecimals(decimals)
    box.setSingleStep(step)
    box.setValue(value)
    return box


class SettingsDialog(QDialog):
    settings_applied = Signal()

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Settings")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)

        bed_group = QGroupBox("Bed / Print Area")
        bed_form = QFormLayout(bed_group)
        p = settings.printer
        self.bed_width = _spin(10, 2000, p.bed_width_mm, 1, 1)
        self.bed_height = _spin(10, 2000, p.bed_height_mm, 1, 1)
        self.origin_x = _spin(-1000, 1000, p.origin_x_mm, 1, 1)
        self.origin_y = _spin(-1000, 1000, p.origin_y_mm, 1, 1)
        self.max_speed = _spin(1, 2000, p.max_speed_mm_s, 1, 10)
        bed_form.addRow("Bed width (mm):", self.bed_width)
        bed_form.addRow("Bed height (mm):", self.bed_height)
        bed_form.addRow("Origin X (mm):", self.origin_x)
        bed_form.addRow("Origin Y (mm):", self.origin_y)
        bed_form.addRow("Max speed (mm/s):", self.max_speed)
        note = QLabel("This must match your printer's actual bed size and maximum speed — set them by hand.")
        note.setWordWrap(True)
        note.setStyleSheet(theme.HINT_STYLE)
        bed_form.addRow(note)
        layout.addWidget(bed_group)

        motion_group = QGroupBox("Motion")
        motion_form = QFormLayout(motion_group)
        m = settings.motion
        self.scale = _spin(0.01, 5.0, m.scale, 2, 0.05)
        self.smoothing = _spin(0.0, 1.0, m.smoothing_factor, 2, 0.05)
        self.deadzone = _spin(0.0, 50.0, m.deadzone_px, 1, 0.5)
        motion_form.addRow("Scale:", self.scale)
        motion_form.addRow("Smoothing (0-1):", self.smoothing)
        motion_form.addRow("Deadzone (mm):", self.deadzone)
        layout.addWidget(motion_group)

        osr_group = QGroupBox("Replay import (.osr)")
        osr_form = QFormLayout(osr_group)
        o = settings.osr
        self.osr_tick = _spin(4, 33, o.playback_tick_ms, 0, 1)
        self.osr_faithful = QCheckBox("Decode exactly like osu! (recommended)")
        self.osr_faithful.setToolTip(
            "Uses the replay frames exactly as osu! reads them: no smoothing, no\n"
            "de-duplication, no gap filling, no re-timing.\n\n"
            "osu!'s own replay pipeline does none of those and renders replays\n"
            "correctly, so this is the reference behaviour rather than a fallback.\n"
            "tools/check_against_osu.py verifies the result is frame-for-frame\n"
            "identical to osu!'s LegacyScoreDecoder.\n\n"
            "Unchecking this enables the experimental repair passes below, each of\n"
            "which moves the path away from what osu! shows."
        )
        self.osr_faithful.setChecked(o.osu_faithful)
        self.osr_align_map = QCheckBox("Start the timeline at the song, not the lead-in")
        self.osr_align_map.setToolTip(
            "osu! records the cursor during the lead-in, before the audio starts, by a\n"
            "different amount in every replay (1696 ms in BackandForthWithClicking.osr,\n"
            "about 27% of the file).\n\n"
            "With this on, t=0 is the moment the song starts - the one instant you can\n"
            "also identify in gameplay footage - so preview and video line up by\n"
            "starting them together. With it off the preview is offset by the lead-in\n"
            "for its whole length, which put the cursor on the opposite side of the\n"
            "playfield in testing."
        )
        self.osr_align_map.setChecked(o.align_to_map_start)
        self.osr_retime_clicks = QCheckBox("Fix the slight drag at each click")
        self.osr_retime_clicks.setToolTip(
            "Ignored unless \"Decode exactly like osu!\" is unchecked.\n"
            "Moves frames off the times osu! recorded (by up to ~28 ms on the\n"
            "reference replays) to smooth the motion through a click."
        )
        self.osr_retime_clicks.setChecked(o.retime_clicks)
        self.osr_recover_gaps = QCheckBox("Stretch dropped-frame gaps (BREAKS sync with footage)")
        self.osr_recover_gaps.setToolTip(
            "Off by default, and should stay off if you compare the preview against\n"
            "gameplay footage.\n\n"
            "Gaps are always filled with points so the path stays continuous. This\n"
            "option additionally INSERTS the time osu! failed to record, which pushes\n"
            "every later timestamp back permanently. Gaps cluster exactly where\n"
            "clicking starts, so the preview tracks perfectly until the first click\n"
            "and then runs late for the rest of the replay.\n\n"
            "Only useful for a replay that will never be lined up against footage."
        )
        self.osr_recover_gaps.setChecked(o.recover_gap_time)
        self.osr_gap_speed = _spin(50, 99, o.gap_speed_pct * 100.0, 0, 5)
        self.osr_gap_detect = _spin(1.5, 10.0, o.gap_detect_ratio, 1, 0.5)
        osr_form.addRow("Playback smoothness (ms/frame):", self.osr_tick)
        osr_form.addRow(self.osr_faithful)
        osr_form.addRow(self.osr_align_map)
        osr_form.addRow(self.osr_retime_clicks)
        osr_form.addRow(self.osr_recover_gaps)
        osr_form.addRow("Gap fill speed (percentile %):", self.osr_gap_speed)
        osr_form.addRow("Gap detect (× normal frame gap):", self.osr_gap_detect)
        osr_note = QLabel(
            "Smoothness applies instantly; the rest re-apply to the loaded replay when "
            "you press OK.\n"
            "The two boxes below \"Decode exactly like osu!\" are experimental repair "
            "passes and are ignored while it is ticked. osu! itself does no smoothing, "
            "de-duplication, gap filling or re-timing, and its replays render correctly — "
            "so if a replay imports at the wrong length or drifts against footage with "
            "these defaults, that is a bug, not a setting."
        )
        osr_note.setWordWrap(True)
        osr_note.setStyleSheet(theme.HINT_STYLE)
        osr_form.addRow(osr_note)
        layout.addWidget(osr_group)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        p = self.settings.printer
        p.bed_width_mm = self.bed_width.value()
        p.bed_height_mm = self.bed_height.value()
        p.origin_x_mm = self.origin_x.value()
        p.origin_y_mm = self.origin_y.value()
        p.max_speed_mm_s = self.max_speed.value()

        m = self.settings.motion
        m.scale = self.scale.value()
        m.smoothing_factor = self.smoothing.value()
        m.deadzone_px = self.deadzone.value()

        o = self.settings.osr
        o.playback_tick_ms = int(self.osr_tick.value())
        o.osu_faithful = self.osr_faithful.isChecked()
        o.align_to_map_start = self.osr_align_map.isChecked()
        o.retime_clicks = self.osr_retime_clicks.isChecked()
        o.recover_gap_time = self.osr_recover_gaps.isChecked()
        o.gap_speed_pct = self.osr_gap_speed.value() / 100.0
        o.gap_detect_ratio = self.osr_gap_detect.value()

        try:
            self.settings.save()
        except OSError as exc:
            logger.warning("Could not persist settings: %s", exc)

        self.settings_applied.emit()
        self.accept()
