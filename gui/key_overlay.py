# Key press visual overlay widget
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from gui import theme

BAR_SPEED_PX_S = 300.0

_ROW_H = 58
_KEY_SIZE = 46
_MARGIN = 6
_GAP = 10
_KEY_RADIUS = 8
_BAR_RADIUS = 3
_OUTLINE = 2.0
_BAR_HEIGHT_FRAC = 0.62
_MIN_BAR_W = 2.0
_FADE_FRAC = 0.30

_BG = QColor(theme.KEY_BG)
_KEY_IDLE = QColor(theme.KEY_IDLE)
_KEY_BORDER = QColor(theme.KEY_BORDER)
_TEXT_IDLE = QColor(theme.KEY_TEXT_IDLE)
_TEXT_ACTIVE = QColor(theme.KEY_TEXT_ACTIVE)
_COUNT_IDLE = QColor(theme.KEY_COUNT_IDLE)

Interval = tuple[float, float]


class KeyOverlay(QWidget):

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._keys: list[tuple[str, list[Interval], QColor]] = []
        self._t = 0.0
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._apply_height()

    def set_keys(self, keys: list[tuple[str, list[Interval], QColor]]) -> None:
        self._keys = keys
        self._apply_height()
        self.update()

    def update_time(self, t_s: float) -> None:
        self._t = t_s
        self.update()

    def _apply_height(self) -> None:
        h = max(len(self._keys), 1) * _ROW_H
        self.setFixedHeight(h)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), _BG)

        keys = self._keys or [("--", [], _KEY_BORDER)]
        for row, (label, intervals, colour) in enumerate(keys):
            self._paint_row(p, row, label, intervals, colour)
        p.end()

    def _paint_row(
        self,
        p: QPainter,
        row: int,
        label: str,
        intervals: list[Interval],
        colour: QColor,
    ) -> None:
        top = row * _ROW_H
        box = QRectF(
            _MARGIN,
            top + (_ROW_H - _KEY_SIZE) / 2.0,
            _KEY_SIZE,
            _KEY_SIZE,
        )
        active = self._is_held(intervals, self._t)

        lane_x = box.right() + _GAP
        lane = QRectF(lane_x, box.y(), max(self.width() - lane_x - _MARGIN, 0.0), _KEY_SIZE)
        if lane.width() > 0:
            self._paint_lane(p, lane, intervals, colour)
        self._paint_key(p, box, label, colour, active, self._count(intervals, self._t))

    def _paint_lane(
        self, p: QPainter, lane: QRectF, intervals: list[Interval], colour: QColor
    ) -> None:
        p.save()
        p.setClipRect(lane)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(colour)

        bar_h = _KEY_SIZE * _BAR_HEIGHT_FRAC
        bar_y = lane.center().y() - bar_h / 2.0

        for press, release in reversed(intervals):
            if press > self._t:
                continue
            end = min(release, self._t)
            x0 = lane.x() + (self._t - end) * BAR_SPEED_PX_S
            if x0 > lane.right():
                break
            x1 = lane.x() + (self._t - press) * BAR_SPEED_PX_S
            p.drawRoundedRect(
                QRectF(x0, bar_y, max(x1 - x0, _MIN_BAR_W), bar_h),
                _BAR_RADIUS,
                _BAR_RADIUS,
            )

        fade_w = lane.width() * _FADE_FRAC
        if fade_w > 0:
            grad = QLinearGradient(lane.right() - fade_w, 0.0, lane.right(), 0.0)
            clear = QColor(_BG)
            clear.setAlpha(0)
            grad.setColorAt(0.0, clear)
            grad.setColorAt(1.0, _BG)
            p.fillRect(lane, grad)
        p.restore()

    def _paint_key(
        self,
        p: QPainter,
        box: QRectF,
        label: str,
        colour: QColor,
        active: bool,
        count: int,
    ) -> None:
        p.setPen(QPen(colour if active else _KEY_BORDER, _OUTLINE))
        p.setBrush(colour if active else _KEY_IDLE)
        p.drawRoundedRect(box, _KEY_RADIUS, _KEY_RADIUS)

        name_font = QFont(self.font())
        name_font.setBold(True)
        name_font.setPointSize(11)
        p.setFont(name_font)
        p.setPen(_TEXT_ACTIVE if active else _TEXT_IDLE)
        p.drawText(
            QRectF(box.x(), box.y() + 5, box.width(), box.height() * 0.52),
            Qt.AlignmentFlag.AlignCenter,
            label,
        )

        count_font = QFont(self.font())
        count_font.setPointSize(8)
        p.setFont(count_font)
        p.setPen(_TEXT_ACTIVE if active else _COUNT_IDLE)
        p.drawText(
            QRectF(
                box.x(),
                box.y() + box.height() * 0.5,
                box.width(),
                box.height() * 0.45,
            ),
            Qt.AlignmentFlag.AlignCenter,
            str(count),
        )

    @staticmethod
    def _is_held(intervals: list[Interval], t: float) -> bool:
        return any(press <= t <= release for press, release in intervals)

    @staticmethod
    def _count(intervals: list[Interval], t: float) -> int:
        return sum(1 for press, _ in intervals if press <= t)
