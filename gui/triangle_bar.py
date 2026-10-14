# Animated triangle pattern menu bar
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPolygonF
from PySide6.QtWidgets import QMenuBar, QWidget

from gui import theme

_STEP = 14
_HEIGHT = 12
_GAP = 8
_INTERVAL_MS = 50


class TriangleMenuBar(QMenuBar):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._frame = 0
        self._bg = QColor(theme.TRIANGLE_BG)
        self._base = QColor(theme.TRIANGLE_BASE)
        self._highlight = QColor(theme.TRIANGLE_HIGHLIGHT)

        self._timer = QTimer(self)
        self._timer.setInterval(_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)

    def _tick(self) -> None:
        self._frame += 1
        self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), self._bg)

        base_path, high_path = QPainterPath(), QPainterPath()
        w, f = self.width(), self._frame
        bottom = float(self.height())
        apex = bottom - _HEIGHT

        items_end = 0
        for action in self.actions():
            if action.isVisible():
                items_end = max(items_end, self.actionGeometry(action).right())
        first = math.ceil((items_end + _GAP) / _STEP) if items_end else 0

        for i in range(first, w // _STEP + 2):
            x = i * _STEP
            tri = QPolygonF([
                QPointF(x, bottom),
                QPointF(x + _STEP / 2.0, apex),
                QPointF(x + _STEP, bottom),
            ])
            lit = math.sin(f * 0.05 + x * 0.01) > 0.2
            (high_path if lit else base_path).addPolygon(tri)

        p.setPen(Qt.PenStyle.NoPen)
        p.fillPath(base_path, self._base)
        p.fillPath(high_path, self._highlight)
        p.end()

        super().paintEvent(event)
