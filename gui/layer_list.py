# Layer list management action widget
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QColorConstants
from PySide6.QtWidgets import (
    QColorDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from recording.model import Recording


class LayerList(QWidget):
    layer_selected = Signal(int)
    play_selected = Signal(int)
    play_all = Signal()
    delete_selected = Signal(int)
    delete_all = Signal()
    color_changed = Signal(int, QColor)
    move_layer_up = Signal(int)
    move_layer_down = Signal(int)
    edit_selected = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.list_widget.currentRowChanged.connect(self._on_current_row_changed)

        order_layout = QHBoxLayout()
        self.move_up_btn = QPushButton("Move Up")
        self.move_down_btn = QPushButton("Move Down")
        order_layout.addWidget(self.move_up_btn)
        order_layout.addWidget(self.move_down_btn)

        btn_layout = QHBoxLayout()
        self.play_btn = QPushButton("Play Selected")
        self.play_all_btn = QPushButton("Play All")
        self.delete_btn = QPushButton("Delete")
        self.delete_all_btn = QPushButton("Delete All")
        self.edit_btn = QPushButton("Edit")
        self.edit_btn.setToolTip(
            "Move, resize or rotate this layer, or edit its individual points.\n"
            "Works on any layer - imported replay, SVG, or hand-drawn."
        )

        btn_layout.addWidget(self.play_btn)
        btn_layout.addWidget(self.play_all_btn)
        btn_layout.addWidget(self.edit_btn)
        btn_layout.addWidget(self.delete_btn)
        btn_layout.addWidget(self.delete_all_btn)

        layout.addWidget(self.list_widget)
        layout.addLayout(order_layout)
        layout.addLayout(btn_layout)

        self.play_btn.clicked.connect(self._on_play_selected)
        self.play_all_btn.clicked.connect(self.play_all.emit)
        self.delete_btn.clicked.connect(self._on_delete_selected)
        self.delete_all_btn.clicked.connect(self._on_delete_all)
        self.move_up_btn.clicked.connect(self._on_move_up)
        self.move_down_btn.clicked.connect(self._on_move_down)
        self.edit_btn.clicked.connect(self._on_edit_selected)

        self._recordings: list[Recording] = []
        self._colors: list[QColor] = []
        self._selected_index: int | None = None

    def _on_edit_selected(self) -> None:
        idx = self.list_widget.currentRow()
        if idx < 0 or idx >= len(self._recordings):
            QMessageBox.information(self, "Edit layer", "Select a layer first.")
            return
        self.edit_selected.emit(idx)

    def set_recordings(self, recordings: list[Recording], colors: list[QColor]) -> None:
        self._recordings = list(recordings)
        self._colors = list(colors)
        self._refresh_list()

    def add_recording(self, recording: Recording, color: QColor) -> None:
        self._recordings.append(recording)
        self._colors.append(color)
        self._refresh_list()
        idx = len(self._recordings) - 1
        self.list_widget.setCurrentRow(idx)
        self._selected_index = idx
        self.layer_selected.emit(idx)

    def clear(self) -> None:
        self._recordings.clear()
        self._colors.clear()
        self._refresh_list()
        self._selected_index = None

    def get_selected_index(self) -> int | None:
        return self._selected_index

    def _refresh_list(self) -> None:
        self.list_widget.clear()
        for idx, rec in enumerate(self._recordings):
            name = rec.name if rec.name else f"Layer {idx+1}"
            duration = rec.duration_s
            color = self._colors[idx] if idx < len(self._colors) else QColorConstants.Gray
            item_text = f"{name} ({duration:.1f}s)"
            item = QListWidgetItem(item_text)
            item.setForeground(color)
            self.list_widget.addItem(item)

    def _on_current_row_changed(self, row: int) -> None:
        self._selected_index = row if row >= 0 else None

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        idx = self.list_widget.row(item)
        self._selected_index = idx
        self.layer_selected.emit(idx)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        idx = self.list_widget.row(item)
        if 0 <= idx < len(self._colors):
            color = QColorDialog.getColor(self._colors[idx], self, "Choose Layer Color")
            if color.isValid():
                self._colors[idx] = color
                self._refresh_list()
                self.color_changed.emit(idx, color)

    def _on_play_selected(self) -> None:
        if self._selected_index is not None:
            self.play_selected.emit(self._selected_index)

    def _on_delete_selected(self) -> None:
        idx = self._selected_index
        if idx is None:
            idx = self.list_widget.currentRow()
        if idx is not None and 0 <= idx < len(self._recordings):
            self.delete_selected.emit(idx)

    def _on_delete_all(self) -> None:
        if not self._recordings:
            return
        reply = QMessageBox.question(
            self, "Delete All Layers", f"Delete all {len(self._recordings)} layer(s)? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.delete_all.emit()

    def _on_move_up(self) -> None:
        idx = self._selected_index
        if idx is not None and idx > 0:
            self.move_layer_up.emit(idx)

    def _on_move_down(self) -> None:
        idx = self._selected_index
        if idx is not None and idx < len(self._recordings) - 1:
            self.move_layer_down.emit(idx)
