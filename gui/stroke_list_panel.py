# Stroke list display and panel
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QPushButton, QVBoxLayout, QWidget

from gui import theme


class StrokeListPanel(QWidget):
    stroke_selected = Signal(int)
    remove_requested = Signal(int)
    clear_all_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        title = QLabel("Sequence")
        title.setStyleSheet(theme.PANEL_TITLE_STYLE)
        layout.addWidget(title)

        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget, 1)

        button_row = QHBoxLayout()
        self.remove_btn = QPushButton("Remove Selected")
        self.clear_btn = QPushButton("Clear All")
        button_row.addWidget(self.remove_btn)
        button_row.addWidget(self.clear_btn)
        layout.addLayout(button_row)

        self.list_widget.currentRowChanged.connect(self.stroke_selected.emit)
        self.remove_btn.clicked.connect(self._on_remove_clicked)
        self.clear_btn.clicked.connect(self.clear_all_requested.emit)

    def _on_remove_clicked(self) -> None:
        row = self.list_widget.currentRow()
        if row >= 0:
            self.remove_requested.emit(row)

    def set_strokes(self, descriptions: list[str]) -> None:
        blocked = self.list_widget.blockSignals(True)
        self.list_widget.clear()
        self.list_widget.addItems(descriptions)
        if descriptions:
            self.list_widget.setCurrentRow(len(descriptions) - 1)
        self.list_widget.blockSignals(blocked)
