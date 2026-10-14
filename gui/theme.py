# Application color theme and stylesheet
from __future__ import annotations

from string import Template

BG = "#2e2e2e"
SURFACE = "#262626"
SURFACE_DEEP = "#1a1a1a"
SURFACE_RAISED = "#383838"
SURFACE_HOVER = "#444444"

BORDER = "#3d3d3d"
BORDER_SUBTLE = "#242424"
BORDER_STRONG = "#555555"

TEXT = "#ffffff"
TEXT_DIM = "#cccccc"
TEXT_MUTED = "#aaaaaa"
TEXT_FAINT = "#8a8a8a"
TEXT_DISABLED = "#6e6e6e"
ON_ACCENT = "#1a1a1a"

ACCENT = "#ff66cc"
ACCENT_HOVER = "#ff8ad8"
ACCENT_PRESSED = "#e055b3"
ACCENT_ALT = "#6b4da8"
ACCENT_ALT_HOVER = "#8163c4"
ACCENT_ALT_LIGHT = "#c9a2ff"

LIST_SELECTION = "#43335c"

OK = "#7ec87e"
WARN = "#d8b25c"
DANGER = "#ff5c5c"
DANGER_SOFT = "#ff8080"

CANVAS_OUTSIDE = SURFACE_DEEP
CANVAS_BED = BG
CANVAS_GRID = BORDER
CANVAS_BED_BORDER = ACCENT
CANVAS_CROSSHAIR = TEXT

LIVE_STROKE = TEXT
FADE_TRAIL = TEXT
OVERSPEED = DANGER
PLAYBACK_MARKER = ACCENT

EDIT_BOX = WARN
EDIT_POINT = ACCENT_ALT_LIGHT
EDIT_SELECTED = ACCENT
ROTATE_HANDLE = OK

SVG_PREVIEW = WARN
SVG_BOX = ACCENT_ALT_LIGHT
SVG_HANDLE_FILL = TEXT

PHANTOM = "#5ad1c8"
TRIGGER_POINT = "#ff9933"
PRINTER_HEAD = "#00ff88"

TRIANGLE_BG = SURFACE_DEEP
TRIANGLE_BASE = SURFACE_HOVER
TRIANGLE_HIGHLIGHT = ACCENT_ALT

KEY_BG = SURFACE_DEEP
KEY_IDLE = BG
KEY_BORDER = "#4d4d4d"
KEY_TEXT_IDLE = TEXT_MUTED
KEY_TEXT_ACTIVE = ON_ACCENT
KEY_COUNT_IDLE = TEXT_FAINT
KEY1 = ACCENT
KEY2 = ACCENT_ALT_LIGHT

PANEL_TITLE_STYLE = f"font-weight: 600; color: {TEXT_DIM};"
HINT_STYLE = f"color: {TEXT_MUTED}; font-size: 11px;"


