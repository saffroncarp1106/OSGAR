# Verify layer editor transform correctness
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path

PROJ = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, PROJ)

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPointF

from gui.canvas import DrawingCanvas
from gui.layer_edit_panel import LayerEditPanel
from storage.osr_importer import parse_osr, place_osr_on_bed

app = QApplication([])
ok = True


def check(label, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(f"   [{'PASS' if cond else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))


canvas = DrawingCanvas()
canvas.resize(800, 800)
canvas.set_bed_size(420.0, 420.0)

doc = parse_osr(os.path.join(PROJ, "BackandForthWithClicking.osr"))
rec = place_osr_on_bed(doc, 200.0, 150.0, 0.0, 210.0, 210.0)
orig = [(p.x_mm, p.y_mm) for p in rec.points]
orig_times = [p.t_s for p in rec.points]
print(f"layer: {len(rec.points)} points, {rec.duration_s:.3f} s")

print("\n1. start_edit seeds an identity transform")
canvas.start_edit(orig, mode="transform")
w, h, rot, cx, cy = canvas.get_edit_transform()
pts = canvas.get_edit_points()
check("point count preserved", len(pts) == len(orig))
check("identity transform", max(
    max(abs(a[0] - b[0]), abs(a[1] - b[1])) for a, b in zip(pts, orig)) < 1e-9,
    f"w={w:.1f} h={h:.1f} rot={rot:.1f}")

print("\n2. move: shifting the box translates every point equally")
canvas.update_edit_transform(center_x_mm=cx + 30.0, center_y_mm=cy - 12.0)
pts = canvas.get_edit_points()
deltas = {(round(p[0] - o[0], 6), round(p[1] - o[1], 6)) for p, o in zip(pts, orig)}
check("single uniform delta", len(deltas) == 1, str(deltas))
check("delta is the requested one", deltas == {(30.0, -12.0)})

print("\n3. scale: 2x width/height doubles every pairwise distance")
canvas.update_edit_transform(center_x_mm=cx, center_y_mm=cy)
canvas.update_edit_transform(width_mm=w * 2, height_mm=h * 2)
pts = canvas.get_edit_points()
ratios = []
for i in range(1, min(len(pts), 400)):
    d0 = math.hypot(orig[i][0] - orig[i - 1][0], orig[i][1] - orig[i - 1][1])
    d1 = math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
    if d0 > 1e-9:
        ratios.append(d1 / d0)
check("all distances scaled 2x", max(abs(r - 2.0) for r in ratios) < 1e-9,
      f"min={min(ratios):.6f} max={max(ratios):.6f}")

print("\n4. rotate: 90 degrees preserves every pairwise distance")
canvas.update_edit_transform(width_mm=w, height_mm=h, rotation_deg=90.0)
pts = canvas.get_edit_points()
worst = 0.0
for i in range(1, min(len(pts), 400)):
    d0 = math.hypot(orig[i][0] - orig[i - 1][0], orig[i][1] - orig[i - 1][1])
    d1 = math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
    worst = max(worst, abs(d1 - d0))
check("rigid rotation", worst < 1e-6, f"worst distance change {worst:.2e} mm")

print("\n5. switching to points mode bakes the transform")
canvas.set_edit_mode("points")
baked = canvas.get_edit_points()
w2, h2, rot2, _, _ = canvas.get_edit_transform()
check("geometry unchanged by the mode switch",
      max(max(abs(a[0] - b[0]), abs(a[1] - b[1])) for a, b in zip(baked, pts)) < 1e-9)
check("box reset to identity", abs(rot2) < 1e-9, f"rot={rot2}")

print("\n6. point selection + move")
canvas.select_all_edit_points()
check("select all", canvas.edit_selection_count() == len(baked))
canvas.clear_edit_selection()
check("deselect", canvas.edit_selection_count() == 0)
p0_px = canvas._mm_to_px(*baked[0])
canvas._edit_mouse_press(p0_px, shift=False)
check("clicking a point selects it", canvas.edit_selection_count() == 1)
target = canvas._mm_to_px(baked[0][0] + 10.0, baked[0][1] + 10.0)
canvas._edit_mouse_move(target)
moved = canvas.get_edit_points()
check("dragged point moved", math.hypot(moved[0][0] - baked[0][0] - 10.0,
                                        moved[0][1] - baked[0][1] - 10.0) < 0.5,
      f"({moved[0][0]-baked[0][0]:.2f}, {moved[0][1]-baked[0][1]:.2f}) mm")
check("other points untouched",
      max(max(abs(a[0] - b[0]), abs(a[1] - b[1]))
          for a, b in zip(moved[1:], baked[1:])) < 1e-9)

print("\n7. delete selected points")
canvas._edit_drag = None
before = canvas.edit_point_count()
canvas.clear_edit_selection()
canvas._edit["sel"] = {5, 6, 7}
removed = canvas.delete_selected_points()
ids = canvas.get_edit_ids()
check("three points removed", removed == 3 and canvas.edit_point_count() == before - 3)
check("ids skip the deleted originals", 5 not in ids and 6 not in ids and 7 not in ids)
check("ids stay sorted/unique", ids == sorted(set(ids)))

print("\n8. refuses to leave fewer than two points")
canvas.select_all_edit_points()
check("delete-all refused", canvas.delete_selected_points() == 0,
      f"still {canvas.edit_point_count()} points")

print("\n9. apply writes back, preserving timestamps")
canvas.clear_edit_selection()
final_pts = canvas.get_edit_points()
final_ids = canvas.get_edit_ids()
kept = []
for (x_mm, y_mm), src_i in zip(final_pts, final_ids):
    src = rec.points[src_i]
    src.x_mm, src.y_mm = x_mm, y_mm
    kept.append(src)
rec.points = kept
check("recording shortened by the deletions", len(rec.points) == len(orig) - 3)
check("timestamps are the originals minus deleted",
      [p.t_s for p in rec.points] == [t for i, t in enumerate(orig_times) if i not in (5, 6, 7)])
check("timestamps still ascending",
      all(b.t_s >= a.t_s for a, b in zip(rec.points, rec.points[1:])))

print("\n10. panel wiring")
panel = LayerEditPanel()
seen = []
panel.mode_changed.connect(seen.append)
panel.points_radio.setChecked(True)
check("mode signal fires", seen == ["points"], str(seen))
check("point buttons shown in points mode", panel._point_row.isVisibleTo(panel))
vals = []
panel.values_changed.connect(lambda *a: vals.append(a))
panel.transform_radio.setChecked(True)
panel.width_spin.setValue(123.0)
check("values signal fires", vals and abs(vals[-1][0] - 123.0) < 1e-9)

canvas.end_edit()
check("end_edit clears the session", not canvas.has_edit())

print("\n" + ("ALL EDITOR CHECKS PASSED" if ok else "EDITOR CHECKS FAILED"))
sys.exit(0 if ok else 1)
