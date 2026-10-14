# Interactive bed drawing canvas widget
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QPolygonF, QResizeEvent
from PySide6.QtWidgets import QWidget

from gui import theme


class DrawingCanvas(QWidget):

    mouse_moved = Signal(float, float)
    mouse_pressed = Signal(float, float)
    mouse_released = Signal(float, float)
    svg_transform_changed = Signal()
    resized = Signal()
    edit_changed = Signal()
    phantom_box_changed = Signal()
    trigger_point_changed = Signal(float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(400, 400)

        self._bed_width_mm = 235.0
        self._bed_height_mm = 235.0

        self._live_path: list[QPointF] = []
        self._live_path_overspeed: list[bool] = []
        self._is_drawing = False

        self._layers: list[list[QPointF]] = []
        self._layer_colors: list[QColor] = []
        self._selected_layer_index: int | None = None

        self._playback_marker: QPointF | None = None
        self._playback_color = QColor(theme.PLAYBACK_MARKER)

        self._fade_trail_enabled = False
        self._fade_trail_layer_index: int | None = None
        self._fade_trail_points: list[QPointF] = []
        self._fade_trail_color = QColor(theme.FADE_TRAIL)

        self._drawing_mode = "freehand"

        self._line_start_pixel: QPointF | None = None

        self._bg_color = QColor(theme.CANVAS_BED)
        self._grid_color = QColor(theme.CANVAS_GRID)
        self._live_color = QColor(theme.LIVE_STROKE)
        self._overspeed_color = QColor(theme.OVERSPEED)
        self._area_border_color = QColor(theme.CANVAS_BED_BORDER)
        self._crosshair_color = QColor(theme.CANVAS_CROSSHAIR)

        self._max_live_points = 4000

        self._svg_preview: dict | None = None
        self._svg_drag: dict | None = None
        self._svg_lock_aspect = True
        self._handle_radius_px = 7.0
        self._rotate_handle_offset_px = 28.0

        self._edit: dict | None = None
        self._edit_drag: dict | None = None
        self._point_hit_radius_px = 6.0
        self._edit_box_color = QColor(theme.EDIT_BOX)
        self._edit_point_color = QColor(theme.EDIT_POINT)
        self._edit_sel_color = QColor(theme.EDIT_SELECTED)
        self._edit_outlier_color = QColor(theme.DANGER)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._phantom: dict | None = None
        self._phantom_editable = False
        self._phantom_drag: dict | None = None
        self._phantom_color = QColor(theme.PHANTOM)

        self._trigger_point: tuple[float, float] | None = None
        self._trigger_drag = False
        self._trigger_color = QColor(theme.TRIGGER_POINT)
        self._trigger_radius_px = 8.0

        self._printer_head: tuple[float, float] | None = None
        self._printer_head_color = QColor(theme.PRINTER_HEAD)

    def set_bed_size(self, width_mm: float, height_mm: float) -> None:
        if width_mm > 0 and height_mm > 0:
            self._bed_width_mm = width_mm
            self._bed_height_mm = height_mm
            self.update()

    def set_trigger_point(self, x_mm: float, y_mm: float) -> None:
        self._trigger_point = (x_mm, y_mm)
        self.update()

    def clear_trigger_point(self) -> None:
        self._trigger_point = None
        self._trigger_drag = False
        self.update()

    def get_trigger_point(self) -> tuple[float, float] | None:
        return self._trigger_point

    def set_printer_head(self, x_mm: float, y_mm: float) -> None:
        self._printer_head = (x_mm, y_mm)
        self.update()

    def clear_printer_head(self) -> None:
        self._printer_head = None
        self.update()

    def set_drawing_mode(self, mode: str) -> None:
        self._drawing_mode = mode
        self.clear()
        self._line_start_pixel = None
        self.update()

    def set_line_start(self, pos: QPointF) -> None:
        self._line_start_pixel = pos
        self.update()

    def clear_line_start(self) -> None:
        self._line_start_pixel = None
        self.update()

    def set_layers(self, layers: list[list[QPointF]], colors: list[QColor], selected_index: int | None = None) -> None:
        self._layers = layers
        self._layer_colors = colors
        self._selected_layer_index = selected_index
        self.update()

    def clear_layers(self) -> None:
        self._layers = []
        self._layer_colors = []
        self._selected_layer_index = None
        self.update()

    def set_playback_position(self, nx: float, ny: float) -> None:
        rect = self._print_area_rect()
        self._playback_marker = QPointF(rect.x() + nx * rect.width(), rect.y() + ny * rect.height())
        self.update()

    def clear_playback_marker(self) -> None:
        self._playback_marker = None
        self.update()

    def set_fade_trail_enabled(self, enabled: bool) -> None:
        self._fade_trail_enabled = enabled
        if not enabled:
            self._fade_trail_points = []
        self.update()

    def set_fade_trail_layer_index(self, index: int | None) -> None:
        self._fade_trail_layer_index = index
        self.update()

    def set_fade_trail(self, points: list[QPointF]) -> None:
        self._fade_trail_points = points
        self.update()

    def _print_area_rect(self) -> QRectF:
        w, h = max(self.width(), 1), max(self.height(), 1)
        bed_aspect = self._bed_width_mm / self._bed_height_mm
        widget_aspect = w / h
        if widget_aspect > bed_aspect:
            area_h = h
            area_w = h * bed_aspect
        else:
            area_w = w
            area_h = w / bed_aspect
        x0 = (w - area_w) / 2.0
        y0 = (h - area_h) / 2.0
        return QRectF(x0, y0, area_w, area_h)

    def _normalize(self, pos: QPointF) -> tuple[float, float]:
        rect = self._print_area_rect()
        nx = (pos.x() - rect.x()) / max(rect.width(), 1e-6)
        ny = (pos.y() - rect.y()) / max(rect.height(), 1e-6)
        nx = min(max(nx, 0.0), 1.0)
        ny = min(max(ny, 0.0), 1.0)
        return nx, ny

    def _mm_to_px(self, x_mm: float, y_mm: float) -> QPointF:
        rect = self._print_area_rect()
        nx = x_mm / max(self._bed_width_mm, 1e-6)
        ny = 1.0 - y_mm / max(self._bed_height_mm, 1e-6)
        return QPointF(rect.x() + nx * rect.width(), rect.y() + ny * rect.height())

    def _px_to_mm(self, pos: QPointF) -> tuple[float, float]:
        rect = self._print_area_rect()
        nx = (pos.x() - rect.x()) / max(rect.width(), 1e-6)
        ny = (pos.y() - rect.y()) / max(rect.height(), 1e-6)
        x_mm = nx * self._bed_width_mm
        y_mm = (1.0 - ny) * self._bed_height_mm
        return x_mm, y_mm

    @staticmethod
    def _rotate(lx: float, ly: float, deg: float) -> tuple[float, float]:
        a = math.radians(deg)
        c, s = math.cos(a), math.sin(a)
        return (lx * c - ly * s, lx * s + ly * c)

    def _svg_corners_mm(self, w: float, h: float, rot: float, cx: float, cy: float) -> list[tuple[float, float]]:
        locals_ = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
        return [(cx + self._rotate(lx, ly, rot)[0], cy + self._rotate(lx, ly, rot)[1]) for lx, ly in locals_]

    def _svg_handle_points(self, w: float, h: float, rot: float, cx: float, cy: float) -> tuple[list[QPointF], QPointF]:
        corners_px = [self._mm_to_px(x, y) for x, y in self._svg_corners_mm(w, h, rot, cx, cy)]
        top_mid_px = QPointF((corners_px[0].x() + corners_px[1].x()) / 2, (corners_px[0].y() + corners_px[1].y()) / 2)
        edge_dx = corners_px[1].x() - corners_px[0].x()
        edge_dy = corners_px[1].y() - corners_px[0].y()
        edge_len = math.hypot(edge_dx, edge_dy) or 1.0
        perp_x, perp_y = edge_dy / edge_len, -edge_dx / edge_len
        center_px = self._mm_to_px(cx, cy)
        if (perp_x * (top_mid_px.x() - center_px.x()) + perp_y * (top_mid_px.y() - center_px.y())) < 0:
            perp_x, perp_y = -perp_x, -perp_y
        rotate_handle_px = QPointF(
            top_mid_px.x() + perp_x * self._rotate_handle_offset_px,
            top_mid_px.y() + perp_y * self._rotate_handle_offset_px,
        )
        return corners_px, rotate_handle_px

    def start_svg_preview(
        self, subpaths: list[list[tuple[float, float]]], width_mm: float, height_mm: float,
        rotation_deg: float, center_x_mm: float, center_y_mm: float,
    ) -> None:
        xs = [p[0] for sp in subpaths for p in sp]
        ys = [p[1] for sp in subpaths for p in sp]
        self._svg_preview = {
            "subpaths": subpaths,
            "bbox": (min(xs), min(ys), max(xs), max(ys)),
            "w": width_mm, "h": height_mm, "rot": rotation_deg, "cx": center_x_mm, "cy": center_y_mm,
        }
        self._svg_drag = None
        self.update()

    def update_svg_transform(
        self, width_mm: float | None = None, height_mm: float | None = None,
        rotation_deg: float | None = None, center_x_mm: float | None = None, center_y_mm: float | None = None,
    ) -> None:
        if self._svg_preview is None:
            return
        if width_mm is not None:
            self._svg_preview["w"] = max(width_mm, 1.0)
        if height_mm is not None:
            self._svg_preview["h"] = max(height_mm, 1.0)
        if rotation_deg is not None:
            self._svg_preview["rot"] = rotation_deg
        if center_x_mm is not None:
            self._svg_preview["cx"] = center_x_mm
        if center_y_mm is not None:
            self._svg_preview["cy"] = center_y_mm
        self.update()

    def set_svg_lock_aspect(self, locked: bool) -> None:
        self._svg_lock_aspect = locked

    def get_svg_transform(self) -> tuple[float, float, float, float, float] | None:
        p = self._svg_preview
        if p is None:
            return None
        return (p["w"], p["h"], p["rot"], p["cx"], p["cy"])

    def get_svg_preview_subpaths(self) -> list[list[tuple[float, float]]] | None:
        return None if self._svg_preview is None else self._svg_preview["subpaths"]

    def has_svg_preview(self) -> bool:
        return self._svg_preview is not None

    def clear_svg_preview(self) -> None:
        self._svg_preview = None
        self._svg_drag = None
        self.update()


    def set_phantom_box(
        self, width_mm: float, height_mm: float, center_x_mm: float, center_y_mm: float
    ) -> None:
        self._phantom = {
            "w": max(width_mm, 1.0), "h": max(height_mm, 1.0),
            "cx": center_x_mm, "cy": center_y_mm,
        }
        self.update()

    def clear_phantom_box(self) -> None:
        self._phantom = None
        self._phantom_drag = None
        self.update()

    def set_phantom_box_editable(self, editable: bool) -> None:
        self._phantom_editable = editable
        self._phantom_drag = None
        self.update()

    def get_phantom_box(self) -> tuple[float, float, float, float] | None:
        p = self._phantom
        return None if p is None else (p["w"], p["h"], p["cx"], p["cy"])

    def has_phantom_box(self) -> bool:
        return self._phantom is not None

    def _phantom_corners_px(self) -> list[QPointF]:
        p = self._phantom
        hw, hh = p["w"] / 2, p["h"] / 2
        return [
            self._mm_to_px(p["cx"] - hw, p["cy"] + hh),
            self._mm_to_px(p["cx"] + hw, p["cy"] + hh),
            self._mm_to_px(p["cx"] + hw, p["cy"] - hh),
            self._mm_to_px(p["cx"] - hw, p["cy"] - hh),
        ]

    def _phantom_mouse_press(self, pos: QPointF) -> bool:
        p = self._phantom
        corners = self._phantom_corners_px()
        for idx, c in enumerate(corners):
            if math.hypot(c.x() - pos.x(), c.y() - pos.y()) <= self._handle_radius_px + 3:
                self._phantom_drag = {"mode": "resize", "corner": idx, "start": dict(p)}
                return True
        mx, my = self._px_to_mm(pos)
        if abs(mx - p["cx"]) <= p["w"] / 2 and abs(my - p["cy"]) <= p["h"] / 2:
            self._phantom_drag = {"mode": "move", "start": dict(p),
                                  "start_mouse_mm": (mx, my)}
            return True
        return False

    def _phantom_mouse_move(self, pos: QPointF) -> None:
        p, drag = self._phantom, self._phantom_drag
        if p is None or drag is None:
            return
        start = drag["start"]
        mx, my = self._px_to_mm(pos)
        if drag["mode"] == "move":
            smx, smy = drag["start_mouse_mm"]
            p["cx"] = start["cx"] + (mx - smx)
            p["cy"] = start["cy"] + (my - smy)
        else:
            hw, hh = start["w"] / 2, start["h"] / 2
            anchors = [
                (start["cx"] + hw, start["cy"] - hh),
                (start["cx"] - hw, start["cy"] - hh),
                (start["cx"] - hw, start["cy"] + hh),
                (start["cx"] + hw, start["cy"] + hh),
            ]
            ax, ay = anchors[drag["corner"]]
            new_w = max(abs(mx - ax), 1.0)
            new_h = max(abs(my - ay), 1.0)
            if self._svg_lock_aspect and start["w"] > 0 and start["h"] > 0:
                aspect = start["h"] / start["w"]
                if new_w * aspect >= new_h:
                    new_h = new_w * aspect
                else:
                    new_w = new_h / aspect
            p["w"], p["h"] = new_w, new_h
            p["cx"] = ax + (new_w / 2 if mx >= ax else -new_w / 2)
            p["cy"] = ay + (new_h / 2 if my >= ay else -new_h / 2)
        self.update()
        self.phantom_box_changed.emit()

    def _paint_phantom_box(self, painter: QPainter) -> None:
        p = self._phantom
        corners = self._phantom_corners_px()
        col = QColor(self._phantom_color)

        fill = QColor(col)
        fill.setAlpha(18)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRect(QRectF(corners[0], corners[2]).normalized())

        pen = QPen(col, 1.6, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(4):
            painter.drawLine(corners[i], corners[(i + 1) % 4])

        label = f"Tablet {p['w']:.0f} × {p['h']:.0f} mm"
        painter.setPen(QPen(col))
        painter.drawText(QPointF(corners[0].x() + 4, corners[0].y() - 5), label)

        if self._phantom_editable:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(col)
            for c in corners:
                painter.drawEllipse(c, self._handle_radius_px, self._handle_radius_px)

    def start_edit(
        self, points_mm: list[tuple[float, float]], mode: str = "transform",
        layer_index: int | None = None,
        outlier_indices: set[int] | None = None,
    ) -> None:
        if not points_mm:
            return
        pts = [(float(x), float(y)) for x, y in points_mm]
        self._edit = {
            "orig": list(pts),
            "pts": list(pts),
            "ids": list(range(len(pts))),
            "mode": mode if mode in ("transform", "points") else "transform",
            "sel": set(),
            "layer_index": layer_index,
            "outliers": outlier_indices or set(),
        }
        self._reset_edit_box()
        self._edit_drag = None
        self.update()

    def _reset_edit_box(self) -> None:
        e = self._edit
        if e is None:
            return
        e["orig"] = list(e["pts"])
        xs = [p[0] for p in e["pts"]]
        ys = [p[1] for p in e["pts"]]
        minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
        e["bbox"] = (minx, miny, maxx, maxy)
        e["w"] = max(maxx - minx, 1e-6)
        e["h"] = max(maxy - miny, 1e-6)
        e["cx"] = (minx + maxx) / 2
        e["cy"] = (miny + maxy) / 2
        e["rot"] = 0.0

    def set_edit_mode(self, mode: str) -> None:
        if self._edit is None or mode not in ("transform", "points"):
            return
        if self._edit["mode"] == mode:
            return
        self._edit["mode"] = mode
        self._reset_edit_box()
        self._edit_drag = None
        self.update()
        self.edit_changed.emit()

    def has_edit(self) -> bool:
        return self._edit is not None

    def edit_mode(self) -> str | None:
        return None if self._edit is None else self._edit["mode"]

    def get_edit_points(self) -> list[tuple[float, float]] | None:
        return None if self._edit is None else list(self._edit["pts"])

    def get_edit_ids(self) -> list[int] | None:
        return None if self._edit is None else list(self._edit["ids"])

    def get_edit_transform(self) -> tuple[float, float, float, float, float] | None:
        e = self._edit
        return None if e is None else (e["w"], e["h"], e["rot"], e["cx"], e["cy"])

    def update_edit_transform(
        self, width_mm: float | None = None, height_mm: float | None = None,
        rotation_deg: float | None = None, center_x_mm: float | None = None,
        center_y_mm: float | None = None,
    ) -> None:
        e = self._edit
        if e is None:
            return
        if width_mm is not None:
            e["w"] = max(width_mm, 0.1)
        if height_mm is not None:
            e["h"] = max(height_mm, 0.1)
        if rotation_deg is not None:
            e["rot"] = rotation_deg
        if center_x_mm is not None:
            e["cx"] = center_x_mm
        if center_y_mm is not None:
            e["cy"] = center_y_mm
        self._apply_edit_transform()

    def edit_selection_count(self) -> int:
        return 0 if self._edit is None else len(self._edit["sel"])

    def edit_point_count(self) -> int:
        return 0 if self._edit is None else len(self._edit["pts"])

    def select_all_edit_points(self) -> None:
        if self._edit is None:
            return
        self._edit["sel"] = set(range(len(self._edit["pts"])))
        self.update()
        self.edit_changed.emit()

    def clear_edit_selection(self) -> None:
        if self._edit is None:
            return
        self._edit["sel"] = set()
        self.update()
        self.edit_changed.emit()

    def delete_selected_points(self) -> int:
        e = self._edit
        if e is None or not e["sel"]:
            return 0
        keep = [i for i in range(len(e["pts"])) if i not in e["sel"]]
        if len(keep) < 2:
            return 0
        removed = len(e["pts"]) - len(keep)
        e["pts"] = [e["pts"][i] for i in keep]
        e["ids"] = [e["ids"][i] for i in keep]
        e["sel"] = set()
        self._reset_edit_box()
        self.update()
        self.edit_changed.emit()
        return removed

    def end_edit(self) -> None:
        self._edit = None
        self._edit_drag = None
        self.update()

    def _apply_edit_transform(self) -> None:
        e = self._edit
        if e is None:
            return
        minx, miny, maxx, maxy = e["bbox"]
        sx = e["w"] / max(maxx - minx, 1e-6)
        sy = e["h"] / max(maxy - miny, 1e-6)
        rot = e["rot"]
        cx, cy = e["cx"], e["cy"]
        out = []
        for x, y in e["orig"]:
            lx = (x - minx) * sx - e["w"] / 2
            ly = (y - miny) * sy - e["h"] / 2
            rx, ry = self._rotate(lx, ly, rot)
            out.append((cx + rx, cy + ry))
        e["pts"] = out
        self.update()
        self.edit_changed.emit()

    def _edit_mouse_press(self, pos: QPointF, shift: bool) -> None:
        e = self._edit
        if e["mode"] == "transform":
            self._edit_box_press(pos)
            return

        best, best_d = None, self._point_hit_radius_px + 3
        for i, (x, y) in enumerate(e["pts"]):
            p = self._mm_to_px(x, y)
            d = math.hypot(p.x() - pos.x(), p.y() - pos.y())
            if d <= best_d:
                best, best_d = i, d

        if best is not None:
            if shift:
                e["sel"].symmetric_difference_update({best})
            elif best not in e["sel"]:
                e["sel"] = {best}
            mx, my = self._px_to_mm(pos)
            self._edit_drag = {
                "mode": "move_points", "start_mouse_mm": (mx, my),
                "start_pts": {i: e["pts"][i] for i in e["sel"]},
            }
        else:
            if not shift:
                e["sel"] = set()
            self._edit_drag = {"mode": "rubber", "start_px": pos, "cur_px": pos,
                               "base": set(e["sel"])}
        self.update()
        self.edit_changed.emit()

    def _edit_box_press(self, pos: QPointF) -> None:
        e = self._edit
        w, h, rot, cx, cy = e["w"], e["h"], e["rot"], e["cx"], e["cy"]
        corners_px, rotate_handle_px = self._svg_handle_points(w, h, rot, cx, cy)

        def dist(a: QPointF, b: QPointF) -> float:
            return math.hypot(a.x() - b.x(), a.y() - b.y())

        if dist(pos, rotate_handle_px) <= self._handle_radius_px + 3:
            mx, my = self._px_to_mm(pos)
            self._edit_drag = {
                "mode": "rotate", "start": self._edit_box_state(),
                "start_angle": math.degrees(math.atan2(my - cy, mx - cx)),
            }
            return
        for idx, corner_px in enumerate(corners_px):
            if dist(pos, corner_px) <= self._handle_radius_px + 3:
                self._edit_drag = {"mode": "resize", "corner": idx,
                                   "start": self._edit_box_state()}
                return
        mx, my = self._px_to_mm(pos)
        lx, ly = self._rotate(mx - cx, my - cy, -rot)
        if abs(lx) <= w / 2 and abs(ly) <= h / 2:
            self._edit_drag = {"mode": "move", "start": self._edit_box_state(),
                               "start_mouse_mm": (mx, my)}

    def _edit_box_state(self) -> dict:
        e = self._edit
        return {k: e[k] for k in ("w", "h", "rot", "cx", "cy")}

    def _edit_mouse_move(self, pos: QPointF) -> None:
        e, drag = self._edit, self._edit_drag
        if e is None or drag is None:
            return
        mx, my = self._px_to_mm(pos)

        if drag["mode"] == "rubber":
            drag["cur_px"] = pos
            x0, x1 = sorted((drag["start_px"].x(), pos.x()))
            y0, y1 = sorted((drag["start_px"].y(), pos.y()))
            inside = set()
            for i, (x, y) in enumerate(e["pts"]):
                p = self._mm_to_px(x, y)
                if x0 <= p.x() <= x1 and y0 <= p.y() <= y1:
                    inside.add(i)
            e["sel"] = drag["base"] | inside
            self.update()
            self.edit_changed.emit()
            return

        if drag["mode"] == "move_points":
            smx, smy = drag["start_mouse_mm"]
            dx, dy = mx - smx, my - smy
            for i, (ox, oy) in drag["start_pts"].items():
                e["pts"][i] = (ox + dx, oy + dy)
            self.update()
            self.edit_changed.emit()
            return

        start = drag["start"]
        if drag["mode"] == "move":
            smx, smy = drag["start_mouse_mm"]
            e["cx"] = start["cx"] + (mx - smx)
            e["cy"] = start["cy"] + (my - smy)
        elif drag["mode"] == "rotate":
            angle_now = math.degrees(math.atan2(my - start["cy"], mx - start["cx"]))
            e["rot"] = start["rot"] + (angle_now - drag["start_angle"])
        elif drag["mode"] == "resize":
            corner = drag["corner"]
            anchor_idx = (corner + 2) % 4
            anchor_mm = self._svg_corners_mm(
                start["w"], start["h"], start["rot"], start["cx"], start["cy"]
            )[anchor_idx]
            sign_x = 1.0 if corner in (1, 2) else -1.0
            sign_y = 1.0 if corner in (2, 3) else -1.0
            dx, dy = mx - anchor_mm[0], my - anchor_mm[1]
            ex = self._rotate(1.0, 0.0, start["rot"])
            ey = self._rotate(0.0, 1.0, start["rot"])
            comp_x = sign_x * (dx * ex[0] + dy * ex[1])
            comp_y = sign_y * (dx * ey[0] + dy * ey[1])
            min_size = 2.0
            if self._svg_lock_aspect and start["w"] > 0 and start["h"] > 0:
                start_diag = math.hypot(start["w"], start["h"])
                scale = max(math.hypot(comp_x, comp_y) / start_diag,
                            min_size / max(start["w"], start["h"]))
                new_w, new_h = start["w"] * scale, start["h"] * scale
            else:
                new_w, new_h = max(comp_x, min_size), max(comp_y, min_size)
            rx, ry = self._rotate(sign_x * new_w / 2, sign_y * new_h / 2, start["rot"])
            e["w"], e["h"] = new_w, new_h
            e["cx"], e["cy"] = anchor_mm[0] + rx, anchor_mm[1] + ry
        self._apply_edit_transform()

    def keyPressEvent(self, event) -> None:
        if self._edit is not None and self._edit["mode"] == "points":
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self.delete_selected_points()
                return
            if event.key() == Qt.Key.Key_A and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.select_all_edit_points()
                return
            if event.key() == Qt.Key.Key_Escape:
                self.clear_edit_selection()
                return
        super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._edit is not None:
            if event.button() == Qt.MouseButton.LeftButton:
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                self._edit_mouse_press(
                    event.position(),
                    bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier),
                )
            return
        if self._svg_preview is not None:
            if event.button() == Qt.MouseButton.LeftButton:
                self._svg_mouse_press(event.position())
            return
        if (
            self._phantom is not None
            and self._phantom_editable
            and event.button() == Qt.MouseButton.LeftButton
            and self._phantom_mouse_press(event.position())
        ):
            return
        if (
            self._trigger_point is not None
            and event.button() == Qt.MouseButton.LeftButton
            and self._trigger_hit_test(event.position())
        ):
            self._trigger_drag = True
            return
        if event.button() == Qt.MouseButton.LeftButton:
            nx, ny = self._normalize(event.position())
            if self._drawing_mode == "freehand":
                self._is_drawing = True
                rect = self._print_area_rect()
                clamped = QPointF(rect.x() + nx * rect.width(), rect.y() + ny * rect.height())
                self._live_path = [clamped]
                self._live_path_overspeed = [False]
                self.update()
            self.mouse_pressed.emit(nx, ny)
        super().mousePressEvent(event)

    def _trigger_hit_test(self, pos: QPointF) -> bool:
        if self._trigger_point is None:
            return False
        tp = self._mm_to_px(self._trigger_point[0], self._trigger_point[1])
        dx = pos.x() - tp.x()
        dy = pos.y() - tp.y()
        return (dx * dx + dy * dy) <= (self._trigger_radius_px * 2) ** 2

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._edit is not None:
            if self._edit_drag is not None:
                self._edit_mouse_move(event.position())
            return
        if self._svg_preview is not None:
            if self._svg_drag is not None:
                self._svg_mouse_move(event.position())
            return
        if self._phantom_drag is not None:
            self._phantom_mouse_move(event.position())
            return
        if self._trigger_drag:
            x_mm, y_mm = self._px_to_mm(event.position())
            x_mm = max(0.0, min(x_mm, self._bed_width_mm))
            y_mm = max(0.0, min(y_mm, self._bed_height_mm))
            self._trigger_point = (x_mm, y_mm)
            self.update()
            return
        nx, ny = self._normalize(event.position())
        if self._drawing_mode == "freehand" and self._is_drawing:
            rect = self._print_area_rect()
            clamped = QPointF(rect.x() + nx * rect.width(), rect.y() + ny * rect.height())
            self._live_path.append(clamped)
            self._live_path_overspeed.append(False)
            if len(self._live_path) > self._max_live_points:
                self._live_path.pop(0)
                self._live_path_overspeed.pop(0)
            self.update()
        self.mouse_moved.emit(nx, ny)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._edit is not None:
            if event.button() == Qt.MouseButton.LeftButton:
                if self._edit_drag and self._edit_drag["mode"] == "move_points":
                    self._reset_edit_box()
                self._edit_drag = None
                self.update()
                self.edit_changed.emit()
            return
        if self._svg_preview is not None:
            if event.button() == Qt.MouseButton.LeftButton:
                self._svg_drag = None
            return
        if self._phantom_drag is not None:
            if event.button() == Qt.MouseButton.LeftButton:
                self._phantom_drag = None
            return
        if self._trigger_drag and event.button() == Qt.MouseButton.LeftButton:
            self._trigger_drag = False
            if self._trigger_point is not None:
                self.trigger_point_changed.emit(self._trigger_point[0], self._trigger_point[1])
            return
        if event.button() == Qt.MouseButton.LeftButton:
            if self._drawing_mode == "freehand":
                self._is_drawing = False
            nx, ny = self._normalize(event.position())
            self.mouse_released.emit(nx, ny)
        super().mouseReleaseEvent(event)

    def _svg_mouse_press(self, pos: QPointF) -> None:
        p = self._svg_preview
        w, h, rot, cx, cy = p["w"], p["h"], p["rot"], p["cx"], p["cy"]
        corners_px, rotate_handle_px = self._svg_handle_points(w, h, rot, cx, cy)

        def dist(a: QPointF, b: QPointF) -> float:
            return math.hypot(a.x() - b.x(), a.y() - b.y())

        if dist(pos, rotate_handle_px) <= self._handle_radius_px + 3:
            mx, my = self._px_to_mm(pos)
            self._svg_drag = {
                "mode": "rotate", "start": dict(p), "start_angle": math.degrees(math.atan2(my - cy, mx - cx)),
            }
            return

        for idx, corner_px in enumerate(corners_px):
            if dist(pos, corner_px) <= self._handle_radius_px + 3:
                self._svg_drag = {"mode": "resize", "corner": idx, "start": dict(p)}
                return

        mx, my = self._px_to_mm(pos)
        lx, ly = self._rotate(mx - cx, my - cy, -rot)
        if abs(lx) <= w / 2 and abs(ly) <= h / 2:
            self._svg_drag = {"mode": "move", "start": dict(p), "start_mouse_mm": (mx, my)}

    def _svg_mouse_move(self, pos: QPointF) -> None:
        drag = self._svg_drag
        p = self._svg_preview
        start = drag["start"]
        mx, my = self._px_to_mm(pos)

        if drag["mode"] == "move":
            smx, smy = drag["start_mouse_mm"]
            p["cx"] = start["cx"] + (mx - smx)
            p["cy"] = start["cy"] + (my - smy)

        elif drag["mode"] == "rotate":
            angle_now = math.degrees(math.atan2(my - start["cy"], mx - start["cx"]))
            p["rot"] = start["rot"] + (angle_now - drag["start_angle"])

        elif drag["mode"] == "resize":
            corner = drag["corner"]
            anchor_idx = (corner + 2) % 4
            anchor_mm = self._svg_corners_mm(start["w"], start["h"], start["rot"], start["cx"], start["cy"])[anchor_idx]
            sign_x = 1.0 if corner in (1, 2) else -1.0
            sign_y = 1.0 if corner in (2, 3) else -1.0
            dx, dy = mx - anchor_mm[0], my - anchor_mm[1]
            ex = self._rotate(1.0, 0.0, start["rot"])
            ey = self._rotate(0.0, 1.0, start["rot"])
            comp_x = sign_x * (dx * ex[0] + dy * ex[1])
            comp_y = sign_y * (dx * ey[0] + dy * ey[1])
            min_size = 2.0
            if self._svg_lock_aspect and start["w"] > 0 and start["h"] > 0:
                start_diag = math.hypot(start["w"], start["h"])
                scale = max(math.hypot(comp_x, comp_y) / start_diag, min_size / max(start["w"], start["h"]))
                new_w, new_h = start["w"] * scale, start["h"] * scale
            else:
                new_w, new_h = max(comp_x, min_size), max(comp_y, min_size)
            rx, ry = self._rotate(sign_x * new_w / 2, sign_y * new_h / 2, start["rot"])
            p["w"], p["h"] = new_w, new_h
            p["cx"], p["cy"] = anchor_mm[0] + rx, anchor_mm[1] + ry

        self.update()
        self.svg_transform_changed.emit()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.update()
        self.resized.emit()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(theme.CANVAS_OUTSIDE))

        rect = self._print_area_rect()
        painter.fillRect(rect, self._bg_color)
        self._draw_grid(painter, rect)

        border_pen = QPen(self._area_border_color, 1.5)
        painter.setPen(border_pen)
        painter.drawRect(rect)

        if self._phantom is not None:
            self._paint_phantom_box(painter)

        editing_idx = self._edit["layer_index"] if self._edit is not None else None
        for idx, layer_pts in enumerate(self._layers):
            if len(layer_pts) < 2:
                continue
            if idx == editing_idx:
                continue
            if (
                self._fade_trail_enabled
                and self._fade_trail_layer_index is not None
                and idx == self._fade_trail_layer_index
            ):
                continue
            color = self._layer_colors[idx] if idx < len(self._layer_colors) else QColor(theme.TEXT_MUTED)
            pen_width = 3.0 if self._selected_layer_index == idx else 1.5
            pen = QPen(color, pen_width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            for i in range(1, len(layer_pts)):
                painter.drawLine(layer_pts[i - 1], layer_pts[i])

        if self._fade_trail_enabled and len(self._fade_trail_points) > 1:
            n = len(self._fade_trail_points)
            for i in range(1, n):
                alpha = int(255 * (i / (n - 1)) ** 1.5) if n > 1 else 255
                color = QColor(self._fade_trail_color)
                color.setAlpha(max(alpha, 0))
                pen = QPen(color, 2.5)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.drawLine(self._fade_trail_points[i - 1], self._fade_trail_points[i])

        if len(self._live_path) > 1:
            normal_pen = QPen(self._live_color, 2.0)
            normal_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            normal_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            overspeed_pen = QPen(self._overspeed_color, 2.5)
            overspeed_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            overspeed_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            for i in range(1, len(self._live_path)):
                is_overspeed = i < len(self._live_path_overspeed) and self._live_path_overspeed[i]
                painter.setPen(overspeed_pen if is_overspeed else normal_pen)
                painter.drawLine(self._live_path[i - 1], self._live_path[i])

        if self._trigger_point is not None:
            tx, ty = self._trigger_point
            tp = self._mm_to_px(tx, ty)
            r = self._trigger_radius_px
            painter.setPen(QPen(self._trigger_color, 2.0))
            painter.setBrush(QColor(self._trigger_color.red(), self._trigger_color.green(),
                                    self._trigger_color.blue(), 80))
            diamond = [QPointF(tp.x(), tp.y() - r), QPointF(tp.x() + r, tp.y()),
                       QPointF(tp.x(), tp.y() + r), QPointF(tp.x() - r, tp.y())]
            painter.drawPolygon(QPolygonF(diamond))
            painter.drawLine(QPointF(tp.x() - r * 1.5, tp.y()), QPointF(tp.x() + r * 1.5, tp.y()))
            painter.drawLine(QPointF(tp.x(), tp.y() - r * 1.5), QPointF(tp.x(), tp.y() + r * 1.5))

        if self._printer_head is not None:
            hx, hy = self._printer_head
            hp = self._mm_to_px(hx, hy)
            r = 10.0
            pen = QPen(self._printer_head_color, 2.0)
            painter.setPen(pen)
            painter.setBrush(QColor(self._printer_head_color.red(),
                                    self._printer_head_color.green(),
                                    self._printer_head_color.blue(), 50))
            painter.drawEllipse(hp, r, r)
            painter.drawLine(QPointF(hp.x() - r * 1.4, hp.y()), QPointF(hp.x() + r * 1.4, hp.y()))
            painter.drawLine(QPointF(hp.x(), hp.y() - r * 1.4), QPointF(hp.x(), hp.y() + r * 1.4))

        if self._playback_marker is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._playback_color)
            painter.drawEllipse(self._playback_marker, 6, 6)

        if self._line_start_pixel is not None:
            painter.setPen(QPen(self._crosshair_color, 1.5))
            cx = self._line_start_pixel.x()
            cy = self._line_start_pixel.y()
            size = 8
            painter.drawLine(QPointF(cx - size, cy), QPointF(cx + size, cy))
            painter.drawLine(QPointF(cx, cy - size), QPointF(cx, cy + size))
            painter.drawEllipse(self._line_start_pixel, 4, 4)

        if self._svg_preview is not None:
            self._paint_svg_preview(painter)

        if self._edit is not None:
            self._paint_edit(painter)

        painter.end()

    def _paint_edit(self, painter: QPainter) -> None:
        e = self._edit
        pts = e["pts"]

        pen = QPen(self._edit_box_color, 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        px = [self._mm_to_px(x, y) for x, y in pts]
        for i in range(1, len(px)):
            painter.drawLine(px[i - 1], px[i])

        if e["mode"] == "transform":
            corners_px, rotate_px = self._svg_handle_points(
                e["w"], e["h"], e["rot"], e["cx"], e["cy"]
            )
            box_pen = QPen(self._edit_box_color, 1.2, Qt.PenStyle.DashLine)
            painter.setPen(box_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for i in range(4):
                painter.drawLine(corners_px[i], corners_px[(i + 1) % 4])
            top_mid = QPointF((corners_px[0].x() + corners_px[1].x()) / 2,
                              (corners_px[0].y() + corners_px[1].y()) / 2)
            painter.drawLine(top_mid, rotate_px)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._edit_box_color)
            for c in corners_px:
                painter.drawEllipse(c, self._handle_radius_px, self._handle_radius_px)
            painter.setBrush(QColor(theme.ROTATE_HANDLE))
            painter.drawEllipse(rotate_px, self._handle_radius_px, self._handle_radius_px)
            return

        step = max(1, len(px) // 4000)
        outliers = e.get("outliers", set())
        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(0, len(px), step):
            selected = i in e["sel"]
            orig_idx = e["ids"][i] if i < len(e["ids"]) else i
            is_outlier = orig_idx in outliers
            if selected:
                painter.setBrush(self._edit_sel_color)
            elif is_outlier:
                painter.setBrush(self._edit_outlier_color)
            else:
                painter.setBrush(self._edit_point_color)
            r = 5.0 if is_outlier else (4.0 if selected else 2.2)
            painter.drawEllipse(px[i], r, r)
        painter.setBrush(self._edit_sel_color)
        for i in e["sel"]:
            if i % step and i < len(px):
                painter.drawEllipse(px[i], 4.0, 4.0)

        if self._edit_drag is not None and self._edit_drag["mode"] == "rubber":
            a, b = self._edit_drag["start_px"], self._edit_drag["cur_px"]
            painter.setPen(QPen(self._edit_sel_color, 1.0, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(a, b).normalized())

    def _paint_svg_preview(self, painter: QPainter) -> None:
        p = self._svg_preview
        w, h, rot, cx, cy = p["w"], p["h"], p["rot"], p["cx"], p["cy"]
        min_x, min_y, max_x, max_y = p["bbox"]
        src_w = max(max_x - min_x, 1e-6)
        src_h = max(max_y - min_y, 1e-6)
        sx, sy = w / src_w, h / src_h

        preview_pen = QPen(QColor(theme.SVG_PREVIEW), 1.5)
        preview_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(preview_pen)
        for sp in p["subpaths"]:
            pts_px = []
            for px, py in sp:
                lx = (px - min_x) * sx - w / 2
                ly = (py - min_y) * sy - h / 2
                rx, ry = self._rotate(lx, ly, rot)
                pts_px.append(self._mm_to_px(cx + rx, cy + ry))
            for i in range(1, len(pts_px)):
                painter.drawLine(pts_px[i - 1], pts_px[i])

        corners_px, rotate_handle_px = self._svg_handle_points(w, h, rot, cx, cy)
        box_pen = QPen(QColor(theme.SVG_BOX), 1.5, Qt.PenStyle.DashLine)
        painter.setPen(box_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(4):
            painter.drawLine(corners_px[i], corners_px[(i + 1) % 4])

        painter.setPen(QPen(QColor(theme.SVG_BOX), 1.0))
        painter.setBrush(QColor(theme.SVG_HANDLE_FILL))
        for c in corners_px:
            painter.drawRect(QRectF(c.x() - self._handle_radius_px, c.y() - self._handle_radius_px,
                                     self._handle_radius_px * 2, self._handle_radius_px * 2))

        painter.setPen(QPen(QColor(theme.SVG_BOX), 1.5))
        painter.drawLine(QPointF((corners_px[0].x() + corners_px[1].x()) / 2,
                                  (corners_px[0].y() + corners_px[1].y()) / 2), rotate_handle_px)
        painter.setBrush(QColor(theme.SVG_BOX))
        painter.drawEllipse(rotate_handle_px, self._handle_radius_px, self._handle_radius_px)

    def _draw_grid(self, painter: QPainter, rect: QRectF, step: int = 40) -> None:
        pen = QPen(self._grid_color, 1.0)
        painter.setPen(pen)
        x = rect.left()
        while x < rect.right():
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            x += step
        y = rect.top()
        while y < rect.bottom():
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            y += step

    def mark_last_segment_overspeed(self) -> None:
        if self._live_path_overspeed:
            self._live_path_overspeed[-1] = True
            self.update()

    def clear(self) -> None:
        self._live_path = []
        self._live_path_overspeed = []
        self.update()