_STYLESHEET = Template(
    """
    QMainWindow, QWidget {
        background-color: $BG;
        color: $TEXT;
        selection-background-color: $ACCENT_ALT;
        selection-color: $TEXT;
    }

    QToolTip {
        background-color: $SURFACE_DEEP;
        color: $TEXT;
        border: 1px solid $ACCENT_ALT;
        padding: 4px 6px;
        border-radius: 4px;
    }

    /* ---- Menu bar / menus ----
       Transparent so TriangleMenuBar's animated pattern shows through; items
       get their own filled box so their labels stay readable on top of it. */
    QMenuBar {
        background: transparent;
        border-bottom: 1px solid $BORDER_SUBTLE;
        padding: 2px;
    }
    QMenuBar::item {
        background-color: $SURFACE;
        border: 1px solid $BORDER;
        margin: 1px 4px;
        padding: 3px 12px;
        border-radius: 4px;
    }
    QMenuBar::item:selected { background-color: $SURFACE_RAISED; border-color: $ACCENT; }
    QMenu {
        background-color: $SURFACE_DEEP;
        border: 1px solid $BORDER;
        padding: 4px;
    }
    QMenu::item {
        padding: 6px 22px 6px 12px;
        border-radius: 4px;
    }
    QMenu::item:selected { background-color: $ACCENT_ALT; color: $TEXT; }
    QMenu::separator { height: 1px; background: $BORDER; margin: 4px 6px; }

    /* ---- Buttons ---- */
    QPushButton {
        background-color: $SURFACE_RAISED;
        border: 1px solid $BORDER;
        border-radius: 6px;
        padding: 6px 14px;
        color: $TEXT;
    }
    QPushButton:hover { background-color: $SURFACE_HOVER; border-color: $ACCENT_ALT; }
    QPushButton:pressed { background-color: $SURFACE; }
    QPushButton:checked {
        background-color: $ACCENT; border-color: $ACCENT; color: $ON_ACCENT; font-weight: 600;
    }
    QPushButton:checked:hover { background-color: $ACCENT_HOVER; border-color: $ACCENT_HOVER; }
    QPushButton:checked:pressed { background-color: $ACCENT_PRESSED; border-color: $ACCENT_PRESSED; }
    QPushButton:disabled { color: $TEXT_DISABLED; background-color: $SURFACE; border-color: $BORDER_SUBTLE; }

    QToolButton {
        background: transparent;
        border: none;
        border-radius: 4px;
        padding: 4px 6px;
        color: $TEXT;
    }
    QToolButton:hover { background-color: $SURFACE_RAISED; }

    /* ---- Group boxes (section frames) ---- */
    QGroupBox {
        border: 1px solid $BORDER;
        border-radius: 8px;
        margin-top: 14px;
        padding: 10px 8px 8px 8px;
        background-color: $SURFACE_DEEP;
        font-weight: 600;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 6px;
        color: $TEXT_MUTED;
    }

    QFrame { border: 1px solid $BORDER_SUBTLE; border-radius: 6px; }

    QLabel { color: $TEXT; background: transparent; }

    /* ---- Status bar ---- */
    QStatusBar {
        background-color: $SURFACE_DEEP;
        border-top: 1px solid $BORDER_SUBTLE;
        color: $TEXT_MUTED;
    }

    /* ---- Lists ---- */
    QListWidget {
        background: $SURFACE_DEEP;
        border: 1px solid $BORDER;
        border-radius: 6px;
        outline: none;
        padding: 2px;
    }
    QListWidget::item { padding: 5px 6px; border-radius: 4px; }
    QListWidget::item:hover { background-color: $SURFACE_RAISED; }
    QListWidget::item:selected { background-color: $LIST_SELECTION; color: $TEXT; }

    /* ---- Inputs ---- */
    QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit, QPlainTextEdit {
        background-color: $SURFACE;
        border: 1px solid $BORDER;
        border-radius: 5px;
        padding: 4px 6px;
        color: $TEXT;
        selection-background-color: $ACCENT_ALT;
    }
    QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover {
        border-color: $BORDER_STRONG;
    }
    QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus, QPlainTextEdit:focus {
        border: 1px solid $ACCENT;
    }
    QComboBox::drop-down { border: none; width: 20px; }
    QComboBox QAbstractItemView {
        background-color: $SURFACE;
        border: 1px solid $BORDER;
        selection-background-color: $ACCENT_ALT;
        selection-color: $TEXT;
        outline: none;
    }
    QSpinBox::up-button, QSpinBox::down-button,
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
        background: $SURFACE_RAISED; width: 16px; border-left: 1px solid $BORDER;
    }
    QSpinBox::up-button:hover, QSpinBox::down-button:hover,
    QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {
        background: $SURFACE_HOVER;
    }

    QCheckBox { spacing: 8px; }
    QCheckBox::indicator {
        width: 16px; height: 16px; border-radius: 4px;
        border: 1px solid $BORDER_STRONG; background: $SURFACE;
    }
    QCheckBox::indicator:hover { border-color: $ACCENT; }
    QCheckBox::indicator:checked {
        background-color: $ACCENT_ALT; border-color: $ACCENT_ALT;
    }

    /* ---- Sliders ---- */
    QSlider::groove:horizontal {
        height: 4px; background: $SURFACE_RAISED; border-radius: 2px;
    }
    QSlider::sub-page:horizontal { background: $ACCENT_ALT; border-radius: 2px; }
    QSlider::handle:horizontal {
        background: $ACCENT; width: 12px; margin: -5px 0; border-radius: 6px;
    }
    QSlider::handle:horizontal:hover { background: $ACCENT_HOVER; }
    QSlider::handle:horizontal:disabled { background: $SURFACE_HOVER; }
    QSlider::sub-page:horizontal:disabled { background: $SURFACE_RAISED; }

    /* ---- Splitters ---- */
    QSplitter::handle { background-color: $BORDER_SUBTLE; }
    QSplitter::handle:hover { background-color: $ACCENT; }
    QSplitter::handle:horizontal { width: 8px; }
    QSplitter::handle:vertical { height: 8px; }

    QScrollArea { border: none; }

    /* ---- Scrollbars ---- */
    QScrollBar:vertical {
        background: transparent; width: 18px; margin: 2px;
    }
    QScrollBar::handle:vertical {
        background: $SURFACE_HOVER; border-radius: 6px; min-height: 28px;
    }
    QScrollBar::handle:vertical:hover { background: $ACCENT_ALT; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QScrollBar:horizontal {
        background: transparent; height: 11px; margin: 2px;
    }
    QScrollBar::handle:horizontal {
        background: $SURFACE_HOVER; border-radius: 4px; min-width: 24px;
    }
    QScrollBar::handle:horizontal:hover { background: $ACCENT_ALT; }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
    """
)


def stylesheet() -> str:
    return _STYLESHEET.substitute(globals())
