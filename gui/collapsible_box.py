# Collapsible container panel widget box
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QSizePolicy, QToolButton, QVBoxLayout, QWidget

from gui import theme


class CollapsibleBox(QWidget):

    toggled = Signal(bool)

    def __init__(self, title: str, content: QWidget, start_expanded: bool = True,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._toggle_btn = QToolButton(self)
        self._toggle_btn.setText(f" {title}")
        self._toggle_btn.setCheckable(True)
        self._toggle_btn.setChecked(start_expanded)
        self._toggle_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._toggle_btn.setArrowType(Qt.ArrowType.DownArrow if start_expanded else Qt.ArrowType.RightArrow)
        self._toggle_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._toggle_btn.setStyleSheet(
            "QToolButton { border: none; font-weight: 600; padding: 6px; text-align: left;"
            f" color: {theme.TEXT_MUTED}; }}"
            f"QToolButton:hover {{ background-color: {theme.SURFACE_RAISED}; border-radius: 4px;"
            f" color: {theme.TEXT}; }}"
        )
        self._toggle_btn.clicked.connect(self._on_toggled)

        self._content = content
        self._content.setVisible(start_expanded)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._toggle_btn)
        layout.addWidget(self._content)

    def _on_toggled(self, checked: bool) -> None:
        self._content.setVisible(checked)
        self._toggle_btn.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
        self.toggled.emit(checked)

    def set_expanded(self, expanded: bool) -> None:
        if expanded != self._toggle_btn.isChecked():
            self._toggle_btn.setChecked(expanded)
        self._on_toggled(expanded)

    def is_expanded(self) -> bool:
        return self._toggle_btn.isChecked()

    def content(self) -> QWidget:
        return self._content
